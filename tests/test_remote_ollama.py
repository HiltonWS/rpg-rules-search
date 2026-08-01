from pathlib import Path

from rpg_rules_search import remote_ollama
from rpg_rules_search.config import OllamaSettings, load_ollama_settings, save_ollama_settings


def test_linux_install_downloads_official_script_before_execution(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    def downloader(url: str, destination: str) -> tuple[str, object]:
        assert url == "https://ollama.com/install.sh"
        Path(destination).write_text("#!/bin/sh\n", encoding="utf-8")
        return destination, object()

    def runner(command: list[str], **_kwargs: object) -> object:
        calls.append(command)
        return object()

    monkeypatch.setattr(remote_ollama.shutil, "which", lambda _name: None)

    remote_ollama.install_ollama(system="Linux", runner=runner, downloader=downloader)  # type: ignore[arg-type]

    assert calls[0][0] == "/bin/sh"
    assert calls[0][1].endswith("install-ollama.sh")


def test_configure_client_preserves_models_and_normalizes_remote_url(tmp_path: Path) -> None:
    save_ollama_settings(
        tmp_path / "ollama.json",
        OllamaSettings(text_model="qwen3:latest", vision_model="gemma3:4b", auto_pull=False),
    )

    settings = remote_ollama.configure_client("http://192.168.1.50:11434/", tmp_path)

    assert settings.base_url == "http://192.168.1.50:11434"
    assert settings.text_model == "qwen3:latest"
    assert settings.auto_pull is False
    assert load_ollama_settings(tmp_path / "ollama.json") == settings