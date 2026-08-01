from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rpg_rules_search.database import (
    connect,
    image_assets_without_tags,
    initialize,
    replace_image_tags,
)
from rpg_rules_search.ollama import DEFAULT_OLLAMA_MODEL, OllamaClient, suggest_image_tags


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aplica auto-tag local com Ollama nas imagens sem tags da biblioteca.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home() / ".local" / "share" / "rpg-rules-search" / "library.sqlite3",
        help="Caminho do SQLite da biblioteca local.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Número máximo de imagens.")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Modelo local do Ollama.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:11434",
        help="Base URL do Ollama local.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    connection = connect(args.database)
    initialize(connection)
    ollama = OllamaClient(model=args.model, base_url=args.url)

    rows = image_assets_without_tags(connection, limit=max(1, args.limit))
    tagged = 0
    for row in rows:
        try:
            image_bytes = Path(str(row["storage_path"])).read_bytes()
            tags = suggest_image_tags(ollama, image_bytes, str(row["file_name"]))
            replace_image_tags(connection, int(row["id"]), tags, source="ai-script")
            tagged += 1
            print(f"[ok] {row['file_name']}: {', '.join(tags) if tags else '(sem tags)'}")
        except OSError as error:
            print(f"[erro] {row['file_name']}: {error}")
        except Exception as error:  # noqa: BLE001
            print(f"[erro] {row['file_name']}: {error}")

    print(f"Concluído. Processadas: {len(rows)} | Tagueadas: {tagged}")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
