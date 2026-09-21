"""Rebuildable search projection; the authoritative store always supplies eligibility.

FTS contains pre-tokenized Chinese/English terms. Vectors are content-addressed and
partitioned by trusted owner and embedding revision. Neither table grants access.
"""

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from threading import RLock
from weakref import finalize

from .models import Memory
from .ports import ConflictError


class RetrievalProjection:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._finalizer = finalize(self, self._connection.close)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS projection_epochs (
                namespace TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS projection_docs (
                rowid INTEGER PRIMARY KEY, namespace TEXT NOT NULL, doc_key TEXT NOT NULL,
                digest TEXT NOT NULL, UNIQUE(namespace, doc_key));
            CREATE VIRTUAL TABLE IF NOT EXISTS projection_fts USING fts5(
                terms, tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS projection_vectors (
                namespace TEXT NOT NULL, provider TEXT NOT NULL, digest TEXT NOT NULL,
                vector TEXT NOT NULL, PRIMARY KEY(namespace, provider, digest));
            CREATE TEMP TABLE IF NOT EXISTS allowed_documents (doc_key TEXT PRIMARY KEY);
        """)
        self._connection.commit()

    @staticmethod
    def key(memory: Memory) -> str:
        return json.dumps([memory.memory_id, memory.version], ensure_ascii=False)

    @staticmethod
    def text(memory: Memory) -> str:
        return " ".join(
            [
                memory.content,
                memory.subject,
                memory.predicate,
                memory.value,
                *memory.keywords,
                *memory.hierarchy_path,
            ]
        )

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def generation(self, namespace: str) -> int:
        with self._lock:
            already_in_transaction = self._connection.in_transaction
            self._connection.execute(
                "INSERT OR IGNORE INTO projection_epochs(namespace,generation) VALUES (?,0)", (namespace,)
            )
            row = self._connection.execute(
                "SELECT generation FROM projection_epochs WHERE namespace=?", (namespace,)
            ).fetchone()
            if not already_in_transaction:
                self._connection.commit()
            return row[0] if row else 0

    def _check_generation(self, namespace: str, generation: int) -> None:
        if generation != self.generation(namespace):
            raise ConflictError("Retrieval projection invalidated during request; retry")

    def sync(self, namespace: str, memories: list[Memory], tokenize, generation: int) -> dict:
        """Synchronize a complete owner snapshot; tokenize only changed records."""
        with self._lock, self._connection:
            self._check_generation(namespace, generation)
            existing = {
                key: (rowid, digest)
                for rowid, key, digest in self._connection.execute(
                    "SELECT rowid, doc_key, digest FROM projection_docs WHERE namespace=?", (namespace,)
                )
            }
            changed = 0
            retained = set()
            for memory in memories:
                key, text = self.key(memory), self.text(memory)
                digest = self.digest(text)
                retained.add(key)
                previous = existing.get(key)
                if previous and previous[1] == digest:
                    continue
                if previous:
                    rowid = previous[0]
                    self._connection.execute("DELETE FROM projection_fts WHERE rowid=?", (rowid,))
                    self._connection.execute(
                        "UPDATE projection_docs SET digest=? WHERE rowid=?", (digest, rowid)
                    )
                else:
                    cursor = self._connection.execute(
                        "INSERT INTO projection_docs(namespace,doc_key,digest) VALUES (?,?,?)",
                        (namespace, key, digest),
                    )
                    rowid = cursor.lastrowid
                self._connection.execute(
                    "INSERT INTO projection_fts(rowid,terms) VALUES (?,?)",
                    (rowid, " ".join(sorted(tokenize(text)))),
                )
                existing[key] = (rowid, digest)
                changed += 1
            for key in existing.keys() - retained:
                rowid = existing[key][0]
                self._connection.execute("DELETE FROM projection_fts WHERE rowid=?", (rowid,))
                self._connection.execute("DELETE FROM projection_docs WHERE rowid=?", (rowid,))
            # Remove vectors for deleted/changed text even before a semantic query.
            valid_digests = {self.digest(memory.content) for memory in memories}
            stored = self._connection.execute(
                "SELECT DISTINCT digest FROM projection_vectors WHERE namespace=?", (namespace,)
            ).fetchall()
            self._connection.executemany(
                "DELETE FROM projection_vectors WHERE namespace=? AND digest=?",
                [(namespace, digest) for (digest,) in stored if digest not in valid_digests],
            )
            return {
                "documents": len(retained),
                "changed": changed,
                "removed": len(existing.keys() - retained),
            }

    def candidates(
        self, namespace: str, terms: set[str], eligible: list[Memory], limit: int, generation: int
    ) -> list[str]:
        if not terms or not eligible or limit <= 0:
            return []
        # Escape all input as literal FTS tokens; no user supplied MATCH operators.
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in sorted(terms))
        with self._lock, self._connection:
            self._check_generation(namespace, generation)
            self._connection.execute("DELETE FROM allowed_documents")
            self._connection.executemany(
                "INSERT OR IGNORE INTO allowed_documents(doc_key) VALUES (?)",
                [(self.key(m),) for m in eligible],
            )
            rows = self._connection.execute(
                """
                SELECT d.doc_key FROM projection_fts
                JOIN projection_docs d ON d.rowid=projection_fts.rowid
                JOIN allowed_documents a ON a.doc_key=d.doc_key
                WHERE projection_fts MATCH ? AND d.namespace=?
                ORDER BY bm25(projection_fts), d.doc_key LIMIT ?
            """,
                (expression, namespace, limit),
            ).fetchall()
            return [row[0] for row in rows]

    def vectors(
        self, namespace: str, provider: str, digests: list[str], generation: int
    ) -> dict[str, list[float]]:
        with self._lock:
            self._check_generation(namespace, generation)
            result = {}
            # Respect SQLite parameter limits independently of owner corpus size.
            for start in range(0, len(digests), 500):
                batch = digests[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                if not batch:
                    continue
                for digest, encoded in self._connection.execute(
                    f"SELECT digest,vector FROM projection_vectors WHERE namespace=? AND provider=? AND digest IN ({placeholders})",
                    [namespace, provider, *batch],
                ):
                    result[digest] = json.loads(encoded)
            return result

    def save_vectors(
        self, namespace: str, provider: str, vectors: dict[str, list[float]], generation: int
    ) -> None:
        dimensions = {len(vector) for vector in vectors.values()}
        if len(dimensions) > 1 or (dimensions and next(iter(dimensions)) == 0):
            raise ValueError("inconsistent projection vector dimensions")
        for vector in vectors.values():
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise ValueError("invalid projection vector")
        with self._lock, self._connection:
            self._check_generation(namespace, generation)
            self._connection.executemany(
                "INSERT OR REPLACE INTO projection_vectors VALUES (?,?,?,?)",
                [(namespace, provider, digest, json.dumps(vector)) for digest, vector in vectors.items()],
            )

    def clear(self, namespace: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO projection_epochs(namespace,generation) VALUES (?,1) ON CONFLICT(namespace) DO UPDATE SET generation=generation+1",
                (namespace,),
            )
            self._connection.execute(
                "DELETE FROM projection_fts WHERE rowid IN (SELECT rowid FROM projection_docs WHERE namespace=?)",
                (namespace,),
            )
            self._connection.execute("DELETE FROM projection_docs WHERE namespace=?", (namespace,))
            self._connection.execute("DELETE FROM projection_vectors WHERE namespace=?", (namespace,))

    def close(self) -> None:
        with self._lock:
            self._finalizer()

    def clear_owner(self, tenant: str, owner: str) -> None:
        """Remove every session projection belonging to one trusted principal."""
        with self._lock:
            namespaces = [
                row[0] for row in self._connection.execute("SELECT namespace FROM projection_epochs")
            ]
            for namespace in namespaces:
                try:
                    parts = json.loads(namespace)
                except (ValueError, TypeError):
                    continue
                if isinstance(parts, list) and parts[:2] == [tenant, owner]:
                    self.clear(namespace)

    def rebuild(self, namespace: str, memories: list[Memory], tokenize) -> dict:
        """Discard one owner's derived state and rebuild from its trusted snapshot."""
        with self._lock:
            self.clear(namespace)
            return self.sync(namespace, memories, tokenize, self.generation(namespace))
