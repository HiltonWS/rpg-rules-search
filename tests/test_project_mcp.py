from pathlib import Path

import pytest

from rpg_rules_search.project_mcp import collect_project_context, consult_project_ollama


class RecordingClient:
    def __init__(self) -> None:
        self.prompt = ""

    def answer(self, prompt: str) -> str:
        self.prompt = prompt
        return "revisão local"


def test_consult_project_ollama_uses_only_requested_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    client = RecordingClient()

    answer = consult_project_ollama(
        "Revise este módulo", ["module.py"], root=tmp_path, client=client  # type: ignore[arg-type]
    )

    assert answer == "revisão local"
    assert "### module.py" in client.prompt
    assert "value = 1" in client.prompt


@pytest.mark.parametrize("path", ["../outside.py", "credencials.json", ".venv/secret.py"])
def test_project_context_rejects_paths_outside_safe_workspace(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        collect_project_context([path], tmp_path)