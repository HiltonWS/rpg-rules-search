from __future__ import annotations

import shutil
from pathlib import Path

from rpg_rules_search.drive import DOCX_MIME_TYPE, PDF_MIME_TYPE, DriveItem

_MIME_TYPES = {
    ".docx": DOCX_MIME_TYPE,
    ".pdf": PDF_MIME_TYPE,
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class LocalFolderGateway:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def list_children(self, folder_id: str) -> list[DriveItem]:
        if folder_id != str(self.root):
            return []
        items: list[DriveItem] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in _MIME_TYPES:
                continue
            resolved_path = path.resolve()
            stat = resolved_path.stat()
            items.append(
                DriveItem(
                    id=f"local:{resolved_path}",
                    name=resolved_path.name,
                    mime_type=_MIME_TYPES[path.suffix.casefold()],
                    modified_time=f"{stat.st_mtime_ns}:{stat.st_size}",
                )
            )
        return sorted(items, key=lambda item: (item.name.casefold(), item.id))

    def download_file(self, file_id: str, destination: Path) -> None:
        source = self._path_from_id(file_id)
        shutil.copyfile(source, destination)

    def export_file_as_pdf(self, file_id: str, destination: Path) -> None:
        raise ValueError("Documentos Google não existem em pastas locais")

    def _path_from_id(self, file_id: str) -> Path:
        if not file_id.startswith("local:"):
            raise ValueError("Arquivo local inválido")
        path = Path(file_id.removeprefix("local:")).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Arquivo fora da pasta local configurada")
        return path