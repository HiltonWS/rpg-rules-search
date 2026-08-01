import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

from rpg_rules_search.database import (
    connect,
    get_image_asset,
    image_source_states,
    initialize,
    load_page_texts,
    remove_missing_image_source_files,
    replace_image_tags,
    replace_pages,
    search,
    search_images,
    search_threats,
    set_document_content_hash,
    set_document_status,
    upsert_document,
    upsert_image_asset,
    upsert_image_source_file,
)
from rpg_rules_search.portable_export import export_portable_dataset


def test_connection_can_cross_fastapi_worker_threads(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(connection.execute, "SELECT 1").result()

    assert result.fetchone()[0] == 1
    connection.close()


def test_search_returns_book_and_page_reference(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="drive-book-1",
        name="Manual do Aventureiro.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )
    replace_pages(
        connection,
        document_id,
        [
            (0, None, "Capa"),
            (12, "10", "Ataque flamejante causa dois dados de dano ao alvo."),
        ],
    )

    results = search(connection, '"ataque flamejante"')

    assert len(results) == 1
    assert results[0].document_name == "Manual do Aventureiro.pdf"
    assert results[0].page_index == 12
    assert results[0].printed_page == "10"
    assert "<mark>Ataque flamejante</mark>" in results[0].snippet


def test_search_joins_word_hyphenated_across_line_break(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="hyphenated-book",
        name="Alto Mar.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-30T18:48:01Z",
    )
    replace_pages(
        connection,
        document_id,
        [(0, None, "Criaturas vivem nas profun-\ndezas do oceano e usam guarda-chuva.")],
    )

    results = search(connection, "profundezas")

    assert len(results) == 1
    assert "<mark>profundezas</mark>" in results[0].snippet


def test_search_removes_soft_hyphens_and_loads_full_page_evidence(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="soft-hyphen-book",
        name="Alto Mar.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-30T18:48:01Z",
    )
    full_text = "As profun\u00addezas representam a pressão e o desconhecido."
    replace_pages(connection, document_id, [(3, "2", full_text)])

    results = search(connection, "profundezas")
    page_texts = load_page_texts(connection, results)

    assert len(results) == 1
    assert page_texts[(document_id, 3)] == full_text


def test_search_corrects_single_letter_typo(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="conditions",
        name="Condições.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )
    replace_pages(
        connection,
        document_id,
        [(0, None, "Condição mental. Fascinado não pode fazer ações.")],
    )

    results = search(connection, "condicao facinado")

    assert len(results) == 1
    assert results[0].document_name == "Condições.pdf"


def test_search_falls_back_to_partial_matches(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="reference",
        name="Referências.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )
    replace_pages(
        connection,
        document_id,
        [
            (0, None, "Esta fonte contém informações gerais."),
            (1, None, "Nenhum registro confiável existe após esse momento."),
            (2, None, "Uma conexão forte existe entre os eventos."),
        ],
    )

    results = search(connection, "fonte confiável")

    assert len(results) == 2
    assert {result.page_index for result in results} == {0, 1}


def test_search_fallback_ignores_fts_boolean_operators(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="operator-fallback",
        name="Referências.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )
    replace_pages(
        connection,
        document_id,
        [(0, None, "Profundezas representa pressão e desconhecido.")],
    )

    results = search(connection, '"explique" AND "profundezas"')

    assert len(results) == 1
    assert results[0].page_index == 0


def test_search_threats_filters_category_and_keeps_page_reference(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="drive-threats",
        name="Ameacas.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )
    replace_pages(
        connection,
        document_id,
        [(7, "6", "Existido de Sangue. Defesa 18. Pontos de Vida 120.")],
        threat_metadata={7: "Ameaça da Realidade"},
    )

    results = search_threats(connection, query="Existido", category="Ameaça da Realidade")

    assert len(results) == 1
    assert results[0].document_name == "Ameacas.pdf"
    assert results[0].page_index == 7
    assert results[0].printed_page == "6"
    assert results[0].threat_category == "Ameaça da Realidade"


def test_image_asset_deduplicates_by_hash_and_refreshes_name(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)

    first_id, first_created = upsert_image_asset(
        connection,
        file_name="token-antigo.png",
        content_type="image/png",
        content_hash="same-hash",
        storage_path="/tmp/token-1.png",
        width=128,
        height=128,
    )
    second_id, second_created = upsert_image_asset(
        connection,
        file_name="token-novo.png",
        content_type="image/png",
        content_hash="same-hash",
        storage_path="/tmp/token-2.png",
        width=128,
        height=128,
    )

    assert first_created is True
    assert second_created is False
    assert first_id == second_id
    assert get_image_asset(connection, first_id)["file_name"] == "token-novo.png"  # type: ignore[index]


def test_image_search_returns_tagged_results(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    image_id, _ = upsert_image_asset(
        connection,
        file_name="espada.png",
        content_type="image/png",
        content_hash="hash-espada",
        storage_path="/tmp/espada.png",
        width=256,
        height=256,
    )
    replace_image_tags(connection, image_id, ["arma", "espada pesada"], source="manual")

    results = search_images(connection, '"espada"')

    assert len(results) == 1
    assert results[0].file_name == "espada.png"
    assert results[0].tags == ["arma", "espada pesada"]


def test_image_source_file_tracking_updates_and_removes_missing_ids(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    image_id, _ = upsert_image_asset(
        connection,
        file_name="token.png",
        content_type="image/png",
        content_hash="hash-token",
        storage_path="/tmp/token.png",
        width=64,
        height=64,
    )

    upsert_image_source_file(
        connection,
        source_file_id="local:/tmp/token.png",
        image_id=image_id,
        file_name="token.png",
        modified_time="1",
        content_hash="hash-token",
    )
    upsert_image_source_file(
        connection,
        source_file_id="local:/tmp/token.png",
        image_id=image_id,
        file_name="token-v2.png",
        modified_time="2",
        content_hash="hash-token",
    )

    states = image_source_states(connection)
    assert len(states) == 1
    assert states[0].file_name == "token-v2.png"
    assert states[0].modified_time == "2"

    removed = remove_missing_image_source_files(connection, set())

    assert removed == ["local:/tmp/token.png"]
    assert image_source_states(connection) == []


def test_portable_export_contains_pages_citations_tags_and_image_files(tmp_path: Path) -> None:
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="portable-book",
        name="Rituais.pdf",
        mime_type="application/pdf",
        modified_time="2026-08-01T12:00:00Z",
    )
    set_document_content_hash(connection, document_id, "book-hash")
    replace_pages(connection, document_id, [(4, "3", "Ritual de energia causa dano.")])
    set_document_status(connection, document_id, "ready")
    image_path = tmp_path / "ritual.png"
    image_path.write_bytes(b"image-data")
    image_id, _ = upsert_image_asset(
        connection,
        file_name="ritual.png",
        content_type="image/png",
        content_hash="image-hash",
        storage_path=str(image_path),
        width=320,
        height=240,
    )
    replace_image_tags(connection, image_id, ["ritual", "energia"], source="ai")

    export_path = export_portable_dataset(connection, tmp_path / "arquivo-arcano.zip")

    with ZipFile(export_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        page = json.loads(archive.read("pages.jsonl"))
        image = json.loads(archive.read("images.jsonl"))
        assert archive.read("images/image-hash.png") == b"image-data"
    assert manifest == {
        "format": "arquivo-arcano",
        "version": 1,
        "pages": 1,
        "images": 1,
        "image_files": 1,
    }
    assert page["citation"] == "[Rituais.pdf, p. 3]"
    assert page["raw_text"] == "Ritual de energia causa dano."
    assert image["archive_path"] == "images/image-hash.png"
    assert image["tags"] == [
        {"tag": "energia", "source": "ai"},
        {"tag": "ritual", "source": "ai"},
    ]
