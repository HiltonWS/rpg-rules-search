---
name: validate-arquivo-arcano
description: "Validate Arquivo Arcano changes. Use after implementing or debugging Python, API, synchronization, database, Ollama, MCP, or frontend behavior."
argument-hint: "Optional focused pytest path"
---

# Validate Arquivo Arcano

1. Identify the narrowest pytest target for the changed behavior.
2. Run it with `.venv/bin/python -m pytest -q <target>`.
3. For frontend work, exercise the affected flow in the running app at desktop and mobile widths.
4. Run `.venv/bin/python -m pytest -q`.
5. Run `.venv/bin/python -m ruff check src tests`.
6. Report the exact pass count and any remaining warning. Do not fix unrelated failures without scope.