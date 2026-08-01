from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from base64 import b64encode
from collections.abc import Callable
from typing import Any

from rpg_rules_search.database import SearchResult

DEFAULT_OLLAMA_MODEL = "gemma3:latest"
RPG_RULES_PERSONA = """Você é o Arquivista Arcano, especialista em consultar e explicar regras de RPG.
Sua função é transformar trechos recuperados da biblioteca do usuário em respostas precisas, didáticas e úteis durante uma sessão de jogo.
Você diferencia regra escrita, interpretação e ausência de evidência. Você não usa conhecimento externo, não completa lacunas por suposição e nunca inventa citações.
Escreva em português natural. Priorize a resposta objetiva, depois sintetize detalhes, condições, exceções e relações entre fontes."""


class OllamaUnavailableError(RuntimeError):
    pass


Transport = Callable[[str, dict[str, object], float], dict[str, object]]

_PORTUGUESE_STOPWORDS = {
    "a", "ao", "aos", "as", "como", "da", "das", "de", "do", "dos", "e", "eh",
    "em", "entre", "era", "essa", "esse", "esta", "este", "foi", "na", "nas", "no",
    "nos", "o", "os", "ou", "para", "pela", "pelo", "por", "qual", "quais", "que",
    "se", "sem", "ser", "sua", "suas", "um", "uma", "umas", "uns",
}
_MAX_EVIDENCE_CHARS = 1400


def build_retrieval_query(question: str) -> str:
    terms = re.findall(r"[\wÀ-ÿ]+", question, flags=re.UNICODE)
    useful_terms = [
        term
        for term in terms
        if len(term) >= 3 and term.casefold() not in _PORTUGUESE_STOPWORDS
    ]
    unique_terms = list(dict.fromkeys(useful_terms))[:12]
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique_terms)


def _evidence_passage(question: str, text: str) -> str:
    clean_text = re.sub(r"\s+", " ", text).strip()
    if len(clean_text) <= _MAX_EVIDENCE_CHARS:
        return clean_text

    terms = [
        term.casefold()
        for term in re.findall(r"[\wÀ-ÿ]+", question, flags=re.UNICODE)
        if len(term) >= 3 and term.casefold() not in _PORTUGUESE_STOPWORDS
    ]
    folded_text = clean_text.casefold()
    positions = [folded_text.find(term) for term in terms]
    matches = [position for position in positions if position >= 0]
    center = min(matches) if matches else 0
    start = max(0, center - _MAX_EVIDENCE_CHARS // 3)
    end = min(len(clean_text), start + _MAX_EVIDENCE_CHARS)
    start = max(0, end - _MAX_EVIDENCE_CHARS)

    if start:
        next_space = clean_text.find(" ", start)
        start = next_space + 1 if next_space >= 0 else start
    if end < len(clean_text):
        previous_space = clean_text.rfind(" ", start, end)
        end = previous_space if previous_space >= 0 else end
    prefix = "..." if start else ""
    suffix = "..." if end < len(clean_text) else ""
    return f"{prefix}{clean_text[start:end]}{suffix}"


def build_evidence_prompt(
    question: str,
    results: list[SearchResult],
    page_texts: dict[tuple[int, int], str] | None = None,
    extended: bool = False,
) -> str:
    evidence = []
    for result in results:
        page = result.printed_page or str(result.page_index + 1)
        source_text = (page_texts or {}).get(
            (result.document_id, result.page_index), result.snippet
        )
        text = re.sub(r"</?mark>", "", source_text, flags=re.IGNORECASE)
        text = _evidence_passage(question, text)
        evidence.append(f"[{result.document_name}, p. {page}] {text}")
    depth_instruction = (
        "Produza uma resposta estendida: examine todas as evidências, organize a análise em seções "
        "e detalhe conceitos, funcionamento, condições, exceções, consequências e exemplos possíveis."
        if extended
        else "Seja direto, mas explique o suficiente para a resposta ser útil durante uma sessão."
    )

    return f"""Responda em português usando exclusivamente as evidências abaixo.

Instruções:
- {depth_instruction}
- Comece com uma resposta direta à pergunta.
- Depois explique e sintetize as evidências relevantes em parágrafos claros. Relacione informações de páginas diferentes quando elas se complementarem.
- Inclua condições, exceções, consequências e distinções importantes somente quando constarem nas evidências.
- Coloque ao menos uma citação exata no formato [Livro, p. X] em cada parágrafo factual.
- Não copie longos trechos: explique com suas próprias palavras, preservando nomes e números das regras.
- Não invente regras, páginas, relações causais ou informações externas.
- Quando as evidências forem parciais ou conflitantes, declare claramente a limitação. Se não responderem à pergunta, diga: "Não encontrei evidência suficiente".

Pergunta: {question}

Evidências:
{chr(10).join(evidence)}
"""


def suggest_image_tags(
    client: OllamaClient,
    image_bytes: bytes,
    file_name: str,
    *,
    max_tags: int = 12,
) -> list[str]:
    prompt = f"""Você receberá UMA imagem de biblioteca de RPG com o nome de arquivo: {file_name}.

Tarefa:
- Gere tags curtas em português para facilitar busca local de imagens.
- Priorize conteúdo visual concreto: criatura, item, arma, armadura, cenário, símbolo, cor dominante, estilo (ex.: retrô, pixel, ilustração).
- Não invente termos que não estejam sugeridos na imagem.
- Retorne apenas uma linha com tags separadas por vírgula, em minúsculas, sem frases.
- Use no máximo {max_tags} tags.
"""
    raw = client.answer(prompt, images=[image_bytes])
    candidates = [
        " ".join(tag.strip().split()).casefold()
        for tag in re.split(r"[,\n;]+", raw)
    ]
    cleaned: list[str] = []
    for tag in candidates:
        if not tag:
            continue
        tag = re.sub(r"^[\-\d\.)\s]+", "", tag)
        tag = re.sub(r"[^\wÀ-ÿ\s-]", "", tag)
        tag = " ".join(tag.split())
        if tag:
            cleaned.append(tag)
    return list(dict.fromkeys(cleaned))[:max_tags]


def _http_transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result: Any = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise OllamaUnavailableError("O Ollama local não respondeu") from error
    if not isinstance(result, dict):
        raise OllamaUnavailableError("O Ollama retornou uma resposta inválida")
    return result


class OllamaClient:
    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 240.0,
        transport: Transport = _http_transport,
        system_prompt: str = RPG_RULES_PERSONA,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.system_prompt = system_prompt

    def answer(
        self, prompt: str, images: list[bytes] | None = None, *, extended: bool = False
    ) -> str:
        user_message: dict[str, object] = {"role": "user", "content": prompt}
        if images:
            user_message["images"] = [b64encode(image).decode("ascii") for image in images]
        response = self.transport(
            f"{self.base_url}/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    user_message,
                ],
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_ctx": 32768 if extended else 16384,
                    "num_predict": 4096 if extended else 2048,
                },
            },
            self.timeout,
        )
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaUnavailableError("O Ollama retornou uma resposta inválida")
        return str(message["content"]).strip()