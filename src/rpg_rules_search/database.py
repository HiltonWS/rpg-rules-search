from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_LINE_BREAK_HYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])")
INDEX_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    drive_file_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    content_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    index_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL,
    printed_page TEXT,
    threat_category TEXT,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    UNIQUE(document_id, page_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(
    document_name,
    text
);

CREATE VIRTUAL TABLE IF NOT EXISTS page_search_vocab USING fts5vocab(page_search, 'row');

CREATE TABLE IF NOT EXISTS image_assets (
    id INTEGER PRIMARY KEY,
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL UNIQUE,
    width INTEGER,
    height INTEGER,
    status TEXT NOT NULL DEFAULT 'ready',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS image_tags (
    id INTEGER PRIMARY KEY,
    image_id INTEGER NOT NULL REFERENCES image_assets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ai',
    confidence REAL,
    UNIQUE(image_id, tag)
);

CREATE TABLE IF NOT EXISTS image_source_files (
    source_file_id TEXT PRIMARY KEY,
    image_id INTEGER NOT NULL REFERENCES image_assets(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS image_tag_search USING fts5(
    tag,
    content='image_tags',
    content_rowid='id'
);

CREATE TABLE IF NOT EXISTS query_activity (
    kind TEXT NOT NULL CHECK(kind IN ('search', 'question')),
    normalized_query TEXT NOT NULL,
    display_query TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(kind, normalized_query)
);

CREATE TRIGGER IF NOT EXISTS pages_after_insert AFTER INSERT ON pages BEGIN
    INSERT INTO page_search(rowid, document_name, text)
    SELECT NEW.id, documents.name, NEW.normalized_text
    FROM documents WHERE documents.id = NEW.document_id;
END;

CREATE TRIGGER IF NOT EXISTS pages_after_delete AFTER DELETE ON pages BEGIN
    DELETE FROM page_search WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS pages_after_update AFTER UPDATE OF normalized_text ON pages BEGIN
    DELETE FROM page_search WHERE rowid = OLD.id;
    INSERT INTO page_search(rowid, document_name, text)
    SELECT NEW.id, documents.name, NEW.normalized_text
    FROM documents WHERE documents.id = NEW.document_id;
END;

CREATE TRIGGER IF NOT EXISTS image_tags_after_insert AFTER INSERT ON image_tags BEGIN
    INSERT INTO image_tag_search(rowid, tag) VALUES (NEW.id, NEW.tag);
END;

CREATE TRIGGER IF NOT EXISTS image_tags_after_delete AFTER DELETE ON image_tags BEGIN
    DELETE FROM image_tag_search WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS image_tags_after_update AFTER UPDATE OF tag ON image_tags BEGIN
    DELETE FROM image_tag_search WHERE rowid = OLD.id;
    INSERT INTO image_tag_search(rowid, tag) VALUES (NEW.id, NEW.tag);
END;
"""


@dataclass(frozen=True)
class SearchResult:
    document_id: int
    document_name: str
    page_index: int
    printed_page: str | None
    snippet: str
    score: float


@dataclass(frozen=True)
class ThreatSearchResult:
    document_id: int
    document_name: str
    page_index: int
    printed_page: str | None
    threat_category: str
    snippet: str


@dataclass(frozen=True)
class ImageSearchResult:
    image_id: int
    file_name: str
    content_type: str
    width: int | None
    height: int | None
    tags: list[str]
    score: float | None


@dataclass(frozen=True)
class DocumentSyncState:
    id: int
    drive_file_id: str
    modified_time: str
    status: str
    index_version: int


@dataclass(frozen=True)
class ImageSourceState:
    source_file_id: str
    image_id: int
    file_name: str
    modified_time: str
    content_hash: str


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return " ".join(_LINE_BREAK_HYPHEN.sub("", normalized).split())


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    document_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()
    }
    if "error_message" not in document_columns:
        connection.execute("ALTER TABLE documents ADD COLUMN error_message TEXT")
    if "index_version" not in document_columns:
        connection.execute(
            "ALTER TABLE documents ADD COLUMN index_version INTEGER NOT NULL DEFAULT 1"
        )
    page_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(pages)").fetchall()
    }
    if "threat_category" not in page_columns:
        connection.execute("ALTER TABLE pages ADD COLUMN threat_category TEXT")
    rows = connection.execute("SELECT id, raw_text, normalized_text FROM pages").fetchall()
    updates = [
        (normalized_text, row["id"])
        for row in rows
        if (normalized_text := normalize_search_text(str(row["raw_text"])))
        != row["normalized_text"]
    ]
    if updates:
        with connection:
            connection.executemany(
                "UPDATE pages SET normalized_text = ? WHERE id = ?",
                updates,
            )


def upsert_document(
    connection: sqlite3.Connection,
    *,
    drive_file_id: str,
    name: str,
    mime_type: str,
    modified_time: str,
) -> int:
    connection.execute(
        """
        INSERT INTO documents(drive_file_id, name, mime_type, modified_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(drive_file_id) DO UPDATE SET
            name = excluded.name,
            mime_type = excluded.mime_type,
            modified_time = excluded.modified_time,
            updated_at = CURRENT_TIMESTAMP
        """,
        (drive_file_id, name, mime_type, modified_time),
    )
    row = connection.execute(
        "SELECT id FROM documents WHERE drive_file_id = ?", (drive_file_id,)
    ).fetchone()
    return int(row["id"])


def document_sync_states(connection: sqlite3.Connection) -> list[DocumentSyncState]:
    rows = connection.execute(
        """
        SELECT id, drive_file_id, modified_time, status, index_version
        FROM documents
        WHERE drive_file_id NOT LIKE 'manual:%'
        """
    ).fetchall()
    return [DocumentSyncState(**dict(row)) for row in rows]


def set_document_status(
    connection: sqlite3.Connection,
    document_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE documents
            SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, error_message, document_id),
        )


def ready_document_with_hash(
    connection: sqlite3.Connection,
    content_hash: str,
    *,
    excluding_document_id: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM documents
        WHERE content_hash = ? AND status = 'ready' AND id != ?
        ORDER BY id
        LIMIT 1
        """,
        (content_hash, excluding_document_id),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def set_document_content_hash(
    connection: sqlite3.Connection,
    document_id: int,
    content_hash: str,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE documents
            SET content_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (content_hash, document_id),
        )


def mark_document_duplicate(
    connection: sqlite3.Connection,
    document_id: int,
    content_hash: str,
) -> None:
    with connection:
        connection.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
        connection.execute(
            """
            UPDATE documents
            SET content_hash = ?, status = 'duplicate', error_message = NULL,
                index_version = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (content_hash, INDEX_VERSION, document_id),
        )


def remove_missing_drive_documents(
    connection: sqlite3.Connection,
    remote_file_ids: set[str],
) -> list[str]:
    missing = [
        state.drive_file_id
        for state in document_sync_states(connection)
        if state.drive_file_id not in remote_file_ids
    ]
    if missing:
        with connection:
            connection.executemany(
                "DELETE FROM documents WHERE drive_file_id = ?",
                [(file_id,) for file_id in missing],
            )
    return missing


def replace_pages(
    connection: sqlite3.Connection,
    document_id: int,
    pages: list[tuple[int, str | None, str]],
    *,
    threat_metadata: dict[int, str] | None = None,
) -> None:
    threat_metadata = threat_metadata or {}
    with connection:
        connection.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
        connection.executemany(
            """
            INSERT INTO pages(
                document_id, page_index, printed_page, threat_category, raw_text, normalized_text
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document_id,
                    page_index,
                    printed_page,
                    threat_metadata.get(page_index),
                    text,
                    normalize_search_text(text),
                )
                for page_index, printed_page, text in pages
            ],
        )
        connection.execute(
            """
            UPDATE documents
            SET status = 'ready', error_message = NULL, index_version = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (INDEX_VERSION, document_id),
        )


def load_page_texts(
    connection: sqlite3.Connection, results: list[SearchResult]
) -> dict[tuple[int, int], str]:
    if not results:
        return {}
    keys = list(dict.fromkeys((result.document_id, result.page_index) for result in results))
    placeholders = ", ".join("(?, ?)" for _ in keys)
    parameters = [value for key in keys for value in key]
    rows = connection.execute(
        f"""
        SELECT document_id, page_index, raw_text
        FROM pages
        WHERE (document_id, page_index) IN ({placeholders})
        """,
        parameters,
    ).fetchall()
    return {
        (int(row["document_id"]), int(row["page_index"])): str(row["raw_text"])
        for row in rows
    }


def record_query_activity(connection: sqlite3.Connection, kind: str, query: str) -> None:
    display_query = " ".join(query.split())
    normalized_query = display_query.casefold()
    with connection:
        connection.execute(
            """
            INSERT INTO query_activity(kind, normalized_query, display_query)
            VALUES (?, ?, ?)
            ON CONFLICT(kind, normalized_query) DO UPDATE SET
                display_query = excluded.display_query,
                count = query_activity.count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (kind, normalized_query, display_query),
        )


def popular_queries(
    connection: sqlite3.Connection, kind: str, limit: int = 6
) -> list[tuple[str, int]]:
    rows = connection.execute(
        """
        SELECT display_query, count
        FROM query_activity
        WHERE kind = ?
        ORDER BY count DESC, updated_at DESC
        LIMIT ?
        """,
        (kind, limit),
    ).fetchall()
    return [(str(row["display_query"]), int(row["count"])) for row in rows]


def _search_rows(
    connection: sqlite3.Connection, query: str, limit: int
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            documents.id AS document_id,
            documents.name AS document_name,
            pages.page_index,
            pages.printed_page,
            snippet(page_search, 1, '<mark>', '</mark>', '...', 18) AS snippet,
            bm25(page_search, 3.0, 1.0) AS score
        FROM page_search
        JOIN pages ON pages.id = page_search.rowid
        JOIN documents ON documents.id = pages.document_id
        WHERE page_search MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _query_terms(query: str) -> list[str]:
    operators = {"and", "or", "not"}
    return [
        term
        for term in re.findall(r"[\wÀ-ÿ]+", query.lower(), flags=re.UNICODE)
        if term not in operators
    ]


def _fuzzy_query(connection: sqlite3.Connection, query: str) -> str | None:
    terms = _query_terms(query)
    if not terms:
        return None

    groups: list[str] = []
    for term in terms:
        maximum_distance = 2 if len(term) >= 10 else 1
        rows = connection.execute(
            """
            SELECT term FROM page_search_vocab
            WHERE substr(term, 1, 1) = ? AND length(term) BETWEEN ? AND ?
            LIMIT 5000
            """,
            (term[0], max(1, len(term) - maximum_distance), len(term) + maximum_distance),
        ).fetchall()
        alternatives = sorted(
            {
                str(row["term"])
                for row in rows
                if str(row["term"]) != term
                and _edit_distance(term, str(row["term"])) <= maximum_distance
            },
            key=lambda candidate: (_edit_distance(term, candidate), candidate),
        )[:3]
        quoted = [term, *alternatives]
        groups.append("(" + " OR ".join(f'\"{word}\"' for word in quoted) + ")")
    return " AND ".join(groups)


def _partial_query(query: str) -> str | None:
    terms = _query_terms(query)
    if len(terms) < 2:
        return None
    return " OR ".join(f'"{term}"' for term in terms)


def search(connection: sqlite3.Connection, query: str, limit: int = 20) -> list[SearchResult]:
    rows = _search_rows(connection, query, limit)
    if not rows and (expanded_query := _fuzzy_query(connection, query)) is not None:
        rows = _search_rows(connection, expanded_query, limit)
    if not rows and (partial_query := _partial_query(query)):
        rows = _search_rows(connection, partial_query, limit)
    return [SearchResult(**dict(row)) for row in rows]


def search_threats(
    connection: sqlite3.Connection,
    query: str | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[ThreatSearchResult]:
    filters = ["pages.threat_category IS NOT NULL"]
    parameters: list[str | int] = []
    if query:
        filters.append("page_search MATCH ?")
        parameters.append(query)
    if category:
        filters.append("pages.threat_category = ?")
        parameters.append(category)

    snippet_expression = (
        "snippet(page_search, 1, '<mark>', '</mark>', '...', 24)"
        if query
        else "substr(pages.raw_text, 1, 320)"
    )
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT
            documents.id AS document_id,
            documents.name AS document_name,
            pages.page_index,
            pages.printed_page,
            pages.threat_category,
            {snippet_expression} AS snippet
        FROM pages
        JOIN documents ON documents.id = pages.document_id
        JOIN page_search ON page_search.rowid = pages.id
        WHERE {' AND '.join(filters)}
        ORDER BY documents.name, pages.page_index
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [ThreatSearchResult(**dict(row)) for row in rows]


def upsert_image_asset(
    connection: sqlite3.Connection,
    *,
    file_name: str,
    content_type: str,
    content_hash: str,
    storage_path: str,
    width: int | None,
    height: int | None,
) -> tuple[int, bool]:
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO image_assets(
                file_name, content_type, content_hash, storage_path, width, height
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (file_name, content_type, content_hash, storage_path, width, height),
        )
    created = cursor.rowcount > 0
    row = connection.execute(
        "SELECT id FROM image_assets WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Imagem não foi persistida")
    image_id = int(row["id"])
    if not created:
        with connection:
            connection.execute(
                """
                UPDATE image_assets
                SET file_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (file_name, image_id),
            )
    return image_id, created


def get_image_asset(connection: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, file_name, content_type, storage_path, width, height, status, error_message
        FROM image_assets
        WHERE id = ?
        """,
        (image_id,),
    ).fetchone()


def set_image_status(
    connection: sqlite3.Connection,
    image_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE image_assets
            SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, error_message, image_id),
        )


def replace_image_tags(
    connection: sqlite3.Connection,
    image_id: int,
    tags: list[str],
    *,
    source: str = "ai",
) -> list[str]:
    normalized_tags = [" ".join(tag.split()).casefold() for tag in tags]
    unique_tags = [tag for tag in dict.fromkeys(normalized_tags) if tag]
    with connection:
        connection.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
        if unique_tags:
            connection.executemany(
                """
                INSERT INTO image_tags(image_id, tag, source)
                VALUES (?, ?, ?)
                """,
                [(image_id, tag, source) for tag in unique_tags],
            )
        connection.execute(
            """
            UPDATE image_assets
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (image_id,),
        )
    return unique_tags


def image_tags_for_ids(
    connection: sqlite3.Connection,
    image_ids: list[int],
) -> dict[int, list[str]]:
    if not image_ids:
        return {}
    placeholders = ", ".join("?" for _ in image_ids)
    rows = connection.execute(
        f"""
        SELECT image_id, tag
        FROM image_tags
        WHERE image_id IN ({placeholders})
        ORDER BY tag
        """,
        image_ids,
    ).fetchall()
    grouped: dict[int, list[str]] = {image_id: [] for image_id in image_ids}
    for row in rows:
        grouped[int(row["image_id"])].append(str(row["tag"]))
    return grouped


def search_images(
    connection: sqlite3.Connection,
    query: str | None = None,
    limit: int = 24,
) -> list[ImageSearchResult]:
    if query:
        ranked_rows = connection.execute(
            """
            SELECT
                image_assets.id AS image_id,
                image_assets.file_name,
                image_assets.content_type,
                image_assets.width,
                image_assets.height,
                bm25(image_tag_search) AS score
            FROM image_tag_search
            JOIN image_tags ON image_tags.id = image_tag_search.rowid
            JOIN image_assets ON image_assets.id = image_tags.image_id
            WHERE image_assets.status = 'ready' AND image_tag_search MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit * 6),
        ).fetchall()
        rows: list[sqlite3.Row] = []
        seen_image_ids: set[int] = set()
        for row in ranked_rows:
            image_id = int(row["image_id"])
            if image_id in seen_image_ids:
                continue
            seen_image_ids.add(image_id)
            rows.append(row)
            if len(rows) >= limit:
                break
    else:
        rows = connection.execute(
            """
            SELECT
                image_assets.id AS image_id,
                image_assets.file_name,
                image_assets.content_type,
                image_assets.width,
                image_assets.height,
                NULL AS score
            FROM image_assets
            WHERE image_assets.status = 'ready'
            ORDER BY image_assets.updated_at DESC, image_assets.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    image_ids = [int(row["image_id"]) for row in rows]
    tags_by_id = image_tags_for_ids(connection, image_ids)
    return [
        ImageSearchResult(
            image_id=int(row["image_id"]),
            file_name=str(row["file_name"]),
            content_type=str(row["content_type"]),
            width=int(row["width"]) if row["width"] is not None else None,
            height=int(row["height"]) if row["height"] is not None else None,
            tags=tags_by_id.get(int(row["image_id"]), []),
            score=float(row["score"]) if row["score"] is not None else None,
        )
        for row in rows
    ]


def image_assets_without_tags(
    connection: sqlite3.Connection,
    limit: int = 500,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT image_assets.id, image_assets.file_name, image_assets.storage_path
        FROM image_assets
        LEFT JOIN image_tags ON image_tags.image_id = image_assets.id
        WHERE image_assets.status = 'ready' AND image_tags.id IS NULL
        ORDER BY image_assets.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def image_source_states(connection: sqlite3.Connection) -> list[ImageSourceState]:
    rows = connection.execute(
        """
        SELECT source_file_id, image_id, file_name, modified_time, content_hash
        FROM image_source_files
        """
    ).fetchall()
    return [ImageSourceState(**dict(row)) for row in rows]


def upsert_image_source_file(
    connection: sqlite3.Connection,
    *,
    source_file_id: str,
    image_id: int,
    file_name: str,
    modified_time: str,
    content_hash: str,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO image_source_files(
                source_file_id, image_id, file_name, modified_time, content_hash
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_file_id) DO UPDATE SET
                image_id = excluded.image_id,
                file_name = excluded.file_name,
                modified_time = excluded.modified_time,
                content_hash = excluded.content_hash,
                updated_at = CURRENT_TIMESTAMP
            """,
            (source_file_id, image_id, file_name, modified_time, content_hash),
        )


def remove_missing_image_source_files(
    connection: sqlite3.Connection,
    remote_file_ids: set[str],
) -> list[str]:
    missing = [
        state.source_file_id
        for state in image_source_states(connection)
        if state.source_file_id not in remote_file_ids
    ]
    if missing:
        with connection:
            connection.executemany(
                "DELETE FROM image_source_files WHERE source_file_id = ?",
                [(file_id,) for file_id in missing],
            )
    return missing


def delete_image_asset_if_unreferenced(
    connection: sqlite3.Connection,
    image_id: int,
) -> bool:
    with connection:
        cursor = connection.execute(
            """
            DELETE FROM image_assets
            WHERE id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM image_source_files WHERE image_id = image_assets.id
              )
            """,
            (image_id,),
        )
    return cursor.rowcount > 0
