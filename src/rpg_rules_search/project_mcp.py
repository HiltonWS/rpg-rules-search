from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from rpg_rules_search.config import OllamaSettings, load_ollama_settings
from rpg_rules_search.ollama import OllamaClient

PROJECT_ROOT = Path(
    os.environ.get("RPG_RULES_PROJECT_ROOT", Path(__file__).resolve().parents[2])
).resolve()
DATA_DIR = Path.home() / ".local" / "share" / "rpg-rules-search"
MAX_FILES = 12
MAX_CONTEXT_BYTES = 120_000
ALLOWED_SUFFIXES = {
    ".css", ".html", ".ini", ".js", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"
}
BLOCKED_PARTS = {".git", ".venv", "__pycache__", "node_modules"}
BLOCKED_NAMES = {".env", "credentials.json", "credencials.json"}
CODING_PERSONA = """Você é um revisor de software do projeto Arquivo Arcano.
Analise apenas a tarefa e os arquivos fornecidos. Priorize correção, privacidade local-first,
compatibilidade, simplicidade e testes. Não alegue ter executado código. Responda em português,
separe achados verificáveis de sugestões e cite caminhos de arquivos quando relevante."""

mcp = FastMCP(
    "arquivo-arcano-ollama",
    instructions=(
        "Consulta o Ollama configurado localmente para revisar e melhorar somente este projeto. "
        "Não use para responder perguntas sobre os livros da biblioteca."
    ),
)


def collect_project_context(paths: Sequence[str], root: Path = PROJECT_ROOT) -> str:
    if not paths:
        raise ValueError("Informe ao menos um arquivo do projeto")
    if len(paths) > MAX_FILES:
        raise ValueError(f"Use no máximo {MAX_FILES} arquivos por consulta")

    sections: list[str] = []
    total_bytes = 0
    resolved_root = root.resolve()
    for requested_path in paths:
        candidate = (resolved_root / requested_path).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(f"Caminho fora do projeto: {requested_path}")
        relative = candidate.relative_to(resolved_root)
        if (
            any(part in BLOCKED_PARTS for part in relative.parts)
            or candidate.name.casefold() in BLOCKED_NAMES
            or candidate.suffix.casefold() not in ALLOWED_SUFFIXES
        ):
            raise ValueError(f"Arquivo não permitido: {requested_path}")
        content = candidate.read_bytes()
        total_bytes += len(content)
        if total_bytes > MAX_CONTEXT_BYTES:
            raise ValueError(f"O contexto excede {MAX_CONTEXT_BYTES} bytes")
        sections.append(f"### {relative.as_posix()}\n```\n{content.decode('utf-8')}\n```")
    return "\n\n".join(sections)


def build_client(settings_path: Path = DATA_DIR / "ollama.json") -> OllamaClient:
    settings = load_ollama_settings(settings_path) or OllamaSettings()
    return OllamaClient(
        model=settings.text_model,
        base_url=settings.base_url,
        system_prompt=CODING_PERSONA,
    )


def consult_project_ollama(
    task: str,
    paths: Sequence[str],
    *,
    root: Path = PROJECT_ROOT,
    client: OllamaClient | None = None,
) -> str:
    if not task.strip():
        raise ValueError("A tarefa não pode ficar vazia")
    context = collect_project_context(paths, root)
    prompt = f"""Tarefa de desenvolvimento:
{task.strip()}

Arquivos fornecidos pelo agente:
{context}

Revise o código sem presumir conteúdo fora destes arquivos. Retorne riscos, proposta objetiva e testes relevantes."""
    return (client or build_client()).answer(prompt)


@mcp.tool()
def consultar_ollama_do_projeto(tarefa: str, arquivos: list[str]) -> str:
    """Consulta o Ollama para revisar uma tarefa usando arquivos textuais deste workspace."""
    return consult_project_ollama(tarefa, arquivos)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()