from __future__ import annotations

import sqlite3
from pathlib import Path

from rpg_rules_search.database import replace_pages
from rpg_rules_search.ingestion.pdf import ExtractedPage, extract_pdf
from rpg_rules_search.ingestion.threats import classify_threat


class OcrRequiredError(RuntimeError):
    def __init__(self, page_indexes: list[int]) -> None:
        self.page_indexes = page_indexes
        pages = ", ".join(str(index + 1) for index in page_indexes)
        super().__init__(f"OCR necessário nas páginas: {pages}")


def ingest_pdf(
    connection: sqlite3.Connection,
    document_id: int,
    path: Path,
    *,
    allow_partial: bool = True,
) -> list[ExtractedPage]:
    pages = extract_pdf(path)
    pages_requiring_ocr = [page.page_index for page in pages if page.requires_ocr]
    if pages_requiring_ocr and not allow_partial:
        raise OcrRequiredError(pages_requiring_ocr)

    threat_metadata = {
        page.page_index: classification.category
        for page in pages
        if (classification := classify_threat(page.text)) is not None
    }
    replace_pages(
        connection,
        document_id,
        [(page.page_index, None, page.text) for page in pages if page.text],
        threat_metadata=threat_metadata,
    )
    return pages
