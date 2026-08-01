from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EXPORT_FORMAT = "arquivo-arcano"
EXPORT_VERSION = 1


def _json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def export_portable_dataset(connection: sqlite3.Connection, destination: Path) -> Path:
    page_rows = connection.execute(
        """
        SELECT
            documents.name AS document_name,
            documents.mime_type,
            documents.content_hash,
            pages.page_index,
            pages.printed_page,
            pages.threat_category,
            pages.raw_text,
            pages.normalized_text
        FROM pages
        JOIN documents ON documents.id = pages.document_id
        WHERE documents.status = 'ready'
        ORDER BY documents.name, documents.id, pages.page_index
        """
    ).fetchall()
    image_rows = connection.execute(
        """
        SELECT id, file_name, content_type, content_hash, storage_path, width, height
        FROM image_assets
        WHERE status = 'ready'
        ORDER BY file_name, id
        """
    ).fetchall()
    tag_rows = connection.execute(
        """
        SELECT image_id, tag, source, confidence
        FROM image_tags
        ORDER BY image_id, tag
        """
    ).fetchall()
    tags_by_image: dict[int, list[dict[str, object]]] = {}
    for row in tag_rows:
        tag = {"tag": str(row["tag"]), "source": str(row["source"])}
        if row["confidence"] is not None:
            tag["confidence"] = float(row["confidence"])
        tags_by_image.setdefault(int(row["image_id"]), []).append(tag)

    destination.parent.mkdir(parents=True, exist_ok=True)
    exported_images = 0
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        pages = "".join(
            _json_line(
                {
                    "type": "page",
                    "document": {
                        "name": str(row["document_name"]),
                        "mime_type": str(row["mime_type"]),
                        "sha256": row["content_hash"],
                    },
                    "page_index": int(row["page_index"]),
                    "printed_page": row["printed_page"],
                    "citation": (
                        f'[{row["document_name"]}, p. '
                        f'{row["printed_page"] or int(row["page_index"]) + 1}]'
                    ),
                    "threat_category": row["threat_category"],
                    "raw_text": str(row["raw_text"]),
                    "normalized_text": str(row["normalized_text"]),
                }
            )
            for row in page_rows
        )
        archive.writestr("pages.jsonl", pages)

        images: list[str] = []
        for row in image_rows:
            storage_path = Path(str(row["storage_path"]))
            archive_path = None
            if storage_path.is_file():
                suffix = storage_path.suffix.lower()
                archive_path = f'images/{row["content_hash"]}{suffix}'
                archive.write(storage_path, archive_path)
                exported_images += 1
            images.append(
                _json_line(
                    {
                        "type": "image",
                        "file_name": str(row["file_name"]),
                        "content_type": str(row["content_type"]),
                        "sha256": str(row["content_hash"]),
                        "width": row["width"],
                        "height": row["height"],
                        "archive_path": archive_path,
                        "tags": tags_by_image.get(int(row["id"]), []),
                    }
                )
            )
        archive.writestr("images.jsonl", "".join(images))
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": EXPORT_FORMAT,
                    "version": EXPORT_VERSION,
                    "pages": len(page_rows),
                    "images": len(image_rows),
                    "image_files": exported_images,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return destination