from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import sqlite3
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

import fitz
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from rpg_rules_search.config import (
    DriveSource,
    OllamaSettings,
    load_drive_source,
    load_ollama_settings,
    save_drive_source,
    save_ollama_settings,
)
from rpg_rules_search.database import (
    ImageSearchResult,
    SearchResult,
    ThreatSearchResult,
    connect,
    get_image_asset,
    image_assets_without_tags,
    initialize,
    load_page_texts,
    popular_queries,
    record_query_activity,
    replace_image_tags,
    search,
    search_images,
    search_threats,
    upsert_image_asset,
)
from rpg_rules_search.drive import FOLDER_MIME_TYPE, DriveItem, folder_id_from_url
from rpg_rules_search.google_drive import GoogleDriveGateway, create_google_drive_gateway
from rpg_rules_search.local_folder import LocalFolderGateway
from rpg_rules_search.oauth import (
    GoogleOAuth,
    InvalidClientSecretsError,
    MissingClientSecretsError,
)
from rpg_rules_search.ollama import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaUnavailableError,
    build_evidence_prompt,
    build_retrieval_query,
    suggest_image_tags,
)
from rpg_rules_search.ollama_runtime import OllamaRuntime, OllamaRuntimeError
from rpg_rules_search.portable_export import export_portable_dataset
from rpg_rules_search.sync import SyncReport, cached_pdf_path, sync_library

PACKAGE_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "rpg-rules-search"
LOGGER = logging.getLogger(__name__)
_IMAGE_AUTO_TAG_COOLDOWN_SECONDS = 300.0


class _RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._monotonic = monotonic
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def retry_after(self, key: str) -> int | None:
        now = self._monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_requests:
                return max(1, int(bucket[0] + self._window_seconds - now + 0.999))
            bucket.append(now)
        return None


class _CooldownImageTagger:
    def __init__(
        self,
        tagger: Callable[[bytes, str], list[str]],
        *,
        cooldown_seconds: float = _IMAGE_AUTO_TAG_COOLDOWN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tagger = tagger
        self._cooldown_seconds = cooldown_seconds
        self._monotonic = monotonic
        self._blocked_until = 0.0

    def __call__(self, content: bytes, file_name: str) -> list[str]:
        if self._monotonic() < self._blocked_until:
            return []
        try:
            return self._tagger(content, file_name)
        except OllamaUnavailableError as error:
            self._blocked_until = self._monotonic() + self._cooldown_seconds
            LOGGER.warning(
                "Auto-tag de imagens indisponível por %.0fs; novas tentativas serão adiadas: %s",
                self._cooldown_seconds,
                error,
            )
            return []

    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown_seconds

    def retry_in_seconds(self) -> int:
        remaining = self._blocked_until - self._monotonic()
        return max(0, int(remaining + 0.999))


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class ThreatSearchResponse(BaseModel):
    query: str | None
    category: str | None
    results: list[ThreatSearchResult]


class ImageResponse(BaseModel):
    image_id: int
    file_name: str
    content_type: str
    width: int | None
    height: int | None
    tags: list[str]
    image_url: str


class ImageSearchResponse(BaseModel):
    query: str | None
    results: list[ImageResponse]


class ImageUploadResponse(ImageResponse):
    duplicate: bool
    auto_tagged: bool


class ImageTagUpdateRequest(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


class OAuthStatus(BaseModel):
    client_configured: bool
    authorized: bool


class AuthorizationStart(BaseModel):
    authorization_url: str


class OllamaStatus(BaseModel):
    base_url: str
    text_model: str
    vision_model: str
    auto_pull: bool
    available: bool
    is_local: bool
    installed_models: list[str]
    image_auto_tag_cooldown_seconds: int
    image_auto_tag_retry_in_seconds: int


class FolderSelection(BaseModel):
    folder_url: str = Field(min_length=1, max_length=2_000)


class LocalFolderSelection(BaseModel):
    folder_path: str = Field(min_length=1, max_length=4_096)


class QuestionResponse(BaseModel):
    question: str
    answer: str
    sources: list[SearchResult]
    model: str


class PopularQuery(BaseModel):
    query: str
    count: int


class PopularResponse(BaseModel):
    searches: list[PopularQuery]
    questions: list[PopularQuery]


class SourceStatus(BaseModel):
    configured: bool
    source_type: str | None = None
    folder_name: str | None = None
    sync_interval_seconds: int = 60


class SyncErrorResponse(BaseModel):
    file_name: str
    message: str


class SyncStatus(BaseModel):
    running: bool
    last_sync_at: str | None = None
    discovered: int = 0
    processed: int = 0
    current_file: str | None = None
    ingested: int = 0
    unchanged: int = 0
    duplicates: int = 0
    removed: int = 0
    errors: list[SyncErrorResponse] = Field(default_factory=list)


SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}


def create_app(
    database_path: Path | None = None,
    *,
    source_path: Path | None = None,
    oauth: GoogleOAuth | None = None,
    ollama_client: OllamaClient | None = None,
    ollama_runtime_factory: Callable[[str], OllamaRuntime] | None = None,
    drive_gateway_factory: Callable[[Any], GoogleDriveGateway] = create_google_drive_gateway,
    enable_periodic_sync: bool = True,
    enable_ollama_runtime: bool = False,
    rate_limit_max_requests: int | None = None,
    rate_limit_window_seconds: float | None = None,
) -> FastAPI:
    resolved_database_path = database_path or DEFAULT_DATA_DIR / "library.sqlite3"
    resolved_ollama_runtime_factory = ollama_runtime_factory or OllamaRuntime
    resolved_source_path = source_path or resolved_database_path.parent / "source.json"
    resolved_ollama_settings_path = resolved_database_path.parent / "ollama.json"
    resolved_oauth = oauth or GoogleOAuth(
        resolved_database_path.parent / "client_secret.json",
        resolved_database_path.parent / "token.json",
    )
    ollama_settings = load_ollama_settings(resolved_ollama_settings_path) or OllamaSettings(
        base_url=os.getenv("RPG_RULES_OLLAMA_URL", "http://127.0.0.1:11434"),
        text_model=os.getenv("RPG_RULES_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        vision_model=os.getenv("RPG_RULES_OLLAMA_VISION_MODEL", "gemma3:4b"),
        auto_pull=os.getenv("RPG_RULES_OLLAMA_AUTO_PULL", "1") != "0",
    )
    image_auto_tag_cooldown_seconds = max(
        0.0,
        float(os.getenv("RPG_RULES_OLLAMA_IMAGE_AUTO_TAG_COOLDOWN_SECONDS", "300")),
    )
    resolved_rate_limit_max_requests = max(
        0,
        rate_limit_max_requests
        if rate_limit_max_requests is not None
        else int(os.getenv("RPG_RULES_API_RATE_LIMIT_MAX_REQUESTS", "240")),
    )
    resolved_rate_limit_window_seconds = max(
        0.0,
        rate_limit_window_seconds
        if rate_limit_window_seconds is not None
        else float(os.getenv("RPG_RULES_API_RATE_LIMIT_WINDOW_SECONDS", "60")),
    )
    rate_limiter = (
        _RateLimiter(
            resolved_rate_limit_max_requests,
            resolved_rate_limit_window_seconds,
        )
        if resolved_rate_limit_max_requests > 0 and resolved_rate_limit_window_seconds > 0
        else None
    )
    resolved_ollama = ollama_client or OllamaClient(
        model=ollama_settings.text_model,
        base_url=ollama_settings.base_url,
    )
    resolved_vision_ollama = ollama_client or OllamaClient(
        model=ollama_settings.vision_model,
        base_url=resolved_ollama.base_url,
    )
    image_tagger = _CooldownImageTagger(
        lambda content, file_name: suggest_image_tags(
            resolved_vision_ollama,
            content,
            file_name,
        ),
        cooldown_seconds=image_auto_tag_cooldown_seconds,
    )

    def configure_ollama() -> None:
        runtime = resolved_ollama_runtime_factory(resolved_ollama.base_url)
        install_if_missing = app.state.ollama_settings.auto_pull
        resolved_ollama.model = runtime.ensure_model(
            resolved_ollama.model,
            install_if_missing=install_if_missing,
        )
        resolved_vision_ollama.model = runtime.ensure_model(
            resolved_vision_ollama.model,
            require_vision=True,
            install_if_missing=install_if_missing,
        )

    def run_sync() -> SyncReport:
        source = load_drive_source(resolved_source_path)
        if source is None:
            raise RuntimeError("Selecione uma pasta do Drive ou uma pasta local antes de sincronizar")
        if source.source_type == "local":
            gateway = LocalFolderGateway(Path(source.folder_id))
        else:
            credentials = resolved_oauth.load_credentials()
            gateway = drive_gateway_factory(credentials)
        connection = connect(resolved_database_path)
        try:
            return sync_library(
                connection,
                gateway,
                source.folder_id,
                resolved_database_path.parent / "documents",
                progress_callback=publish_sync_progress,
                image_tagger=image_tagger,
            )
        finally:
            connection.close()

    def publish_sync_progress(report: SyncReport, current_file: str | None) -> None:
        app.state.sync_status = SyncStatus(
            running=True,
            discovered=report.discovered,
            processed=(
                report.ingested + report.unchanged + report.duplicates + len(report.errors)
            ),
            current_file=current_file,
            ingested=report.ingested,
            unchanged=report.unchanged,
            duplicates=report.duplicates,
            removed=report.removed,
            errors=[
                SyncErrorResponse(file_name=error.file_name, message=error.message)
                for error in report.errors
            ],
        )

    async def perform_sync() -> SyncStatus:
        async with app.state.sync_lock:
            app.state.sync_status = SyncStatus(running=True)
            try:
                report = await asyncio.to_thread(run_sync)
                status = SyncStatus(
                    running=False,
                    last_sync_at=datetime.now(UTC).isoformat(),
                    discovered=report.discovered,
                    processed=report.discovered,
                    ingested=report.ingested,
                    unchanged=report.unchanged,
                    duplicates=report.duplicates,
                    removed=report.removed,
                    errors=[
                        SyncErrorResponse(file_name=error.file_name, message=error.message)
                        for error in report.errors
                    ],
                )
                app.state.sync_status = status
                return status
            except Exception:
                app.state.sync_status = SyncStatus(running=False)
                raise

    async def periodic_sync() -> None:
        while True:
            source = load_drive_source(resolved_source_path)
            can_sync = source is not None and (
                source.source_type == "local" or resolved_oauth.authorized
            )
            if can_sync:
                try:
                    await perform_sync()
                except Exception:
                    LOGGER.exception("Falha na sincronização periódica")
            await asyncio.sleep(source.sync_interval_seconds if source else 60)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Iterator[None]:
        resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = connect(resolved_database_path)
        initialize(connection)
        connection.close()
        if enable_ollama_runtime:
            try:
                await asyncio.to_thread(configure_ollama)
            except OllamaRuntimeError as error:
                LOGGER.warning("Ollama não foi configurado automaticamente: %s", error)
        sync_task = asyncio.create_task(periodic_sync()) if enable_periodic_sync else None
        try:
            yield
        finally:
            if sync_task is not None:
                sync_task.cancel()
                try:
                    await sync_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Arquivo Arcano", version="0.1.0", lifespan=lifespan)
    app.state.database_path = resolved_database_path
    app.state.source_path = resolved_source_path
    app.state.ollama_settings_path = resolved_ollama_settings_path
    app.state.ollama_settings = ollama_settings
    app.state.oauth = resolved_oauth
    app.state.oauth_states = set()
    app.state.ollama = resolved_ollama
    app.state.vision_ollama = resolved_vision_ollama
    app.state.sync_lock = asyncio.Lock()
    app.state.sync_status = SyncStatus(running=False)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    if rate_limiter is not None:

        @app.middleware("http")
        async def enforce_rate_limit(request: Request, call_next: Callable[..., Any]) -> Any:
            if request.url.path.startswith("/api/"):
                client_host = request.client.host if request.client is not None else "unknown"
                key = f"{client_host}:{request.method}:{request.url.path}"
                retry_after = rate_limiter.retry_after(key)
                if retry_after is not None:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Muitas requisições; tente novamente em instantes."},
                        headers={"Retry-After": str(retry_after)},
                    )
            return await call_next(request)

    def database() -> Iterator[sqlite3.Connection]:
        connection = connect(app.state.database_path)
        try:
            yield connection
        finally:
            connection.close()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="profile.html")

    @app.get("/api/search", response_model=SearchResponse)
    def search_api(
        q: str = Query(min_length=2, max_length=300),
        limit: int = Query(default=20, ge=1, le=100),
        connection: sqlite3.Connection = Depends(database),
    ) -> SearchResponse:
        try:
            results = search(connection, q, limit)
        except sqlite3.OperationalError as error:
            raise HTTPException(status_code=400, detail="Consulta de busca inválida") from error
        record_query_activity(connection, "search", q)
        return SearchResponse(query=q, results=results)

    @app.get("/api/popular", response_model=PopularResponse)
    def popular_api(
        limit: int = Query(default=6, ge=1, le=12),
        connection: sqlite3.Connection = Depends(database),
    ) -> PopularResponse:
        return PopularResponse(
            searches=[
                PopularQuery(query=query, count=count)
                for query, count in popular_queries(connection, "search", limit)
            ],
            questions=[
                PopularQuery(query=query, count=count)
                for query, count in popular_queries(connection, "question", limit)
            ],
        )

    @app.get("/api/export", response_class=FileResponse)
    def portable_export_api(
        connection: sqlite3.Connection = Depends(database),
    ) -> FileResponse:
        export_dir = resolved_database_path.parent / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(suffix=".zip", dir=export_dir)
        os.close(descriptor)
        export_path = Path(temporary_name)
        try:
            export_portable_dataset(connection, export_path)
        except Exception:
            export_path.unlink(missing_ok=True)
            raise
        return FileResponse(
            export_path,
            media_type="application/zip",
            filename="arquivo-arcano.zip",
            background=BackgroundTask(export_path.unlink, missing_ok=True),
        )

    def image_storage_path(content_hash: str, file_name: str, content_type: str) -> Path:
        guessed_extension = Path(file_name).suffix.lower()
        if not guessed_extension:
            guessed_extension = mimetypes.guess_extension(content_type) or ".img"
        return resolved_database_path.parent / "images" / f"{content_hash}{guessed_extension}"

    def image_dimensions(image_bytes: bytes) -> tuple[int | None, int | None]:
        try:
            pixmap = fitz.Pixmap(image_bytes)
        except Exception:  # noqa: BLE001
            return None, None
        return int(pixmap.width), int(pixmap.height)

    def to_image_response(result: ImageSearchResult) -> ImageResponse:
        return ImageResponse(
            image_id=result.image_id,
            file_name=result.file_name,
            content_type=result.content_type,
            width=result.width,
            height=result.height,
            tags=result.tags,
            image_url=f"/api/images/{result.image_id}/preview",
        )

    @app.get("/api/images", response_model=ImageSearchResponse)
    def image_search_api(
        q: str | None = Query(default=None, min_length=2, max_length=300),
        limit: int = Query(default=24, ge=1, le=120),
        connection: sqlite3.Connection = Depends(database),
    ) -> ImageSearchResponse:
        try:
            results = search_images(connection, q, limit)
        except sqlite3.OperationalError as error:
            raise HTTPException(status_code=400, detail="Consulta de busca inválida") from error
        if q:
            record_query_activity(connection, "search", q)
        return ImageSearchResponse(
            query=q,
            results=[to_image_response(result) for result in results],
        )

    @app.post("/api/images", response_model=ImageUploadResponse)
    async def upload_image(
        file: UploadFile = File(),
        auto_tag: bool = Query(default=True),
        connection: sqlite3.Connection = Depends(database),
    ) -> ImageUploadResponse:
        content = await file.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="A imagem excede 8 MB")
        if not content:
            raise HTTPException(status_code=400, detail="A imagem está vazia")
        if file.content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Formato de imagem não suportado")

        content_hash = hashlib.sha256(content).hexdigest()
        storage_path = image_storage_path(content_hash, file.filename or "imagem", file.content_type)
        width, height = image_dimensions(content)
        image_id, created = upsert_image_asset(
            connection,
            file_name=file.filename or "imagem",
            content_type=file.content_type,
            content_hash=content_hash,
            storage_path=str(storage_path),
            width=width,
            height=height,
        )
        if created:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_bytes(content)

        auto_tagged = False
        if auto_tag:
            try:
                tags = suggest_image_tags(
                    resolved_vision_ollama,
                    content,
                    file.filename or "imagem",
                )
                replace_image_tags(connection, image_id, tags, source="ai")
                auto_tagged = bool(tags)
            except OllamaUnavailableError:
                auto_tagged = False

        result = next(
            (
                item
                for item in search_images(connection, None, 200)
                if item.image_id == image_id
            ),
            None,
        )
        if result is None:
            row = get_image_asset(connection, image_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Imagem não encontrada")
            result = ImageSearchResult(
                image_id=image_id,
                file_name=str(row["file_name"]),
                content_type=str(row["content_type"]),
                width=int(row["width"]) if row["width"] is not None else None,
                height=int(row["height"]) if row["height"] is not None else None,
                tags=[],
                score=None,
            )
        response = to_image_response(result)
        return ImageUploadResponse(
            **response.model_dump(),
            duplicate=not created,
            auto_tagged=auto_tagged,
        )

    @app.post("/api/images/auto-tag")
    def auto_tag_pending_images(
        limit: int = Query(default=100, ge=1, le=500),
        connection: sqlite3.Connection = Depends(database),
    ) -> dict[str, int]:
        rows = image_assets_without_tags(connection, limit=limit)
        tagged = 0
        for row in rows:
            try:
                image_bytes = Path(str(row["storage_path"])).read_bytes()
                tags = suggest_image_tags(
                    resolved_vision_ollama,
                    image_bytes,
                    str(row["file_name"]),
                )
                replace_image_tags(connection, int(row["id"]), tags, source="ai")
                tagged += 1
            except (OSError, OllamaUnavailableError):
                continue
        return {"processed": len(rows), "tagged": tagged}

    @app.put("/api/images/{image_id}/tags", response_model=ImageResponse)
    def update_image_tags(
        image_id: int,
        request: ImageTagUpdateRequest,
        connection: sqlite3.Connection = Depends(database),
    ) -> ImageResponse:
        row = get_image_asset(connection, image_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Imagem não encontrada")
        replace_image_tags(connection, image_id, request.tags, source="manual")
        result = next(
            (
                item
                for item in search_images(connection, None, 500)
                if item.image_id == image_id
            ),
            None,
        )
        if result is None:
            result = ImageSearchResult(
                image_id=image_id,
                file_name=str(row["file_name"]),
                content_type=str(row["content_type"]),
                width=int(row["width"]) if row["width"] is not None else None,
                height=int(row["height"]) if row["height"] is not None else None,
                tags=[],
                score=None,
            )
        return to_image_response(result)

    @app.get("/api/images/{image_id}/file", response_class=FileResponse)
    def image_file(
        image_id: int,
        connection: sqlite3.Connection = Depends(database),
    ) -> FileResponse:
        row = get_image_asset(connection, image_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Imagem não encontrada")
        path = Path(str(row["storage_path"]))
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Arquivo de imagem não encontrado")
        return FileResponse(path, media_type=str(row["content_type"]), filename=str(row["file_name"]))

    @app.get("/api/images/{image_id}/preview", response_class=FileResponse)
    def image_preview(
        image_id: int,
        connection: sqlite3.Connection = Depends(database),
    ) -> FileResponse:
        row = get_image_asset(connection, image_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Imagem não encontrada")
        path = Path(str(row["storage_path"]))
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Arquivo de imagem não encontrado")
        return FileResponse(path, media_type=str(row["content_type"]))

    @app.get("/api/threats", response_model=ThreatSearchResponse)
    def threat_search_api(
        q: str | None = Query(default=None, min_length=2, max_length=300),
        category: str | None = Query(default=None, min_length=2, max_length=100),
        limit: int = Query(default=20, ge=1, le=100),
        connection: sqlite3.Connection = Depends(database),
    ) -> ThreatSearchResponse:
        try:
            results = search_threats(connection, q, category, limit)
        except sqlite3.OperationalError as error:
            raise HTTPException(status_code=400, detail="Consulta de busca inválida") from error
        return ThreatSearchResponse(query=q, category=category, results=results)

    @app.get("/api/documents/{document_id}/file", response_class=FileResponse)
    def document_file(document_id: int) -> FileResponse:
        path, name = resolve_document_pdf(document_id)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=name,
            content_disposition_type="inline",
        )

    def resolve_document_pdf(document_id: int) -> tuple[Path, str]:
        connection = connect(resolved_database_path)
        try:
            row = connection.execute(
                "SELECT drive_file_id, name, mime_type, status FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["status"] != "ready":
            raise HTTPException(status_code=404, detail="Documento não encontrado")
        path = cached_pdf_path(
            resolved_database_path.parent / "documents",
            str(row["drive_file_id"]),
            str(row["mime_type"]),
        )
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Arquivo local não encontrado")
        return path, str(row["name"])

    def render_evidence_images(sources: list[SearchResult], limit: int = 4) -> list[bytes]:
        images: list[bytes] = []
        seen_pages: set[tuple[int, int]] = set()
        for source in sources:
            key = (source.document_id, source.page_index)
            if key in seen_pages:
                continue
            seen_pages.add(key)
            try:
                pdf_path, _ = resolve_document_pdf(source.document_id)
                with fitz.open(pdf_path) as document:
                    if source.page_index >= document.page_count:
                        continue
                    page = document[source.page_index]
                    scale = min(2.0, 1200 / page.rect.width)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    images.append(pixmap.tobytes("png"))
            except (HTTPException, OSError, RuntimeError):
                continue
            if len(images) >= limit:
                break
        return images

    @app.get(
        "/api/documents/{document_id}/pages/{page_index}.pdf",
        response_class=FileResponse,
    )
    def document_page_preview(document_id: int, page_index: int) -> FileResponse:
        pdf_path, _ = resolve_document_pdf(document_id)
        preview_dir = resolved_database_path.parent / "page-previews"
        preview_path = preview_dir / f"{document_id}-{page_index}.pdf"
        if page_index < 0:
            raise HTTPException(status_code=404, detail="Página não encontrada")
        if not preview_path.is_file() or preview_path.stat().st_mtime < pdf_path.stat().st_mtime:
            with fitz.open(pdf_path) as document:
                if page_index >= document.page_count:
                    raise HTTPException(status_code=404, detail="Página não encontrada")
                preview = fitz.open()
                preview.insert_pdf(document, from_page=page_index, to_page=page_index)
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview.save(preview_path)
            preview.close()
        return FileResponse(
            preview_path,
            media_type="application/pdf",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @app.get("/api/oauth/status", response_model=OAuthStatus)
    def oauth_status() -> OAuthStatus:
        return OAuthStatus(
            client_configured=resolved_oauth.client_configured,
            authorized=resolved_oauth.authorized,
        )

    @app.post("/api/oauth/client", response_model=OAuthStatus)
    async def upload_oauth_client(file: UploadFile = File()) -> OAuthStatus:
        content = await file.read(1_048_577)
        if len(content) > 1_048_576:
            raise HTTPException(status_code=413, detail="O arquivo OAuth excede 1 MB")
        try:
            resolved_oauth.save_client_secrets(content)
        except InvalidClientSecretsError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return OAuthStatus(client_configured=True, authorized=resolved_oauth.authorized)

    @app.post("/api/oauth/start", response_model=AuthorizationStart)
    def start_oauth() -> AuthorizationStart:
        try:
            authorization_url, state = resolved_oauth.begin_authorization()
        except MissingClientSecretsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        app.state.oauth_states.add(state)
        return AuthorizationStart(authorization_url=authorization_url)

    @app.get("/api/oauth/callback")
    def oauth_callback(request: Request, state: str, error: str | None = None) -> RedirectResponse:
        if error:
            return RedirectResponse(url=f"/?oauth_error={error}", status_code=303)
        if state not in app.state.oauth_states:
            raise HTTPException(status_code=400, detail="Estado OAuth inválido ou expirado")
        app.state.oauth_states.remove(state)
        try:
            resolved_oauth.complete_authorization(str(request.url), state)
        except Exception:
            LOGGER.exception("Falha ao concluir autorização OAuth")
            return RedirectResponse(url="/?oauth_error=authorization_failed", status_code=303)
        return RedirectResponse(url="/?oauth=connected", status_code=303)

    @app.delete("/api/oauth", response_model=OAuthStatus)
    def disconnect_oauth() -> OAuthStatus:
        resolved_oauth.disconnect()
        return OAuthStatus(
            client_configured=resolved_oauth.client_configured,
            authorized=False,
        )

    def authenticated_drive() -> GoogleDriveGateway:
        try:
            credentials = resolved_oauth.load_credentials()
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        return drive_gateway_factory(credentials)

    @app.put("/api/source", response_model=SourceStatus)
    def select_source(
        selection: FolderSelection,
        gateway: GoogleDriveGateway = Depends(authenticated_drive),
    ) -> SourceStatus:
        try:
            folder_id = folder_id_from_url(selection.folder_url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        item: DriveItem = gateway.get_item(folder_id)
        if item.trashed or item.mime_type != FOLDER_MIME_TYPE:
            raise HTTPException(status_code=400, detail="Selecione uma pasta válida do Google Drive")
        source = DriveSource(folder_id=item.id, folder_name=item.name)
        save_drive_source(app.state.source_path, source)
        return SourceStatus(
            configured=True,
            source_type=source.source_type,
            folder_name=source.folder_name,
            sync_interval_seconds=source.sync_interval_seconds,
        )

    @app.put("/api/source/local", response_model=SourceStatus)
    def select_local_source(selection: LocalFolderSelection) -> SourceStatus:
        folder = Path(selection.folder_path).expanduser().resolve()
        if not folder.is_dir():
            raise HTTPException(status_code=400, detail="Selecione uma pasta local existente")
        source = DriveSource(
            source_type="local",
            folder_id=str(folder),
            folder_name=folder.name or str(folder),
        )
        save_drive_source(app.state.source_path, source)
        return SourceStatus(
            configured=True,
            source_type=source.source_type,
            folder_name=source.folder_name,
            sync_interval_seconds=source.sync_interval_seconds,
        )

    @app.get("/api/sync", response_model=SyncStatus)
    def sync_status() -> SyncStatus:
        return app.state.sync_status

    @app.post("/api/sync", response_model=SyncStatus)
    async def sync_now() -> SyncStatus:
        if app.state.sync_lock.locked():
            raise HTTPException(status_code=409, detail="Uma sincronização já está em andamento")
        try:
            return await perform_sync()
        except (PermissionError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/ask", response_model=QuestionResponse)
    def ask_api(
        q: str = Query(min_length=3, max_length=500),
        extended: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(database),
    ) -> QuestionResponse:
        record_query_activity(connection, "question", q)
        retrieval_query = build_retrieval_query(q)
        sources = search(connection, retrieval_query, limit=8) if retrieval_query else []
        if not sources:
            return QuestionResponse(
                question=q,
                answer="Não encontrei evidência suficiente na biblioteca.",
                sources=[],
                model=resolved_ollama.model,
            )
        try:
            page_texts = load_page_texts(connection, sources)
            images = render_evidence_images(sources, limit=6 if extended else 4)
            answer = resolved_ollama.answer(
                build_evidence_prompt(q, sources, page_texts, extended=extended),
                images=images,
                extended=extended,
            )
        except OllamaUnavailableError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return QuestionResponse(
            question=q,
            answer=answer,
            sources=sources,
            model=resolved_ollama.model,
        )

    @app.get("/api/source", response_model=SourceStatus)
    def source_status() -> SourceStatus:
        source = load_drive_source(app.state.source_path)
        if source is None:
            return SourceStatus(configured=False)
        return SourceStatus(
            configured=True,
            source_type=source.source_type,
            folder_name=source.folder_name,
            sync_interval_seconds=source.sync_interval_seconds,
        )

    def ollama_status() -> OllamaStatus:
        settings: OllamaSettings = app.state.ollama_settings
        runtime = resolved_ollama_runtime_factory(settings.base_url)
        available = runtime.is_available()
        installed_models: list[str] = []
        if available:
            try:
                installed_models = [model.name for model in runtime.installed_models()]
            except OllamaRuntimeError:
                available = False
        return OllamaStatus(
            **settings.model_dump(),
            available=available,
            is_local=runtime.is_local,
            installed_models=installed_models,
            image_auto_tag_cooldown_seconds=int(image_tagger.cooldown_seconds),
            image_auto_tag_retry_in_seconds=image_tagger.retry_in_seconds(),
        )

    @app.get("/api/ollama", response_model=OllamaStatus)
    def ollama_status_api() -> OllamaStatus:
        return ollama_status()

    @app.put("/api/ollama", response_model=OllamaStatus)
    def update_ollama_api(settings: OllamaSettings) -> OllamaStatus:
        save_ollama_settings(app.state.ollama_settings_path, settings)
        app.state.ollama_settings = settings
        resolved_ollama.base_url = settings.base_url
        resolved_ollama.model = settings.text_model
        resolved_vision_ollama.base_url = settings.base_url
        resolved_vision_ollama.model = settings.vision_model
        return ollama_status()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app(enable_ollama_runtime=True)
