from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/api/oauth/callback"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_INSECURE_TRANSPORT_LOCK = Lock()


class MissingClientSecretsError(FileNotFoundError):
    pass


class InvalidClientSecretsError(ValueError):
    pass


FlowFactory = Callable[..., Any]


@contextmanager
def allow_matching_loopback_http(authorization_response: str, redirect_uri: str):
    callback = urlparse(authorization_response)
    redirect = urlparse(redirect_uri)
    callback_target = (callback.scheme, callback.hostname, callback.port, callback.path)
    redirect_target = (redirect.scheme, redirect.hostname, redirect.port, redirect.path)
    if (
        callback.scheme != "http"
        or callback.hostname not in LOOPBACK_HOSTS
        or callback_target != redirect_target
    ):
        yield
        return

    with _INSECURE_TRANSPORT_LOCK:
        previous = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = previous


class GoogleOAuth:
    def __init__(
        self,
        client_secrets_path: Path,
        token_path: Path,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
    ) -> None:
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self.redirect_uri = redirect_uri
        self._pending_code_verifiers: dict[str, str] = {}
        self._pending_lock = Lock()

    @property
    def client_configured(self) -> bool:
        return self.client_secrets_path.is_file()

    @property
    def authorized(self) -> bool:
        return self.token_path.is_file()

    def save_client_secrets(self, content: bytes) -> None:
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidClientSecretsError("O arquivo OAuth não contém JSON válido") from error
        installed = payload.get("installed") if isinstance(payload, dict) else None
        required_fields = {"client_id", "client_secret", "auth_uri", "token_uri"}
        if not isinstance(installed, dict) or not required_fields.issubset(installed):
            raise InvalidClientSecretsError(
                "Use credenciais OAuth do tipo aplicativo para computador"
            )

        self.client_secrets_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.client_secrets_path.with_suffix(".tmp")
        temporary_path.write_bytes(content)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, self.client_secrets_path)

    def load_credentials(self) -> Credentials:
        if not self.authorized:
            raise PermissionError("Google Drive ainda não foi autorizado")
        credentials = Credentials.from_authorized_user_file(str(self.token_path), DRIVE_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            temporary_path = self.token_path.with_suffix(".tmp")
            temporary_path.write_text(credentials.to_json(), encoding="utf-8")
            temporary_path.chmod(0o600)
            os.replace(temporary_path, self.token_path)
        if not credentials.valid:
            raise PermissionError("A autorização do Google Drive expirou")
        return credentials

    def _create_flow(self, factory: FlowFactory, *, state: str | None = None) -> Any:
        if not self.client_configured:
            raise MissingClientSecretsError(
                f"Credenciais OAuth não encontradas em {self.client_secrets_path}"
            )
        flow = factory(
            str(self.client_secrets_path),
            scopes=DRIVE_SCOPES,
            state=state,
        )
        flow.redirect_uri = self.redirect_uri
        return flow

    def begin_authorization(
        self,
        factory: FlowFactory = Flow.from_client_secrets_file,
    ) -> tuple[str, str]:
        flow = self._create_flow(factory)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="false",
            prompt="consent",
        )
        code_verifier = getattr(flow, "code_verifier", None)
        if isinstance(code_verifier, str):
            with self._pending_lock:
                self._pending_code_verifiers[state] = code_verifier
        return authorization_url, state

    def complete_authorization(
        self,
        authorization_response: str,
        state: str,
        factory: FlowFactory = Flow.from_client_secrets_file,
    ) -> None:
        flow = self._create_flow(factory, state=state)
        with self._pending_lock:
            code_verifier = self._pending_code_verifiers.pop(state, None)
        if code_verifier is not None:
            flow.code_verifier = code_verifier
        with allow_matching_loopback_http(authorization_response, self.redirect_uri):
            flow.fetch_token(authorization_response=authorization_response)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.token_path.with_suffix(".tmp")
        temporary_path.write_text(flow.credentials.to_json(), encoding="utf-8")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, self.token_path)

    def disconnect(self) -> None:
        with self._pending_lock:
            self._pending_code_verifiers.clear()
        self.token_path.unlink(missing_ok=True)