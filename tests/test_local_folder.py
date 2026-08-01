from pathlib import Path

import pytest

from rpg_rules_search.drive import DOCX_MIME_TYPE, PDF_MIME_TYPE
from rpg_rules_search.local_folder import LocalFolderGateway


def test_local_folder_lists_supported_files_recursively_and_copies_them(tmp_path: Path) -> None:
    library = tmp_path / "library"
    nested = library / "versao-1.1"
    nested.mkdir(parents=True)
    (library / "regras.pdf").write_bytes(b"pdf")
    (nested / "ameacas.docx").write_bytes(b"docx")
    (nested / "notas.txt").write_text("ignorado", encoding="utf-8")
    gateway = LocalFolderGateway(library)

    items = gateway.list_children(str(library.resolve()))

    assert [(item.name, item.mime_type) for item in items] == [
        ("ameacas.docx", DOCX_MIME_TYPE),
        ("regras.pdf", PDF_MIME_TYPE),
    ]
    destination = tmp_path / "copy.pdf"
    pdf_item = next(item for item in items if item.name == "regras.pdf")
    gateway.download_file(pdf_item.id, destination)
    assert destination.read_bytes() == b"pdf"


def test_local_folder_rejects_files_outside_configured_root(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"pdf")
    gateway = LocalFolderGateway(library)

    with pytest.raises(ValueError, match="fora da pasta"):
        gateway.download_file(f"local:{outside}", tmp_path / "copy.pdf")