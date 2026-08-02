from rpg_rules_search.ollama_runtime import OllamaModel, OllamaRuntime, _parameter_count


class FakeRuntime(OllamaRuntime):
    pulled: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.pulled = []

    def ensure_available(self) -> bool:
        return True

    def pull_model(self, model: str) -> None:
        self.pulled.append(model)

    def _request_json(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout: float,
    ) -> dict[str, object]:
        if path == "/api/tags":
            return {
                "models": [
                    {"name": "text-small", "size": 2_000},
                    {"name": "vision-large", "size": 8_000},
                    {"name": "text-large", "size": 9_000},
                ]
            }
        details = {
            "text-small": {"capabilities": ["completion"], "parameter_size": "3B"},
            "vision-large": {
                "capabilities": ["completion", "vision"],
                "parameter_size": "12B",
            },
            "text-large": {
                "capabilities": ["completion", "tools"],
                "parameter_size": "14B",
            },
        }
        model = str((payload or {})["model"])
        entry = details[model]
        return {
            "capabilities": entry["capabilities"],
            "details": {"parameter_size": entry["parameter_size"]},
        }


def test_runtime_selects_best_installed_text_and_vision_models() -> None:
    runtime = FakeRuntime()

    assert runtime.best_model() == "text-large"
    assert runtime.best_model(require_vision=True) == "vision-large"
    assert runtime.model_supports_vision("text-small") is False
    assert runtime.model_supports_vision("vision-large") is True


def test_parameter_count_parses_ollama_sizes() -> None:
    assert _parameter_count("14.7B") == 14_700_000_000
    assert _parameter_count("900M") == 900_000_000
    assert _parameter_count("unknown") == 0


def test_remote_runtime_is_not_considered_local() -> None:
    assert OllamaRuntime("http://192.168.1.50:11434").is_local is False


def test_runtime_keeps_best_installed_model_without_pull() -> None:
    runtime = FakeRuntime()

    assert runtime.ensure_model("fallback") == "text-large"
    assert runtime.pulled == []


class EmptyRuntime(FakeRuntime):
    def installed_models(self) -> list[OllamaModel]:
        return []

    def _model_details(self, name: str) -> dict[str, object]:
        return {"capabilities": ["completion", "vision"], "parameter_count": 4_000_000_000}


def test_runtime_pulls_fallback_when_no_model_is_installed() -> None:
    runtime = EmptyRuntime()

    assert runtime.ensure_model("gemma3:4b", require_vision=True) == "gemma3:4b"
    assert runtime.pulled == ["gemma3:4b"]