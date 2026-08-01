import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import fitz
from fastapi.testclient import TestClient

import rpg_rules_search.app as app_module
import rpg_rules_search.__main__ as main_module
from rpg_rules_search.app import create_app
from rpg_rules_search.config import DriveSource, save_drive_source
from rpg_rules_search.database import (
    connect,
    replace_pages,
    set_document_status,
    upsert_document,
)
from rpg_rules_search.drive import PDF_MIME_TYPE, DriveItem
from rpg_rules_search.oauth import GoogleOAuth
from rpg_rules_search.ollama_runtime import OllamaModel, OllamaRuntime

PNG_DOT = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x02"
    b"\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeOllama:
    model = "test-model"

    def __init__(self) -> None:
        self.prompt = ""
        self.images: list[bytes] = []

    def answer(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        *,
        extended: bool = False,
    ) -> str:
        self.prompt = prompt
        self.images = images or []
        return "Role dois dados. [Bestiario.pdf, p. 3]"


class AuthorizedOAuth:
    authorized = True
    client_configured = True

    def load_credentials(self) -> object:
        return object()


class DownloadingDrive:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def list_children(self, folder_id: str) -> list[DriveItem]:
        return [
            DriveItem(
                id="synced-book",
                name="Sincronizado.pdf",
                mime_type=PDF_MIME_TYPE,
                modified_time="2026-07-25T12:00:00Z",
            )
        ]

    def download_file(self, file_id: str, destination: Path) -> None:
        destination.write_bytes(self.content)


class SyncingImageDrive:
    def __init__(self) -> None:
        self.list_calls = 0

    def list_children(self, folder_id: str) -> list[DriveItem]:
        assert folder_id == "selected"
        self.list_calls += 1
        if self.list_calls == 1:
            return [DriveItem("image-1", "Mapa Castelo.png", "image/png", "1")]
        return [
            DriveItem("image-1", "Mapa Castelo.png", "image/png", "1"),
            DriveItem("image-2", "Token Fogo.png", "image/png", "2"),
        ]

    def download_file(self, file_id: str, destination: Path) -> None:
        destination.write_bytes(PNG_DOT + file_id.encode("utf-8"))


class AvailableOllamaRuntime(OllamaRuntime):
    def is_available(self) -> bool:
        return True

    def installed_models(self) -> list[OllamaModel]:
        return [OllamaModel("qwen3:latest", 1, frozenset({"completion"}), 1)]


def test_index_includes_live_sync_progress(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.sqlite3", enable_periodic_sync=False)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="sync-progress"' in response.text
    assert 'id="sync-current-file"' in response.text
    assert "window.setInterval(refreshSyncStatus, 1000)" in response.text
    assert 'id="page-preview-canvas"' in response.text
    assert 'id="page-preview-text"' in response.text
    assert "pdf.min.mjs" in response.text
    assert 'id="page-preview-pdf" class="pdf-page"' in response.text
    assert 'id="zoom-out"' in response.text
    assert 'id="zoom-in"' in response.text
    assert 'id="zoom-fit"' in response.text
    assert 'data-mode="teach"' not in response.text
    assert 'id="local-folder-form"' in response.text
    assert "const ruleTaxonomy" in response.text
    assert "function appendTaxonomyText" in response.text
    assert "rule-term rule-term-${kind}" in response.text
    assert "appendTaxonomyText(answerText, data.answer)" in response.text
    assert "/static/app.css?v=20260801-9" in response.text
    assert 'id="image-dialog"' in response.text
    assert 'id="download-image"' in response.text
    assert 'id="image-file" name="file" type="file"' in response.text
    assert 'id="image-file" name="file" type="file" accept="image/png,image/jpeg,image/webp,image/gif" required' not in response.text
    assert 'id="ollama-form"' in response.text
    assert 'id="ollama-auto-pull"' in response.text
    assert "await Promise.all([refreshSource(), refreshOllama()])" in response.text
    assert 'id="popular"' in response.text
    assert "Aprofundar resposta" in response.text


def test_profile_page_is_available_from_index(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.sqlite3", enable_periodic_sync=False)

    with TestClient(app) as client:
        index_response = client.get("/")
        profile_response = client.get("/profile")

    assert index_response.status_code == 200
    assert 'href="/profile"' in index_response.text
    assert profile_response.status_code == 200
    assert "Meu perfil" in profile_response.text
    assert 'id="profile-form"' in profile_response.text
    assert 'id="profile-name"' in profile_response.text
    assert "localStorage" in profile_response.text


def test_main_entrypoint_uses_environment_configuration(monkeypatch: object) -> None:
    monkeypatch.setenv("RPG_RULES_HOST", "0.0.0.0")
    monkeypatch.setenv("RPG_RULES_PORT", "9000")
    monkeypatch.setenv("RPG_RULES_RELOAD", "0")

    called: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> None:
        called["args"] = args
        called["kwargs"] = kwargs

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main_module.main()

    assert called["kwargs"]["host"] == "0.0.0.0"
    assert called["kwargs"]["port"] == 9000
    assert called["kwargs"]["reload"] is False


def test_api_rate_limit_returns_429(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "app.sqlite3",
        enable_periodic_sync=False,
        rate_limit_max_requests=2,
        rate_limit_window_seconds=60,
    )

    with TestClient(app) as client:
        first = client.get("/api/health")
        second = client.get("/api/health")
        third = client.get("/api/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json() == {"detail": "Muitas requisições; tente novamente em instantes."}
    assert third.headers["Retry-After"] == "60"


def test_rate_limit_does_not_block_non_api_routes(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "app.sqlite3",
        enable_periodic_sync=False,
        rate_limit_max_requests=1,
        rate_limit_window_seconds=60,
    )

    with TestClient(app) as client:
        first = client.get("/")
        second = client.get("/profile")

    assert first.status_code == 200
    assert second.status_code == 200


def test_search_api_returns_cited_result(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    app = create_app(database_path)

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="book-1",
            name="Bestiario.pdf",
            mime_type="application/pdf",
            modified_time="2026-07-25T12:00:00Z",
        )
        replace_pages(connection, document_id, [(4, "3", "O dragão rola dois dados de fogo.")])
        connection.close()

        response = client.get("/api/search", params={"q": "dragão"})

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["document_name"] == "Bestiario.pdf"
    assert result["printed_page"] == "3"


def test_popular_api_counts_searches_and_questions(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    ollama = FakeOllama()
    app = create_app(database_path, ollama_client=ollama)  # type: ignore[arg-type]

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="popular-book",
            name="Manual.pdf",
            mime_type="application/pdf",
            modified_time="2026-07-25T12:00:00Z",
        )
        replace_pages(connection, document_id, [(0, "1", "Ataque flamejante causa dano.")])
        connection.close()
        client.get("/api/search", params={"q": "ataque flamejante"})
        client.get("/api/search", params={"q": "Ataque Flamejante"})
        client.get("/api/ask", params={"q": "Como funciona ataque flamejante?"})
        response = client.get("/api/popular")

    assert response.status_code == 200
    assert response.json()["searches"][0]["count"] == 2
    assert response.json()["questions"][0]["query"] == "Como funciona ataque flamejante?"


def test_portable_export_api_downloads_ai_neutral_dataset(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    app = create_app(database_path, enable_periodic_sync=False)

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="export-book",
            name="Manual.pdf",
            mime_type="application/pdf",
            modified_time="2026-08-01T12:00:00Z",
        )
        replace_pages(connection, document_id, [(1, "1", "Regra portátil.")])
        set_document_status(connection, document_id, "ready")
        connection.close()

        response = client.get("/api/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "arquivo-arcano.zip" in response.headers["content-disposition"]
    with ZipFile(BytesIO(response.content)) as archive:
        page = json.loads(archive.read("pages.jsonl"))
    assert page["citation"] == "[Manual.pdf, p. 1]"
    assert page["raw_text"] == "Regra portátil."


def test_ollama_settings_api_persists_remote_host_and_reports_status(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    app = create_app(
        database_path,
        enable_periodic_sync=False,
        ollama_runtime_factory=AvailableOllamaRuntime,
    )

    with TestClient(app) as client:
        response = client.put(
            "/api/ollama",
            json={
                "base_url": "http://192.168.1.50:11434/",
                "text_model": "qwen3:latest",
                "vision_model": "gemma3:4b",
                "auto_pull": False,
            },
        )
        persisted = json.loads((tmp_path / "ollama.json").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert response.json() == {
        "base_url": "http://192.168.1.50:11434",
        "text_model": "qwen3:latest",
        "vision_model": "gemma3:4b",
        "auto_pull": False,
        "available": True,
        "is_local": False,
        "installed_models": ["qwen3:latest"],
        "image_auto_tag_cooldown_seconds": 300,
        "image_auto_tag_retry_in_seconds": 0,
    }
    assert persisted["base_url"] == "http://192.168.1.50:11434"

    restarted_app = create_app(
        database_path,
        enable_periodic_sync=False,
        ollama_runtime_factory=AvailableOllamaRuntime,
    )
    with TestClient(restarted_app) as client:
        restarted_status = client.get("/api/ollama")
    assert restarted_status.json()["base_url"] == "http://192.168.1.50:11434"
    assert restarted_status.json()["auto_pull"] is False


def test_invalid_fts_query_returns_readable_error(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.sqlite3")

    with TestClient(app) as client:
        response = client.get("/api/search", params={"q": '"consulta aberta'})

    assert response.status_code == 400
    assert response.json()["detail"] == "Consulta de busca inválida"


def test_source_status_reflects_persisted_drive_folder(tmp_path: Path) -> None:
    source_path = tmp_path / "source.json"
    app = create_app(tmp_path / "app.sqlite3", source_path=source_path)

    with TestClient(app) as client:
        assert client.get("/api/source").json() == {
            "configured": False,
            "source_type": None,
            "folder_name": None,
            "sync_interval_seconds": 60,
        }

        save_drive_source(
            source_path,
            DriveSource(
                folder_id="folder-123",
                folder_name="Biblioteca",
                sync_interval_seconds=120,
            ),
        )

        assert client.get("/api/source").json() == {
            "configured": True,
            "source_type": "drive",
            "folder_name": "Biblioteca",
            "sync_interval_seconds": 120,
        }


def test_local_folder_source_syncs_without_google_oauth(tmp_path: Path) -> None:
    library = tmp_path / "Livros"
    library.mkdir()
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 80), "Regra carregada automaticamente da pasta local.")
    document.save(library / "Manual 1.1.pdf")
    document.close()
    app = create_app(tmp_path / "app.sqlite3", enable_periodic_sync=False)

    with TestClient(app) as client:
        source_response = client.put(
            "/api/source/local",
            json={"folder_path": str(library)},
        )
        sync_response = client.post("/api/sync")
        search_response = client.get("/api/search", params={"q": "carregada"})

    assert source_response.json()["source_type"] == "local"
    assert sync_response.status_code == 200
    assert sync_response.json()["ingested"] == 1
    assert search_response.json()["results"][0]["document_name"] == "Manual 1.1.pdf"


def test_threat_api_lists_reality_threats_without_query(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    app = create_app(database_path)

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="threat-book",
            name="Ameacas.pdf",
            mime_type="application/pdf",
            modified_time="2026-07-25T12:00:00Z",
        )
        replace_pages(
            connection,
            document_id,
            [(9, "8", "Existido de Sangue. Defesa 18. Pontos de Vida 120.")],
            threat_metadata={9: "Ameaça da Realidade"},
        )
        connection.close()

        response = client.get(
            "/api/threats",
            params={"category": "Ameaça da Realidade"},
        )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["document_name"] == "Ameacas.pdf"
    assert result["page_index"] == 9
    assert result["threat_category"] == "Ameaça da Realidade"


def test_oauth_client_upload_is_saved_outside_database(tmp_path: Path) -> None:
    oauth = GoogleOAuth(tmp_path / "secrets" / "client.json", tmp_path / "secrets" / "token.json")
    app = create_app(tmp_path / "app.sqlite3", oauth=oauth)
    client_config = {
        "installed": {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/oauth/client",
            files={"file": ("credentials.json", json.dumps(client_config), "application/json")},
        )

    assert response.status_code == 200
    assert response.json() == {"client_configured": True, "authorized": False}
    assert oauth.client_secrets_path.is_file()
    assert oauth.client_secrets_path.stat().st_mode & 0o777 == 0o600


def test_ask_api_retrieves_local_evidence_before_ollama(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    ollama = FakeOllama()
    app = create_app(database_path, ollama_client=ollama)  # type: ignore[arg-type]

    with TestClient(app) as client:
        connection = connect(database_path)
        document_id = upsert_document(
            connection,
            drive_file_id="book-ask",
            name="Bestiario.pdf",
            mime_type="application/pdf",
            modified_time="2026-07-25T12:00:00Z",
        )
        replace_pages(connection, document_id, [(4, "3", "Ataque flamejante rola dois dados.")])
        connection.close()

        response = client.get("/api/ask", params={"q": "Como funciona ataque flamejante?"})

    assert response.status_code == 200
    assert response.json()["answer"] == "Role dois dados. [Bestiario.pdf, p. 3]"
    assert response.json()["sources"][0]["document_name"] == "Bestiario.pdf"
    assert "[Bestiario.pdf, p. 3]" in ollama.prompt


def test_image_library_upload_search_and_open_file(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.sqlite3")

    with TestClient(app) as client:
        upload = client.post(
            "/api/images?auto_tag=false",
            files={"file": ("token.png", PNG_DOT, "image/png")},
        )
        image_id = upload.json()["image_id"]
        tags = client.put(
            f"/api/images/{image_id}/tags",
            json={"tags": ["símbolo", "token"]},
        )
        search_response = client.get("/api/images", params={"q": "token"})
        file_response = client.get(f"/api/images/{image_id}/file")
        preview_response = client.get(f"/api/images/{image_id}/preview")

    assert upload.status_code == 200
    assert upload.json()["duplicate"] is False
    assert tags.status_code == 200
    assert tags.json()["tags"] == ["símbolo", "token"]
    assert search_response.status_code == 200
    assert search_response.json()["results"][0]["file_name"] == "token.png"
    assert search_response.json()["results"][0]["image_url"] == f"/api/images/{image_id}/preview"
    assert file_response.status_code == 200
    assert file_response.content.startswith(b"\x89PNG")
    assert file_response.headers["content-disposition"].startswith("attachment;")
    assert preview_response.status_code == 200
    assert preview_response.content.startswith(b"\x89PNG")
    assert "content-disposition" not in preview_response.headers


def test_image_library_marks_duplicate_uploads(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.sqlite3")

    with TestClient(app) as client:
        first = client.post(
            "/api/images?auto_tag=false",
            files={"file": ("token-a.png", PNG_DOT, "image/png")},
        )
        second = client.post(
            "/api/images?auto_tag=false",
            files={"file": ("token-b.png", PNG_DOT, "image/png")},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["image_id"] == second.json()["image_id"]
    assert second.json()["duplicate"] is True


def test_sync_api_downloads_and_indexes_selected_drive_folder(tmp_path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 80), "Regra sincronizada automaticamente do Google Drive.")
    pdf_bytes = document.tobytes()
    document.close()

    source_path = tmp_path / "source.json"
    save_drive_source(source_path, DriveSource(folder_id="selected", folder_name="Livros"))
    drive = DownloadingDrive(pdf_bytes)
    app = create_app(
        tmp_path / "app.sqlite3",
        source_path=source_path,
        oauth=AuthorizedOAuth(),  # type: ignore[arg-type]
        drive_gateway_factory=lambda _credentials: drive,  # type: ignore[arg-type,return-value]
        enable_periodic_sync=False,
    )

    with TestClient(app) as client:
        sync_response = client.post("/api/sync")
        search_response = client.get("/api/search", params={"q": "sincronizada"})
        document_id = search_response.json()["results"][0]["document_id"]
        file_response = client.get(f"/api/documents/{document_id}/file")
        preview_response = client.get(f"/api/documents/{document_id}/pages/0.pdf")

    assert sync_response.status_code == 200
    assert sync_response.json()["ingested"] == 1
    assert search_response.json()["results"][0]["document_name"] == "Sincronizado.pdf"
    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "application/pdf"
    assert file_response.content.startswith(b"%PDF")
    assert file_response.headers["content-disposition"].startswith("inline")
    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"] == "application/pdf"
    assert preview_response.content.startswith(b"%PDF")
    with fitz.open(stream=preview_response.content, filetype="pdf") as preview:
        assert preview.page_count == 1
        assert "Regra sincronizada" in preview[0].get_text()


def test_sync_api_cools_down_image_auto_tag_after_ollama_unavailable(tmp_path: Path) -> None:
    source_path = tmp_path / "source.json"
    save_drive_source(source_path, DriveSource(folder_id="selected", folder_name="Imagens"))
    drive = SyncingImageDrive()

    class UnavailableVisionOllama:
        model = "vision-test"

        def __init__(self) -> None:
            self.calls = 0

        def answer(
            self,
            prompt: str,
            images: list[bytes] | None = None,
            *,
            extended: bool = False,
        ) -> str:
            self.calls += 1
            raise app_module.OllamaUnavailableError("O Ollama local não respondeu")

    ollama = UnavailableVisionOllama()
    app = create_app(
        tmp_path / "app.sqlite3",
        source_path=source_path,
        oauth=AuthorizedOAuth(),  # type: ignore[arg-type]
        ollama_client=ollama,  # type: ignore[arg-type]
        drive_gateway_factory=lambda _credentials: drive,  # type: ignore[arg-type,return-value]
        enable_periodic_sync=False,
    )

    with TestClient(app) as client:
        first_sync = client.post("/api/sync")
        status_after_failure = client.get("/api/ollama")
        second_sync = client.post("/api/sync")
        image_search = client.get("/api/images", params={"q": "token"})

    assert first_sync.status_code == 200
    assert status_after_failure.status_code == 200
    assert second_sync.status_code == 200
    assert ollama.calls == 1
    assert second_sync.json()["ingested"] == 1
    assert status_after_failure.json()["image_auto_tag_retry_in_seconds"] > 0
    assert image_search.json()["results"][0]["file_name"] == "Token Fogo.png"


def test_ollama_status_reads_auto_tag_cooldown_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("RPG_RULES_OLLAMA_IMAGE_AUTO_TAG_COOLDOWN_SECONDS", "45")
    app = create_app(tmp_path / "app.sqlite3", enable_periodic_sync=False)

    with TestClient(app) as client:
        response = client.get("/api/ollama")

    assert response.status_code == 200
    assert response.json()["image_auto_tag_cooldown_seconds"] == 45
    assert response.json()["image_auto_tag_retry_in_seconds"] == 0


def test_app_startup_configures_text_and_vision_models(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, bool, bool]] = []

    class FakeRuntime:
        def __init__(self, base_url: str) -> None:
            assert base_url == "http://127.0.0.1:11434"

        def ensure_model(
            self,
            preferred_model: str,
            *,
            require_vision: bool = False,
            install_if_missing: bool = True,
        ) -> str:
            calls.append((preferred_model, require_vision, install_if_missing))
            return f"resolved:{preferred_model}"

    monkeypatch.setenv("RPG_RULES_OLLAMA_MODEL", "qwen3:latest")
    monkeypatch.setenv("RPG_RULES_OLLAMA_VISION_MODEL", "gemma3:4b")
    monkeypatch.setattr(app_module, "OllamaRuntime", FakeRuntime)

    app = create_app(
        tmp_path / "app.sqlite3",
        enable_periodic_sync=False,
        enable_ollama_runtime=True,
    )

    with TestClient(app):
        pass

    assert calls == [
        ("qwen3:latest", False, True),
        ("gemma3:4b", True, True),
    ]
    assert app.state.ollama.model == "resolved:qwen3:latest"
    assert app.state.vision_ollama.model == "resolved:gemma3:4b"


def test_app_startup_respects_auto_pull_env_flag(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, bool, bool]] = []

    class FakeRuntime:
        def __init__(self, _base_url: str) -> None:
            pass

        def ensure_model(
            self,
            preferred_model: str,
            *,
            require_vision: bool = False,
            install_if_missing: bool = True,
        ) -> str:
            calls.append((preferred_model, require_vision, install_if_missing))
            return preferred_model

    monkeypatch.setenv("RPG_RULES_OLLAMA_AUTO_PULL", "0")
    monkeypatch.setattr(app_module, "OllamaRuntime", FakeRuntime)

    app = create_app(
        tmp_path / "app.sqlite3",
        enable_periodic_sync=False,
        enable_ollama_runtime=True,
    )

    with TestClient(app):
        pass

    assert calls == [
        ("gemma3:latest", False, False),
        ("gemma3:4b", True, False),
    ]


def test_app_startup_continues_when_runtime_configuration_fails(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    class FailingRuntime:
        def __init__(self, _base_url: str) -> None:
            pass

        def ensure_model(
            self,
            _preferred_model: str,
            *,
            require_vision: bool = False,
            install_if_missing: bool = True,
        ) -> str:
            raise app_module.OllamaRuntimeError("falha simulada")

    monkeypatch.setattr(app_module, "OllamaRuntime", FailingRuntime)

    app = create_app(
        tmp_path / "app.sqlite3",
        enable_periodic_sync=False,
        enable_ollama_runtime=True,
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
