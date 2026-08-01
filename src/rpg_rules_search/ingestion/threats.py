from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

REALITY_THREAT = "Ameaça da Realidade"

STAT_MARKERS = (
    re.compile(r"\bdefesa\s+\d+"),
    re.compile(r"\bpontos? de vida\b|\bpv\s+\d+"),
    re.compile(r"\bagi\b.*\bfor\b.*\bint\b.*\bpre\b.*\bvig\b"),
    re.compile(r"\bresistencias?\b"),
    re.compile(r"\bvulnerabilidades?\b"),
    re.compile(r"\bacoes?\b|\bagredir\b"),
    re.compile(r"\bpresenca perturbadora\b"),
    re.compile(r"\bdeslocamento\b"),
    re.compile(r"\bpericias?\b"),
)


@dataclass(frozen=True)
class ThreatClassification:
    category: str


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def classify_threat(text: str) -> ThreatClassification | None:
    normalized = " ".join(_normalize(text).split())
    marker_count = sum(bool(marker.search(normalized)) for marker in STAT_MARKERS)
    if marker_count < 3:
        return None

    if re.search(r"\bameacas? da realidade\b", normalized):
        return ThreatClassification(category=REALITY_THREAT)
    return ThreatClassification(category="Ameaça")