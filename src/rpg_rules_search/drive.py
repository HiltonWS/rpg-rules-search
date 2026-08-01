from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlparse

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
PNG_MIME_TYPE = "image/png"
JPEG_MIME_TYPE = "image/jpeg"
WEBP_MIME_TYPE = "image/webp"
GIF_MIME_TYPE = "image/gif"
SUPPORTED_MIME_TYPES = frozenset(
    {
        PDF_MIME_TYPE,
        DOCX_MIME_TYPE,
        GOOGLE_DOCS_MIME_TYPE,
        PNG_MIME_TYPE,
        JPEG_MIME_TYPE,
        WEBP_MIME_TYPE,
        GIF_MIME_TYPE,
    }
)
DRIVE_HOSTS = frozenset({"drive.google.com", "www.drive.google.com"})


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    trashed: bool = False
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None


class DriveGateway(Protocol):
    def list_children(self, folder_id: str) -> list[DriveItem]: ...


def folder_id_from_url(folder_url: str) -> str:
    parsed = urlparse(folder_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in DRIVE_HOSTS:
        raise ValueError("Informe um link válido de pasta do Google Drive")

    path_parts = [part for part in parsed.path.split("/") if part]
    if "folders" in path_parts:
        folder_index = path_parts.index("folders") + 1
        if folder_index < len(path_parts) and path_parts[folder_index]:
            return path_parts[folder_index]

    query_id = parse_qs(parsed.query).get("id", [])
    if query_id and query_id[0]:
        return query_id[0]
    raise ValueError("Informe um link válido de pasta do Google Drive")


def discover_supported_files(gateway: DriveGateway, root_folder_id: str) -> list[DriveItem]:
    """Return supported files below one selected folder, including nested folders."""
    folders = deque([root_folder_id])
    visited_folders = {root_folder_id}
    files: list[DriveItem] = []

    while folders:
        folder_id = folders.popleft()
        for item in gateway.list_children(folder_id):
            if item.trashed:
                continue
            if item.mime_type == SHORTCUT_MIME_TYPE:
                if not item.shortcut_target_id or not item.shortcut_target_mime_type:
                    continue
                item = DriveItem(
                    id=item.shortcut_target_id,
                    name=item.name,
                    mime_type=item.shortcut_target_mime_type,
                    modified_time=item.modified_time,
                )
            if item.mime_type == FOLDER_MIME_TYPE:
                if item.id not in visited_folders:
                    visited_folders.add(item.id)
                    folders.append(item.id)
            elif item.mime_type in SUPPORTED_MIME_TYPES:
                files.append(item)

    return sorted(files, key=lambda item: (item.name.casefold(), item.id))
