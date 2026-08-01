from rpg_rules_search.drive import (
    DOCX_MIME_TYPE,
    FOLDER_MIME_TYPE,
    GOOGLE_DOCS_MIME_TYPE,
    PDF_MIME_TYPE,
    SHORTCUT_MIME_TYPE,
    DriveItem,
    discover_supported_files,
    folder_id_from_url,
)


class FakeDrive:
    def __init__(self, children: dict[str, list[DriveItem]]) -> None:
        self.children = children
        self.visited: list[str] = []

    def list_children(self, folder_id: str) -> list[DriveItem]:
        self.visited.append(folder_id)
        return self.children.get(folder_id, [])


def test_discovers_supported_documents_and_images_below_selected_folder() -> None:
    drive = FakeDrive(
        {
            "selected": [
                DriveItem("nested", "Sistema", FOLDER_MIME_TYPE),
                DriveItem("book", "Regras.pdf", PDF_MIME_TYPE),
                DriveItem("image", "Mapa.png", "image/png"),
                DriveItem("deleted", "Antigo.pdf", PDF_MIME_TYPE, trashed=True),
            ],
            "nested": [DriveItem("notes", "Poderes.docx", DOCX_MIME_TYPE)],
            "outside": [DriveItem("secret", "Fora.pdf", PDF_MIME_TYPE)],
        }
    )

    files = discover_supported_files(drive, "selected")

    assert [item.id for item in files] == ["image", "notes", "book"]
    assert drive.visited == ["selected", "nested"]
    assert "outside" not in drive.visited


def test_extracts_folder_id_from_google_drive_links() -> None:
    assert (
        folder_id_from_url("https://drive.google.com/drive/u/0/folders/folder-123?usp=sharing")
        == "folder-123"
    )
    assert folder_id_from_url("https://drive.google.com/open?id=folder-456") == "folder-456"


def test_rejects_non_drive_folder_link() -> None:
    try:
        folder_id_from_url("https://example.com/drive/folders/folder-123")
    except ValueError as error:
        assert str(error) == "Informe um link válido de pasta do Google Drive"
    else:
        raise AssertionError("link externo deveria ser rejeitado")


def test_discovers_supported_files_through_drive_shortcuts() -> None:
    drive = FakeDrive(
        {
            "selected": [
                DriveItem(
                    "folder-link",
                    "Livros",
                    SHORTCUT_MIME_TYPE,
                    shortcut_target_id="real-folder",
                    shortcut_target_mime_type=FOLDER_MIME_TYPE,
                ),
                DriveItem(
                    "book-link",
                    "Bestiario.pdf",
                    SHORTCUT_MIME_TYPE,
                    modified_time="2026-07-25T12:00:00Z",
                    shortcut_target_id="real-book",
                    shortcut_target_mime_type=PDF_MIME_TYPE,
                ),
                DriveItem(
                    "doc-link",
                    "Regras da casa",
                    SHORTCUT_MIME_TYPE,
                    modified_time="2026-07-30T18:48:01Z",
                    shortcut_target_id="real-doc",
                    shortcut_target_mime_type=GOOGLE_DOCS_MIME_TYPE,
                ),
            ],
            "real-folder": [DriveItem("nested-book", "Regras.pdf", PDF_MIME_TYPE)],
        }
    )

    files = discover_supported_files(drive, "selected")

    assert [item.id for item in files] == ["real-book", "real-doc", "nested-book"]
    assert drive.visited == ["selected", "real-folder"]
