from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size: int
    capabilities: frozenset[str]
    parameter_count: int


class OllamaRuntimeError(RuntimeError):
    pass


class OllamaRuntime:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        autostart: bool = True,
        startup_timeout: float = 12.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.autostart = autostart
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def is_local(self) -> bool:
        host = (urlparse(self.base_url).hostname or "").casefold()
        return host in {"127.0.0.1", "localhost", "::1"}

    def ensure_available(self) -> bool:
        if self.is_available():
            return True
        if not self.is_local or not self.autostart:
            return False
        executable = shutil.which("ollama")
        if executable is None:
            return False
        environment = os.environ.copy()
        environment.setdefault("OLLAMA_HOST", urlparse(self.base_url).netloc)
        self._process = subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.is_available():
                return True
            time.sleep(0.2)
        return False

    def is_available(self) -> bool:
        try:
            self._request_json("/api/tags", timeout=1.5)
        except OllamaRuntimeError:
            return False
        return True

    def installed_models(self) -> list[OllamaModel]:
        response = self._request_json("/api/tags", timeout=5.0)
        raw_models = response.get("models", [])
        if not isinstance(raw_models, list):
            return []
        models: list[OllamaModel] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or not isinstance(raw_model.get("name"), str):
                continue
            name = str(raw_model["name"])
            details = self._model_details(name)
            size = raw_model.get("size", 0)
            models.append(
                OllamaModel(
                    name=name,
                    size=int(size) if isinstance(size, int | float) else 0,
                    capabilities=frozenset(details["capabilities"]),
                    parameter_count=int(details["parameter_count"]),
                )
            )
        return models

    def best_model(self, *, require_vision: bool = False) -> str | None:
        candidates = [
            model
            for model in self.installed_models()
            if not require_vision or "vision" in model.capabilities
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda model: (
                "completion" in model.capabilities,
                "tools" in model.capabilities,
                model.parameter_count,
                model.size,
                model.name,
            ),
        ).name

    def pull_model(self, model: str) -> None:
        if not self.ensure_available():
            raise OllamaRuntimeError(f"Ollama indisponível em {self.base_url}")
        self._request_json(
            "/api/pull",
            payload={"model": model, "stream": False},
            timeout=60 * 60,
        )

    def ensure_model(
        self,
        preferred_model: str,
        *,
        require_vision: bool = False,
        install_if_missing: bool = True,
    ) -> str:
        if not self.ensure_available():
            raise OllamaRuntimeError(f"Ollama indisponível em {self.base_url}")
        best = self.best_model(require_vision=require_vision)
        if best is not None:
            return best
        if not install_if_missing:
            raise OllamaRuntimeError("Nenhum modelo compatível está instalado")
        self.pull_model(preferred_model)
        details = self._model_details(preferred_model)
        capabilities = details.get("capabilities", [])
        if require_vision and "vision" not in capabilities:
            raise OllamaRuntimeError(f"O modelo {preferred_model} não oferece visão")
        return preferred_model

    def _model_details(self, name: str) -> dict[str, object]:
        try:
            response = self._request_json(
                "/api/show",
                payload={"model": name},
                timeout=5.0,
            )
        except OllamaRuntimeError:
            return {"capabilities": [], "parameter_count": 0}
        capabilities = response.get("capabilities", [])
        details = response.get("details", {})
        parameter_size = details.get("parameter_size", "") if isinstance(details, dict) else ""
        return {
            "capabilities": [str(value) for value in capabilities]
            if isinstance(capabilities, list)
            else [],
            "parameter_count": _parameter_count(str(parameter_size)),
        }

    def _request_json(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout: float,
    ) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise OllamaRuntimeError(f"Ollama indisponível em {self.base_url}") from error
        if not isinstance(result, dict):
            raise OllamaRuntimeError("Resposta inválida do Ollama")
        return result


def _parameter_count(parameter_size: str) -> int:
    normalized = parameter_size.strip().upper().replace(" ", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    if not normalized:
        return 0
    suffix = normalized[-1]
    try:
        if suffix in multipliers:
            return int(float(normalized[:-1]) * multipliers[suffix])
        return int(float(normalized))
    except ValueError:
        return 0