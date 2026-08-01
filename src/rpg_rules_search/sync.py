from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from rpg_rules_search.database import (
    INDEX_VERSION,
    delete_image_asset_if_unreferenced,
    document_sync_states,
    get_image_asset,
    image_source_states,
    mark_document_duplicate,
    ready_document_with_hash,
    remove_missing_drive_documents,
    remove_missing_image_source_files,
    replace_image_tags,
    set_document_content_hash,
    set_document_status,
    upsert_document,
    upsert_image_asset,
    upsert_image_source_file,
)
from rpg_rules_search.drive import (
    DOCX_MIME_TYPE,
    GOOGLE_DOCS_MIME_TYPE,
    DriveGateway,
    discover_supported_files,
)
from rpg_rules_search.ingestion.docx import convert_docx_to_pdf
from rpg_rules_search.ingestion.service import ingest_pdf

LOGGER = logging.getLogger(__name__)


class DownloadGateway(DriveGateway, Protocol):
    def download_file(self, file_id: str, destination: Path) -> None: ...

    def export_file_as_pdf(self, file_id: str, destination: Path) -> None: ...


@dataclass(frozen=True)
class SyncError:
    file_name: str
    message: str


@dataclass
class SyncReport:
    discovered: int = 0
    ingested: int = 0
    unchanged: int = 0
    duplicates: int = 0
    removed: int = 0
    errors: list[SyncError] = field(default_factory=list)


DocxConverter = Callable[[Path, Path], None]
ProgressCallback = Callable[[SyncReport, str | None], None]
ImageTagger = Callable[[bytes, str], list[str]]


def _cache_stem(file_id: str) -> str:
    if file_id.startswith("local:"):
        return f"local-{sha256(file_id.encode('utf-8')).hexdigest()}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", file_id)


def _content_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cached_pdf_path(cache_dir: Path, file_id: str, mime_type: str) -> Path:
    suffix = ".converted.pdf" if mime_type == DOCX_MIME_TYPE else ".pdf"
    return cache_dir / f"{_cache_stem(file_id)}{suffix}"


def _is_image_mime_type(mime_type: str) -> bool:
    return mime_type.startswith("image/")


def _image_suffix(file_name: str, mime_type: str) -> str:
    lowered = file_name.casefold()
    if lowered.endswith((".jpg", ".jpeg")):
        return ".jpg"
    if lowered.endswith(".webp"):
        return ".webp"
    if lowered.endswith(".gif"):
        return ".gif"
    if lowered.endswith(".png"):
        return ".png"
    if mime_type == "image/jpeg":
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "image/gif":
        return ".gif"
    return ".png"


def _filename_tags(file_name: str) -> list[str]:
    stem = Path(file_name).stem
    tokens = re.findall(r"[\wÀ-ÿ]+", stem, flags=re.UNICODE)
    tags = [token.casefold() for token in tokens if len(token) >= 2]
    return list(dict.fromkeys(tags))[:12]


def sync_library(
    connection: sqlite3.Connection,
    gateway: DownloadGateway,
    root_folder_id: str,
    cache_dir: Path,
    *,
    docx_converter: DocxConverter = convert_docx_to_pdf,
    progress_callback: ProgressCallback | None = None,
    image_tagger: ImageTagger | None = None,
) -> SyncReport:
    items = discover_supported_files(gateway, root_folder_id)
    report = SyncReport(discovered=len(items))
    if progress_callback is not None:
        progress_callback(report, None)
    cache_dir.mkdir(parents=True, exist_ok=True)

    remote_ids = {item.id for item in items}
    image_remote_ids = {item.id for item in items if _is_image_mime_type(item.mime_type)}
    image_states = {state.source_file_id: state for state in image_source_states(connection)}
    removed_ids = remove_missing_drive_documents(connection, remote_ids)
    removed_image_ids = remove_missing_image_source_files(connection, image_remote_ids)
    report.removed = len(removed_ids) + len(removed_image_ids)
    for file_id in removed_ids:
        for cached_path in cache_dir.glob(f"{_cache_stem(file_id)}.*"):
            cached_path.unlink(missing_ok=True)

    image_cache_dir = (cache_dir / "images").resolve()
    for file_id in removed_image_ids:
        state = image_states[file_id]
        row = get_image_asset(connection, state.image_id)
        if row is None:
            continue
        storage_path = Path(str(row["storage_path"])).resolve()
        if storage_path.is_relative_to(image_cache_dir) and delete_image_asset_if_unreferenced(
            connection, state.image_id
        ):
            storage_path.unlink(missing_ok=True)

    states = {state.drive_file_id: state for state in document_sync_states(connection)}
    image_tagger_available = image_tagger is not None
    for item in items:
        if progress_callback is not None:
            progress_callback(report, item.name)

        if _is_image_mime_type(item.mime_type):
            modified_time = item.modified_time or "unknown"
            image_state = image_states.get(item.id)
            if image_state and image_state.modified_time == modified_time:
                report.unchanged += 1
                if progress_callback is not None:
                    progress_callback(report, item.name)
                continue

            image_cache_dir = cache_dir / "images"
            image_cache_dir.mkdir(parents=True, exist_ok=True)
            suffix = _image_suffix(item.name, item.mime_type)
            temporary_path = image_cache_dir / f"{_cache_stem(item.id)}{suffix}.download"
            try:
                gateway.download_file(item.id, temporary_path)
                content_hash = _content_hash(temporary_path)
                storage_path = image_cache_dir / f"{content_hash}{suffix}"
                image_id, created = upsert_image_asset(
                    connection,
                    file_name=item.name,
                    content_type=item.mime_type,
                    content_hash=content_hash,
                    storage_path=str(storage_path),
                    width=None,
                    height=None,
                )
                if created or not storage_path.exists():
                    os.replace(temporary_path, storage_path)
                else:
                    temporary_path.unlink(missing_ok=True)
                upsert_image_source_file(
                    connection,
                    source_file_id=item.id,
                    image_id=image_id,
                    file_name=item.name,
                    modified_time=modified_time,
                    content_hash=content_hash,
                )
                if created:
                    tags = _filename_tags(item.name)
                    if image_tagger is not None and image_tagger_available:
                        try:
                            tags.extend(image_tagger(storage_path.read_bytes(), item.name))
                        except Exception as error:  # noqa: BLE001
                            LOGGER.warning("Falha ao gerar tags para %s: %s", item.name, error)
                            image_tagger_available = False
                    replace_image_tags(connection, image_id, tags, source="source")
                report.ingested += 1
            except Exception as error:  # noqa: BLE001
                message = str(error) or error.__class__.__name__
                report.errors.append(SyncError(file_name=item.name, message=message))
                temporary_path.unlink(missing_ok=True)
            if progress_callback is not None:
                progress_callback(report, item.name)
            continue

        state = states.get(item.id)
        modified_time = item.modified_time or "unknown"
        if (
            state
            and state.modified_time == modified_time
            and state.status == "ready"
            and state.index_version == INDEX_VERSION
        ):
            report.unchanged += 1
            if progress_callback is not None:
                progress_callback(report, item.name)
            continue

        extension = ".docx" if item.mime_type == DOCX_MIME_TYPE else ".pdf"
        cache_path = cache_dir / f"{_cache_stem(item.id)}{extension}"
        temporary_path = cache_path.with_suffix(f"{extension}.download")
        converted_path = cached_pdf_path(cache_dir, item.id, DOCX_MIME_TYPE)
        completed = False
        document_id = upsert_document(
            connection,
            drive_file_id=item.id,
            name=item.name,
            mime_type=item.mime_type,
            modified_time=modified_time,
        )
        set_document_status(connection, document_id, "processing")

        try:
            if item.mime_type == GOOGLE_DOCS_MIME_TYPE:
                gateway.export_file_as_pdf(item.id, temporary_path)
            else:
                gateway.download_file(item.id, temporary_path)
            content_hash = _content_hash(temporary_path)
            if ready_document_with_hash(
                connection,
                content_hash,
                excluding_document_id=document_id,
            ) is not None:
                mark_document_duplicate(connection, document_id, content_hash)
                report.duplicates += 1
                completed = True
                continue
            ingestion_path = temporary_path
            if item.mime_type == DOCX_MIME_TYPE:
                docx_converter(temporary_path, converted_path)
                ingestion_path = converted_path
            ingest_pdf(connection, document_id, ingestion_path)
            set_document_content_hash(connection, document_id, content_hash)
            os.replace(temporary_path, cache_path)
            report.ingested += 1
            completed = True
        except Exception as error:  # noqa: BLE001
            message = str(error) or error.__class__.__name__
            set_document_status(connection, document_id, "error", message)
            report.errors.append(SyncError(file_name=item.name, message=message))
        finally:
            temporary_path.unlink(missing_ok=True)
            if not completed:
                converted_path.unlink(missing_ok=True)
            if progress_callback is not None:
                progress_callback(report, item.name)

    return report