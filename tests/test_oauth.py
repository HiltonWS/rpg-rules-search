import json
import os
from pathlib import Path

import pytest

from rpg_rules_search.oauth import GoogleOAuth, MissingClientSecretsError


class FakeCredentials:
    def to_json(self) -> str:
        return json.dumps({"token": "access-token", "refresh_token": "refresh-token"})


class FakeFlow:
    credentials = FakeCredentials()

    def authorization_url(self, **kwargs: object) -> tuple[str, str]:
        assert kwargs["access_type"] == "offline"
        assert kwargs["include_granted_scopes"] == "false"
        return "https://accounts.google.com/o/oauth2/auth", "csrf-state"

    def fetch_token(self, *, authorization_response: str) -> None:
        assert authorization_response.endswith("code=abc")


def test_oauth_requires_downloaded_desktop_client_secrets(tmp_path: Path) -> None:
    oauth = GoogleOAuth(tmp_path / "client_secret.json", tmp_path / "token.json")

    with pytest.raises(MissingClientSecretsError):
        oauth.begin_authorization(lambda *_args, **_kwargs: FakeFlow())


def test_oauth_saves_callback_token_atomically(tmp_path: Path) -> None:
    client_path = tmp_path / "client_secret.json"
    token_path = tmp_path / "token.json"
    client_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuth(client_path, token_path)

    url, state = oauth.begin_authorization(lambda *_args, **_kwargs: FakeFlow())
    oauth.complete_authorization(
        "http://127.0.0.1:8765/api/oauth/callback?code=abc",
        state,
        lambda *_args, **_kwargs: FakeFlow(),
    )

    assert url.startswith("https://accounts.google.com/")
    assert state == "csrf-state"
    assert json.loads(token_path.read_text(encoding="utf-8"))["refresh_token"] == "refresh-token"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert not token_path.with_suffix(".tmp").exists()


def test_oauth_allows_http_only_while_exchanging_loopback_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    client_path = tmp_path / "client_secret.json"
    client_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuth(client_path, tmp_path / "token.json")

    class InspectingFlow(FakeFlow):
        def fetch_token(self, *, authorization_response: str) -> None:
            assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"

    oauth.complete_authorization(
        "http://127.0.0.1:8765/api/oauth/callback?code=abc",
        "csrf-state",
        lambda *_args, **_kwargs: InspectingFlow(),
    )

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_oauth_preserves_pkce_verifier_between_start_and_callback(tmp_path: Path) -> None:
    client_path = tmp_path / "client_secret.json"
    client_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuth(client_path, tmp_path / "token.json")

    class PkceFlow(FakeFlow):
        code_verifier: str | None = None

        def authorization_url(self, **kwargs: object) -> tuple[str, str]:
            self.code_verifier = "original-code-verifier"
            return super().authorization_url(**kwargs)

        def fetch_token(self, *, authorization_response: str) -> None:
            assert self.code_verifier == "original-code-verifier"

    oauth.begin_authorization(lambda *_args, **_kwargs: PkceFlow())
    oauth.complete_authorization(
        "http://127.0.0.1:8765/api/oauth/callback?code=abc",
        "csrf-state",
        lambda *_args, **_kwargs: PkceFlow(),
    )