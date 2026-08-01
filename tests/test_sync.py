from pathlib import Path

import fitz

from rpg_rules_search.database import INDEX_VERSION, connect, initialize, search, search_images
from rpg_rules_search.drive import GOOGLE_DOCS_MIME_TYPE, PDF_MIME_TYPE, DriveItem
from rpg_rules_search.sync import cached_pdf_path, sync_library


class FakeDrive:
    def __init__(self, pdf_bytes: bytes) -> None:
        self.pdf_bytes = pdf_bytes
        self.items = [
            DriveItem(
                id="book-1",
                name="Bestiario.pdf",
                mime_type=PDF_MIME_TYPE,
                modified_time="2026-07-25T12:00:00Z",
            )
        ]
        self.downloads = 0

    def list_children(self, folder_id: str) -> list[DriveItem]:
        assert folder_id == "selected"
        return self.items

    def download_file(self, file_id: str, destination: Path) -> None:
        assert file_id == "book-1"
        self.downloads += 1
        destination.write_bytes(self.pdf_bytes)

    def export_file_as_pdf(self, file_id: str, destination: Path) -> None:
        destination.write_bytes(self.pdf_bytes)


def create_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 80), "A criatura possui defesa sobrenatural e ataque flamejante.")
    content = document.tobytes()
    document.close()
    return content


def create_png_dot() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x02"
        b"\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_local_cache_path_has_bounded_name_for_deep_folders(tmp_path: Path) -> None:
    file_id = "local:/" + "/muito-longo" * 100 + "/manual.pdf"

    path = cached_pdf_path(tmp_path, file_id, PDF_MIME_TYPE)

    assert path.name.startswith("local-")
    assert len(path.name) < 100


def test_sync_downloads_changed_files_skips_unchanged_and_removes_missing(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())

    first = sync_library(connection, drive, "selected", tmp_path / "cache")
    second = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert first.ingested == 1
    assert second.unchanged == 1
    assert drive.downloads == 1
    assert search(connection, "flamejante")[0].document_name == "Bestiario.pdf"

    drive.items = []
    third = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert third.removed == 1
    assert search(connection, "flamejante") == []
    assert not (tmp_path / "cache" / "book-1.pdf").exists()


def test_sync_reindexes_unchanged_file_when_index_version_is_old(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    sync_library(connection, drive, "selected", tmp_path / "cache")
    connection.execute("UPDATE documents SET index_version = ?", (INDEX_VERSION - 1,))
    connection.commit()

    report = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert report.ingested == 1
    assert drive.downloads == 2


def test_sync_reports_progress_while_processing_files(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    progress: list[tuple[int, int, str | None]] = []

    sync_library(
        connection,
        drive,
        "selected",
        tmp_path / "cache",
        progress_callback=lambda report, current_file: progress.append(
            (report.discovered, report.ingested + report.unchanged + len(report.errors), current_file)
        ),
    )

    assert progress[0] == (1, 0, None)
    assert progress[-1] == (1, 1, "Bestiario.pdf")


def test_sync_exports_and_indexes_native_google_document(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    drive.items = [
        DriveItem(
            id="native-doc",
            name="Regras da casa",
            mime_type=GOOGLE_DOCS_MIME_TYPE,
            modified_time="2026-07-30T18:48:01Z",
        )
    ]

    report = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert report.ingested == 1
    assert search(connection, "flamejante")[0].document_name == "Regras da casa"
    assert (tmp_path / "cache" / "native-doc.pdf").is_file()


def test_sync_indexes_identical_pdf_only_once_but_keeps_different_versions(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    original = create_pdf_bytes()
    newer_document = fitz.open()
    newer_page = newer_document.new_page()
    newer_page.insert_text((40, 80), "Conteúdo exclusivo da versão 1.1.")
    newer = newer_document.tobytes()
    newer_document.close()
    drive = FakeDrive(original)
    drive.items = [
        DriveItem("book-1", "AS 1.0.pdf", PDF_MIME_TYPE, "1"),
        DriveItem("book-copy", "Cópia AS 1.0.pdf", PDF_MIME_TYPE, "1"),
        DriveItem("book-1.1", "AS 1.1.pdf", PDF_MIME_TYPE, "1"),
    ]

    def download(file_id: str, destination: Path) -> None:
        destination.write_bytes(newer if file_id == "book-1.1" else original)

    drive.download_file = download  # type: ignore[method-assign]

    report = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert report.ingested == 2
    assert report.duplicates == 1
    rows = connection.execute(
        "SELECT name, status FROM documents ORDER BY name"
    ).fetchall()
    assert [(row["name"], row["status"]) for row in rows] == [
        ("AS 1.0.pdf", "ready"),
        ("AS 1.1.pdf", "ready"),
        ("Cópia AS 1.0.pdf", "duplicate"),
    ]


def test_sync_downloads_and_indexes_images_from_source_folder(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    image_bytes = create_png_dot()
    drive.items = [
        DriveItem("token-1", "Token Fogo.png", "image/png", "1"),
    ]

    def download(file_id: str, destination: Path) -> None:
        assert file_id == "token-1"
        destination.write_bytes(image_bytes)

    drive.download_file = download  # type: ignore[method-assign]

    first = sync_library(connection, drive, "selected", tmp_path / "cache")
    second = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert first.ingested == 1
    assert second.unchanged == 1
    results = search_images(connection, '"fogo"')
    assert len(results) == 1
    assert results[0].file_name == "Token Fogo.png"
    assert "fogo" in results[0].tags
    stored_path = Path(
        str(
            connection.execute(
                "SELECT storage_path FROM image_assets WHERE id = ?", (results[0].image_id,)
            ).fetchone()["storage_path"]
        )
    )

    drive.items = []
    third = sync_library(connection, drive, "selected", tmp_path / "cache")

    assert third.removed == 1
    assert search_images(connection) == []
    assert not stored_path.exists()


def test_sync_enriches_source_image_tags_with_local_ai(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    drive.items = [DriveItem("map-1", "Mapa Castelo.png", "image/png", "1")]
    drive.download_file = (  # type: ignore[method-assign]
        lambda _file_id, destination: destination.write_bytes(create_png_dot())
    )

    report = sync_library(
        connection,
        drive,
        "selected",
        tmp_path / "cache",
        image_tagger=lambda _content, _file_name: ["fortaleza", "fantasia"],
    )

    assert report.ingested == 1
    results = search_images(connection, '"fortaleza"')
    assert len(results) == 1
    assert set(results[0].tags) >= {"mapa", "castelo", "fortaleza", "fantasia"}


def test_sync_stops_ai_tag_attempts_after_first_failure(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    drive = FakeDrive(create_pdf_bytes())
    drive.items = [
        DriveItem("image-1", "Mapa Castelo.png", "image/png", "1"),
        DriveItem("image-2", "Token Fogo.png", "image/png", "1"),
    ]
    drive.download_file = (  # type: ignore[method-assign]
        lambda _file_id, destination: destination.write_bytes(
            create_png_dot() + destination.name.encode()
        )
    )
    attempts = 0

    def unavailable_tagger(_content: bytes, _file_name: str) -> list[str]:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("O Ollama local não respondeu")

    report = sync_library(
        connection,
        drive,
        "selected",
        tmp_path / "cache",
        image_tagger=unavailable_tagger,
    )

    assert report.ingested == 2
    assert attempts == 1
    assert len(search_images(connection)) == 2