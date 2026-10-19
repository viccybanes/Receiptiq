"""Opt-in, revision-checked local examples. Retrieval never changes receipt values."""

import http.client
import json
import math
import os

from .db import now

EMBEDDING_MODEL = "embeddinggemma:300m-qat-q4_0"
ALLOWED_EMBEDDING_MODELS = (EMBEDDING_MODEL,)
MAX_TEXT = 6000
MAX_VECTOR = 2048
MAX_ENTRIES = 10_000
TEXT_ITEM_FIELDS = ("description", "unit", "item_type")


def vector(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_VECTOR:
        raise ValueError("Invalid local embedding dimensions.")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
           or abs(n) > 1_000_000 for n in value):
        raise ValueError("Invalid local embedding values.")
    length = math.sqrt(sum(n * n for n in value))
    if not length:
        raise ValueError("Local embedding has no direction.")
    return [n / length for n in value]


class OllamaEmbedder:
    model = EMBEDDING_MODEL

    def embed(self, text):
        # A fixed loopback host prevents receipt text going to a remote model provider.
        port = int(os.environ.get("RECEIPTIQ_OLLAMA_PORT", "11435"))
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid local Ollama port.")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            connection.request("POST", "/api/embed", json.dumps({
                "model": self.model, "input": text[:MAX_TEXT], "dimensions": 256,
                "truncate": True, "keep_alive": "2m"}), {"Content-Type": "application/json"})
            response = connection.getresponse()
            content = response.read(256_001)
            if response.status != 200 or len(content) > 256_000:
                raise ValueError("Install the local embedding model before finding similar receipts.")
            result = json.loads(content)
            embeddings = result.get("embeddings") if isinstance(result, dict) else None
            if not isinstance(embeddings, list) or len(embeddings) != 1:
                raise ValueError("Invalid response from the local embedding model.")
            return vector(embeddings[0])
        finally:
            connection.close()


def eligible(receipt):
    return bool(receipt and receipt.get("retrieval_allowed") == 1 and receipt["status"] == "verified"
                and receipt["verified_revision"] == receipt["revision"])


def evidence(receipt):
    """Only names, units, and classifications are reusable examples; amounts are omitted."""
    data = receipt["data"]
    draft = receipt.get("evidence", {}).get("draft", {})
    corrections = []
    if draft and draft.get("supplier") != data["supplier"] and data["supplier"]:
        corrections.append({"field": "supplier", "before": str(draft.get("supplier", ""))[:500],
                            "after": data["supplier"][:500]})
    items = []
    draft_items = {item.get("id"): item for item in draft.get("items", [])}
    for position, item in enumerate(data["items"][:100]):
        items.append({"id": item["id"], **{field: item.get(field, "")[:1000] for field in TEXT_ITEM_FIELDS}})
        original = draft_items.get(item["id"], {})
        if not original and position < len(draft.get("items", [])):
            original = draft["items"][position]
        for field in TEXT_ITEM_FIELDS:
            if original and original.get(field, "") != item.get(field, "") and item.get(field):
                corrections.append({"field": f"items.{position}.{field}",
                                    "before": str(original.get(field, ""))[:1000], "after": item[field][:1000]})
    return {"receipt_id": receipt["id"], "revision": receipt["revision"], "vendor": data["supplier"][:500],
            "items": items, "corrections": corrections[:40],
            "citation": f"receipt:{receipt['id']}@{receipt['revision']}"}


def receipt_text(receipt):
    example = evidence(receipt)
    parts = ["Vendor: " + example["vendor"]]
    parts.extend("Item: " + " | ".join(item[field] for field in TEXT_ITEM_FIELDS) for item in example["items"])
    parts.extend(f"Correction {pair['field']}: {pair['before']} -> {pair['after']}"
                 for pair in example["corrections"])
    return "\n".join(parts)[:MAX_TEXT]


class Retrieval:
    def __init__(self, store, embedder=None):
        self.store = store
        self.embedder = embedder if embedder is not None else OllamaEmbedder()
        self.last_error = ""

    def _embed(self, text):
        embed = self.embedder if callable(self.embedder) else self.embedder.embed
        return vector(embed(text[:MAX_TEXT]))

    def _purge(self, receipt_id=None):
        scope = "receipt_id=? AND (" if receipt_id is not None else "("
        parameters = (receipt_id, EMBEDDING_MODEL) if receipt_id is not None else (EMBEDDING_MODEL,)
        self.store.execute(
            "DELETE FROM retrieval_entries WHERE " + scope + "embedding_model != ? OR NOT EXISTS("
            "SELECT 1 FROM receipts r WHERE r.id=retrieval_entries.receipt_id AND r.retrieval_allowed=1 "
            "AND r.status='verified' AND r.verified_revision=r.revision "
            "AND r.revision=retrieval_entries.verified_revision))", parameters)

    def index(self, receipt_id):
        self.last_error = ""
        self._purge(receipt_id)
        receipt = self.store.receipt(receipt_id)
        if not eligible(receipt):
            return False
        text = receipt_text(receipt)
        current = self.store.one("SELECT * FROM retrieval_entries WHERE receipt_id=?", (receipt_id,))
        if current and current["text"] == text:
            return True
        try:
            embedding = self._embed(text)
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as exc:
            self.last_error = "Local examples unavailable: " + str(exc)[:200]
            return False
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            live = connection.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not eligible(dict(live) if live else None) or live["revision"] != receipt["revision"]:
                connection.execute("DELETE FROM retrieval_entries WHERE receipt_id=?", (receipt_id,))
                return False
            count = connection.execute("SELECT COUNT(*) FROM retrieval_entries").fetchone()[0]
            exists = connection.execute("SELECT 1 FROM retrieval_entries WHERE receipt_id=?", (receipt_id,)).fetchone()
            if not exists and count >= MAX_ENTRIES:
                self.last_error = "Local example limit reached (10,000 receipts)."
                return False
            connection.execute(
                "INSERT INTO retrieval_entries VALUES(?,?,?,?,?,?) ON CONFLICT(receipt_id) DO UPDATE SET "
                "verified_revision=excluded.verified_revision,embedding_model=excluded.embedding_model,"
                "text=excluded.text,vector_json=excluded.vector_json,indexed_at=excluded.indexed_at",
                (receipt_id, receipt["revision"], EMBEDDING_MODEL, text, json.dumps(embedding), now()))
        return True

    def search(self, receipt_id, query=None, limit=5):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("Choose between 1 and 20 local examples.")
        if query is not None and not isinstance(query, str):
            raise ValueError("Search text must be a string.")
        self.last_error = ""
        self._purge()
        target = self.store.receipt(receipt_id)
        if not target:
            return []
        text = query[:MAX_TEXT] if query is not None else receipt_text(target)
        if not text.strip():
            return []
        rows = self.store.read(
            "SELECT e.* FROM retrieval_entries e JOIN receipts r ON r.id=e.receipt_id "
            "WHERE e.receipt_id!=? AND e.embedding_model=? AND r.retrieval_allowed=1 AND r.status='verified' "
            "AND r.verified_revision=r.revision AND e.verified_revision=r.revision ORDER BY e.receipt_id LIMIT ?",
            (receipt_id, EMBEDDING_MODEL, MAX_ENTRIES))
        if not rows:
            return []
        try:
            query_vector = self._embed(text)
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as exc:
            self.last_error = "Local examples unavailable: " + str(exc)[:200]
            return []
        # ponytail: linear cosine search is capped at 10k x 2048 values; use an ANN index above this ceiling.
        ranked = []
        for row in rows:
            if len(row["vector_json"]) > 128_000:
                continue
            try:
                candidate = vector(json.loads(row["vector_json"]))
            except (ValueError, TypeError):
                continue
            if len(candidate) == len(query_vector):
                ranked.append((sum(a * b for a, b in zip(query_vector, candidate)), row))
        results = []
        for score, row in sorted(ranked, key=lambda pair: (-pair[0], pair[1]["receipt_id"])):
            live = self.store.receipt(row["receipt_id"])
            if eligible(live) and live["revision"] == row["verified_revision"]:
                results.append({**evidence(live), "score": round(max(-1, min(1, score)), 6)})
                if len(results) == limit:
                    break
        return results

    def sync(self, max_receipts=MAX_ENTRIES):
        if isinstance(max_receipts, bool) or not isinstance(max_receipts, int) or not 1 <= max_receipts <= MAX_ENTRIES:
            raise ValueError("Sync between 1 and 10,000 receipts at a time.")
        self.last_error = ""
        self._purge()
        rows = self.store.read(
            "SELECT r.id FROM receipts r LEFT JOIN retrieval_entries e ON e.receipt_id=r.id "
            "WHERE r.retrieval_allowed=1 AND r.status='verified' AND r.verified_revision=r.revision "
            "AND e.receipt_id IS NULL ORDER BY r.created_at,r.id LIMIT ?", (max_receipts,))
        indexed = 0
        for row in rows:
            if self.index(row["id"]):
                indexed += 1
            elif self.last_error:
                break  # An offline runtime must not turn a batch into thousands of timed-out requests.
        pending = self.store.one(
            "SELECT COUNT(*) AS count FROM receipts r LEFT JOIN retrieval_entries e ON e.receipt_id=r.id "
            "WHERE r.retrieval_allowed=1 AND r.status='verified' AND r.verified_revision=r.revision "
            "AND e.receipt_id IS NULL")["count"]
        return {"indexed": indexed, "pending": pending, "error": self.last_error}

    def rebuild(self, max_receipts=MAX_ENTRIES):
        if isinstance(max_receipts, bool) or not isinstance(max_receipts, int) or not 1 <= max_receipts <= MAX_ENTRIES:
            raise ValueError("Rebuild between 1 and 10,000 receipts at a time.")
        self.store.execute("DELETE FROM retrieval_entries")
        return self.sync(max_receipts)
