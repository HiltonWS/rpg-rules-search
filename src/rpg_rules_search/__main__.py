import os

import uvicorn


def build_uvicorn_config() -> tuple[str, int, bool]:
    host = os.getenv("RPG_RULES_HOST", "127.0.0.1")
    port = int(os.getenv("RPG_RULES_PORT", "8765"))
    reload = os.getenv("RPG_RULES_RELOAD", "1") == "1"
    return host, port, reload


def main() -> None:
    host, port, reload = build_uvicorn_config()
    uvicorn.run("rpg_rules_search.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
