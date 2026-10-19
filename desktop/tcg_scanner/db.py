import json
import sqlite3
import time
import os
import tempfile
from pathlib import Path
from filelock import FileLock, Timeout


SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
 language TEXT NOT NULL, set_id TEXT NOT NULL, name TEXT, series_id TEXT,
 release_date TEXT, raw_json TEXT NOT NULL, PRIMARY KEY(language, set_id)
);
CREATE TABLE IF NOT EXISTS cards (
 id INTEGER PRIMARY KEY,
 language TEXT NOT NULL, card_id TEXT NOT NULL, set_id TEXT NOT NULL,
 name TEXT, collector_number TEXT, rarity TEXT, image_url TEXT,
 variants_json TEXT, variants_detailed_json TEXT, raw_json TEXT,
 brief_json TEXT NOT NULL, metadata_updated_at REAL,
 metadata_status TEXT NOT NULL DEFAULT 'pending', metadata_error TEXT,
 download_status TEXT NOT NULL DEFAULT 'pending', download_error TEXT,
 local_image_path TEXT, downloaded_url TEXT, sha256 TEXT,
 width INTEGER, height INTEGER, byte_size INTEGER,
 downloaded_at REAL, last_attempt_at REAL, download_attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(language, card_id),
 FOREIGN KEY(language, set_id) REFERENCES sets(language, set_id)
);
CREATE INDEX IF NOT EXISTS cards_by_set ON cards(language, set_id);
CREATE INDEX IF NOT EXISTS cards_by_status ON cards(download_status);
CREATE TABLE IF NOT EXISTS http_cache (
 url TEXT PRIMARY KEY, body TEXT NOT NULL, etag TEXT, last_modified TEXT, checked_at REAL NOT NULL
);

"""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    """Used only by the coordinator thread; workers never share a connection."""
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(path) + ".lock")
        try:
            self.lock.acquire(timeout=0)
        except Timeout as exc:
            raise ValueError("Another tcg-scanner command is using this data directory") from exc
        try:
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("Database was created by a newer version")
            if version == 1:
                # SQLite backup includes committed WAL contents. Never replace a prior backup.
                backup_path = path.with_name(path.name + ".v1.bak")
                if not backup_path.exists():
                    fd, temporary = tempfile.mkstemp(prefix=backup_path.name + ".", suffix=".part",
                                                       dir=path.parent)
                    os.close(fd)
                    try:
                        backup = sqlite3.connect(temporary)
                        try:
                            self.conn.backup(backup)
                        finally:
                            backup.close()
                        # Windows FlushFileBuffers requires a writable handle.
                        with open(temporary, "rb+") as saved:
                            os.fsync(saved.fileno())
                        os.replace(temporary, backup_path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = FULL")
            self.conn.executescript("BEGIN IMMEDIATE;" + SCHEMA + EMBEDDING_SCHEMA +
                                    "PRAGMA user_version = 2; COMMIT;")
        except BaseException:
            if hasattr(self, "conn"):
                self.conn.close()
            self.lock.release()
            raise

    def close(self):
        self.conn.close()
        self.lock.release()

    def cached_json(self, client, url, ttl_hours, force=False):
        row = self.conn.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()
        if row and not force and time.time() - row["checked_at"] < ttl_hours * 3600:
            return json.loads(row["body"])
        headers = {}
        if row:
            if row["etag"]:
                headers["If-None-Match"] = row["etag"]
            if row["last_modified"]:
                headers["If-Modified-Since"] = row["last_modified"]
        body, response_headers, status = client.json(url, headers)
        if status == 304:
            if row is None:
                raise ValueError("304 without cached response")
            body = json.loads(row["body"])
        h = {k.lower(): v for k, v in response_headers.items()}
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?)",
                (url, dumps(body), h.get("etag", row["etag"] if status == 304 else None),
                 h.get("last-modified", row["last_modified"] if status == 304 else None), time.time()))
        return body

    def save_set(self, language, data):
        with self.conn:
            self.conn.execute("""INSERT INTO sets VALUES (?,?,?,?,?,?)
                ON CONFLICT(language,set_id) DO UPDATE SET name=excluded.name,
                series_id=excluded.series_id, release_date=excluded.release_date,
                raw_json=excluded.raw_json""", (language, data["id"], data.get("name"),
                (data.get("serie") or {}).get("id"), data.get("releaseDate"), dumps(data)))

    def card(self, language, card_id):
        row = self.conn.execute("SELECT * FROM cards WHERE language=? AND card_id=?",
                                (language, card_id)).fetchone()
        return dict(row) if row else None

    def seed_card(self, language, set_id, brief):
        with self.conn:
            self.conn.execute("""INSERT INTO cards(language,card_id,set_id,name,collector_number,brief_json)
                VALUES (?,?,?,?,?,?) ON CONFLICT(language,card_id) DO NOTHING""",
                (language, brief["id"], set_id, brief.get("name"), brief.get("localId"), dumps(brief)))

    def update_card(self, language, card_id, values):
        with self.conn:
            self.conn.execute("UPDATE cards SET " + ",".join(f"{k}=?" for k in values)
                + " WHERE language=? AND card_id=?", (*values.values(), language, card_id))

    def status(self):
        return [dict(row) for row in self.conn.execute("""SELECT language, metadata_status,
            download_status, COUNT(*) AS cards FROM cards
            GROUP BY language, metadata_status, download_status""")]


# Additive v1 -> v2 migration: original cards, IDs, paths and metadata are untouched.
EMBEDDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_models (
 model_key TEXT PRIMARY KEY, model_name TEXT NOT NULL, requested_revision TEXT NOT NULL,
 spec_json TEXT NOT NULL, dimension INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
 embedding_id INTEGER PRIMARY KEY,
 card_pk INTEGER NOT NULL REFERENCES cards(id),
 model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
 status TEXT NOT NULL, vector BLOB, dimension INTEGER,
 source_url TEXT, source_sha256 TEXT, source_kind TEXT,
 attempted_at REAL, processed_at REAL, error TEXT,
 UNIQUE(card_pk, model_key)
);
CREATE INDEX IF NOT EXISTS embeddings_by_model ON embeddings(model_key, status);
"""
