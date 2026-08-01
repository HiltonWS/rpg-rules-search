from pathlib import Path

import fitz

from rpg_rules_search.database import connect, initialize, search, upsert_document
from rpg_rules_search.ingestion.pdf import extract_pdf
from rpg_rules_search.ingestion.service import ingest_pdf
from rpg_rules_search.ingestion.threats import classify_threat


def create_pdf(path: Path) -> None:
    document = fitz.open()
    text_page = document.new_page(width=400, height=600)
    text_page.insert_text((40, 80), "Poder flamejante: role 2d6 de dano.")
    document.new_page(width=400, height=600)
    document.save(path)
    document.close()


def test_extracts_text_coordinates_and_marks_blank_page_for_ocr(tmp_path: Path) -> None:
    pdf_path = tmp_path / "rules.pdf"
    create_pdf(pdf_path)

    pages = extract_pdf(pdf_path)

    assert len(pages) == 2
    assert "2d6" in pages[0].text
    assert pages[0].blocks[0].x0 > 0
    assert pages[0].requires_ocr is False
    assert pages[1].requires_ocr is True


def test_ingested_pdf_is_searchable_with_page_reference(tmp_path: Path) -> None:
    pdf_path = tmp_path / "rules.pdf"
    create_pdf(pdf_path)
    connection = connect(tmp_path / "library.sqlite3")
    initialize(connection)
    document_id = upsert_document(
        connection,
        drive_file_id="pdf-1",
        name="Regras.pdf",
        mime_type="application/pdf",
        modified_time="2026-07-25T12:00:00Z",
    )

    ingest_pdf(connection, document_id, pdf_path)
    results = search(connection, "2d6")

    assert len(results) == 1
    assert results[0].page_index == 0


def test_classifies_reality_threat_stat_block_without_matching_narrative() -> None:
    stat_block = """
    AMEAÇA DA REALIDADE
    Existido de Sangue
    Defesa 18
    Pontos de Vida 120
    AGI 3 FOR 4 INT 1 PRE 2 VIG 3
    Resistências balístico 5
    Ações Agredir
    """

    threat = classify_threat(stat_block)

    assert threat is not None
    assert threat.category == "Ameaça da Realidade"
    assert classify_threat("As ameaças daquela realidade mudaram a investigação.") is None
