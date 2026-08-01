from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DriveSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["drive", "local"] = "drive"
    folder_id: str = Field(min_length=1)
    folder_name: str = Field(min_length=1)
    sync_interval_seconds: int = Field(default=60, ge=15)


class OllamaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = "http://127.0.0.1:11434"
    text_model: str = Field(default="gemma3:latest", min_length=1)
    vision_model: str = Field(default="gemma3:4b", min_length=1)
    auto_pull: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("A URL do Ollama deve usar http:// ou https://")
        return normalized


def load_drive_source(path: Path) -> DriveSource | None:
    if not path.exists():
        return None
    return DriveSource.model_validate_json(path.read_text(encoding="utf-8"))


def save_drive_source(path: Path, source: DriveSource) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(source.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, path)


def load_ollama_settings(path: Path) -> OllamaSettings | None:
    if not path.exists():
        return None
    return OllamaSettings.model_validate_json(path.read_text(encoding="utf-8"))


def save_ollama_settings(path: Path, settings: OllamaSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, path)