from pathlib import Path

from fastapi.testclient import TestClient

from rpg_rules_search.app import create_app
from rpg_rules_search.database import connect, replace_pages, upsert_document
from rpg_rules_search.drive import PDF_MIME_TYPE


class OptimizingOllama:
    model = "test-model"

    def __init__(self) -> None:
        self.suggestion_calls = 0

    def suggest_retrieval_terms(self, _query: str) -> list[str]:
        self.suggestion_calls += 1
        return ["restauração"]


def test_search_expansion_runs_in_background_and_reuses_cache(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    ollama = OptimizingOllama()
    app = create_app(
        database_path,
        ollama_client=ollama,  # type: ignore[arg-type]
        enable_periodic_sync=False,
    )

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="book-optimized",
            name="Manual.pdf",
            mime_type=PDF_MIME_TYPE,
            modified_time="2026-08-02T12:00:00Z",
        )
        replace_pages(connection, document_id, [(0, "1", "Restauração mágica recupera vida.")])
        connection.close()

        first = client.get("/api/search", params={"q": "cura"})
        second = client.get("/api/search", params={"q": "cura"})

    assert first.status_code == 200
    assert first.json()["results"] == []
    assert second.status_code == 200
    assert second.json()["results"][0]["document_name"] == "Manual.pdf"
    assert ollama.suggestion_calls == 1