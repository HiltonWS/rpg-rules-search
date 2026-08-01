---
name: review-with-local-ollama
description: "Ask the project-scoped local Ollama MCP for a second review of Arquivo Arcano code, tests, architecture, privacy, or implementation options. Use only for developing this repository."
argument-hint: "Task and up to 12 workspace-relative text files"
---

# Review With Local Ollama

1. Select only files needed to understand the development question, up to 12 and 120 KB total.
2. Call the `arquivo-arcano-ollama` tool `consultar_ollama_do_projeto` with a concrete task and workspace-relative paths.
3. Treat the response as a second opinion, not verified fact. Check every claim against code, tests, and runtime evidence.
4. Never pass credentials, `.env`, user PDFs/images, OAuth data, databases, `.git`, `.venv`, or paths outside the workspace.
5. The MCP is for project development. Use the application retrieval flow, not this skill, for RPG rule questions.