from rpg_rules_search.database import SearchResult
from rpg_rules_search.ollama import (
    OllamaClient,
    build_evidence_prompt,
    build_expanded_retrieval_query,
    build_retrieval_query,
)


def test_retrieval_query_removes_portuguese_stopwords() -> None:
    query = build_retrieval_query("O que é profundezas na homebrew?")

    assert query == '"profundezas" AND "homebrew"'


def test_expanded_retrieval_query_combines_original_and_suggested_terms() -> None:
    query = build_expanded_retrieval_query(
        "Como recuperar vida?", ["cura", "pontos de vida", "restauração"]
    )

    assert query == '"recuperar" OR "vida" OR "cura" OR "pontos vida" OR "restauração"'


def test_evidence_prompt_contains_page_citations_and_rejects_unsupported_answers() -> None:
    prompt = build_evidence_prompt(
        "Como funciona ataque flamejante?",
        [
            SearchResult(
                document_id=1,
                document_name="Manual.pdf",
                page_index=12,
                printed_page="10",
                snippet="Ataque flamejante causa <mark>dois dados</mark> de dano.",
                score=-1.0,
            )
        ],
    )

    assert "[Manual.pdf, p. 10]" in prompt
    assert "Não encontrei evidência suficiente" in prompt
    assert "<mark>" not in prompt
    assert "sintetize" in prompt


def test_evidence_prompt_uses_relevant_passage_from_full_page_text() -> None:
    result = SearchResult(
        document_id=1,
        document_name="Manual.pdf",
        page_index=12,
        printed_page="10",
        snippet="trecho curto",
        score=-1.0,
    )
    page_text = "Introdução sem relação. " * 100 + "Profundezas é o elemento da pressão."

    prompt = build_evidence_prompt(
        "O que é profundezas?", [result], {(1, 12): page_text}
    )

    assert "Profundezas é o elemento da pressão." in prompt
    assert "trecho curto" not in prompt


def test_ollama_client_uses_local_chat_api_without_streaming() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured.update(url=url, payload=payload, timeout=timeout)
        return {"message": {"content": "Use dois dados. [Manual.pdf, p. 10]"}}

    client = OllamaClient(model="gemma3:latest", transport=transport)
    answer = client.answer("Pergunta e evidências", images=[b"page image"])

    assert answer == "Use dois dados. [Manual.pdf, p. 10]"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["stream"] is False  # type: ignore[index]
    assert captured["payload"]["messages"][0]["role"] == "system"  # type: ignore[index]
    assert "especialista" in captured["payload"]["messages"][0]["content"]  # type: ignore[index]
    assert captured["payload"]["messages"][1]["images"] == ["cGFnZSBpbWFnZQ=="]  # type: ignore[index]


    def test_client_accepts_custom_system_prompt() -> None:
        captured: dict[str, object] = {}

        def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
            captured["payload"] = payload
            return {"message": {"content": "ok"}}

        OllamaClient(transport=transport, system_prompt="Revisor local").answer("Revise")

        messages = captured["payload"]["messages"]  # type: ignore[index]
        assert messages[0]["content"] == "Revisor local"  # type: ignore[index]
    assert captured["payload"]["options"] == {  # type: ignore[index]
        "temperature": 0,
        "num_ctx": 16384,
        "num_predict": 2048,
    }


def test_extended_answer_uses_larger_context_and_output_budget() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured["payload"] = payload
        return {"message": {"content": "Resposta detalhada"}}

    client = OllamaClient(transport=transport)
    client.answer("Pergunta e evidências", extended=True)

    assert captured["payload"]["options"] == {  # type: ignore[index]
        "temperature": 0,
        "num_ctx": 32768,
        "num_predict": 4096,
    }


def test_text_only_client_does_not_send_images() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured["payload"] = payload
        return {"message": {"content": "Resposta textual"}}

    client = OllamaClient(transport=transport, accepts_images=False)
    client.answer("Pergunta e evidências", images=[b"page image"])

    messages = captured["payload"]["messages"]  # type: ignore[index]
    assert "images" not in messages[1]


def test_ollama_client_suggests_short_retrieval_terms() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        captured.update(payload=payload, timeout=timeout)
        return {"message": {"content": "cura, pontos de vida, restauração, cura"}}

    client = OllamaClient(transport=transport)

    assert client.suggest_retrieval_terms("Como recuperar vida?") == [
        "cura",
        "pontos de vida",
        "restauração",
    ]
    assert captured["timeout"] == 30.0
    assert captured["payload"]["options"]["num_predict"] == 96  # type: ignore[index]