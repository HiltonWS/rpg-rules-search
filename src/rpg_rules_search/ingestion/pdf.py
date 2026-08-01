from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class TextBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class ExtractedPage:
    page_index: int
    width: float
    height: float
    text: str
    blocks: tuple[TextBlock, ...]
    requires_ocr: bool


def extract_pdf(path: Path, minimum_text_characters: int = 8) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []

    with fitz.open(path) as document:
        for page_index, page in enumerate(document):
            blocks = tuple(
                TextBlock(
                    text=block[4].strip(),
                    x0=block[0],
                    y0=block[1],
                    x1=block[2],
                    y1=block[3],
                )
                for block in page.get_text("blocks")
                if block[4].strip()
            )
            text = "\n".join(block.text for block in blocks)
            useful_characters = sum(character.isalnum() for character in text)
            pages.append(
                ExtractedPage(
                    page_index=page_index,
                    width=page.rect.width,
                    height=page.rect.height,
                    text=text,
                    blocks=blocks,
                    requires_ocr=useful_characters < minimum_text_characters,
                )
            )

    return pages
