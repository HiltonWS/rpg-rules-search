---
name: "Python Local-First"
description: "Use when editing the Arquivo Arcano Python backend, ingestion, synchronization, SQLite, Ollama, MCP, or tests. Preserves local-first privacy and focused validation."
applyTo: "src/**/*.py, tests/**/*.py"
---

# Python Local-First

- Keep public APIs typed and preserve existing endpoint response shapes.
- Use parametrized SQLite queries and retain document, page, raw text, and citations.
- Treat each synchronized file independently; one failure must not stop the remaining files.
- Never read or transmit credentials, OAuth tokens, library files, or the user database during development.
- Use `.venv/bin/python`. Run the narrowest relevant pytest target immediately after editing, then the full suite.
- When changing indexed text or metadata, decide explicitly whether `INDEX_VERSION` must increase.