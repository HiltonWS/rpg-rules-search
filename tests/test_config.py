import json
from pathlib import Path

from rpg_rules_search.config import (
    DriveSource,
    OllamaSettings,
    load_drive_source,
    load_ollama_settings,
    save_drive_source,
    save_ollama_settings,
)


def test_drive_source_round_trip_uses_atomic_json_file(tmp_path: Path) -> None:
    config_path = tmp_path / "source.json"
    source = DriveSource(folder_id="folder-123", folder_name="Meus livros")

    save_drive_source(config_path, source)

    assert load_drive_source(config_path) == source
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "source_type": "drive",
        "folder_id": "folder-123",
        "folder_name": "Meus livros",
        "sync_interval_seconds": 60,
    }
    assert not config_path.with_suffix(".tmp").exists()


def test_missing_drive_source_is_unconfigured(tmp_path: Path) -> None:
    assert load_drive_source(tmp_path / "source.json") is None


def test_ollama_settings_round_trip_normalizes_url(tmp_path: Path) -> None:
    config_path = tmp_path / "ollama.json"
    settings = OllamaSettings(
        base_url="http://192.168.1.50:11434/",
        text_model="qwen3:latest",
        vision_model="gemma3:4b",
        auto_pull=False,
    )

    save_ollama_settings(config_path, settings)

    assert load_ollama_settings(config_path) == settings
    assert settings.base_url == "http://192.168.1.50:11434"
    assert not config_path.with_suffix(".tmp").exists()