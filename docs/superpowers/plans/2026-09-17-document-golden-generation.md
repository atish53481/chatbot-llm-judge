# Document → Golden Dataset Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload a reference/requirement document (PDF, TXT, DOCX, MD/MDX) in the sidebar and have DeepEval's own `Synthesizer` generate a golden dataset (question/expected_answer/context) from it, landing in the same golden-answer store the sidebar already edits.

**Architecture:** A new `backend/rag/` package wraps DeepEval's `Synthesizer` and a local, offline `sentence-transformers` embedder (a `DeepEvalBaseEmbeddingModel` subclass, same wrapper pattern as `backend/judges/judge.py`). A new SQLite `documents` table tracks uploads/status; raw files live on disk under `backend/datasets/documents/<theme>/`. Three new FastAPI endpoints (`POST/GET /api/documents`, `DELETE /api/documents/{id}`) drive it; a new sidebar section uploads a file and shows results. Generated goldens are saved via the existing `goldens_store.add_golden()`, tagged `source="synthesized"`.

**Tech Stack:** FastAPI, SQLite (existing), DeepEval `Synthesizer` + `ContextConstructionConfig`, `sentence-transformers` (local embedder), `langchain-community`/`pypdf`/`docx2txt` (DeepEval's own document loaders), `python-multipart` (FastAPI file upload support).

**Spec:** `docs/superpowers/specs/2026-09-17-document-golden-generation-design.md`

## Global Constraints

- Supported document types: **PDF, TXT, DOCX, MD, MARKDOWN, MDX only** — reject anything else with a clear 400, never a stack trace.
- Embedder is **local and offline** (`sentence-transformers`, model `all-MiniLM-L6-v2`) — no embedding API key, ever.
- Generation LLM **reuses the existing judge** (`build_judge()`) — no second paid key for generation either.
- Generated goldens are saved via the **existing** `goldens_store.add_golden()` — same file, same shape, plus `source`/`source_document`. No new golden storage mechanism.
- `POST /api/documents` is **synchronous/blocking**, matching `/api/run` — no background job queue.
- No live per-question RAG retrieval, no vector database — out of scope per spec.
- **Disk space:** `sentence-transformers` + its `torch` dependency need real space to install (this machine had only ~195MB free on 2026-09-17, which was insufficient) — verify free space before Task 1's install step.

---

### Task 1: Dependencies + local embedder

**Files:**
- Modify: `requirements.txt`
- Create: `backend/rag/__init__.py` (empty)
- Create: `backend/rag/embeddings.py`
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Produces: `backend.rag.embeddings.LocalSentenceEmbedder` — a `DeepEvalBaseEmbeddingModel` subclass with `__init__(self, model_name: str = "all-MiniLM-L6-v2")`, `embed_text(text: str) -> list[float]`, `embed_texts(texts: list[str]) -> list[list[float]]`, `a_embed_text`/`a_embed_texts` (async equivalents), `get_model_name() -> str`.

- [ ] **Step 1: Add the new dependencies to requirements.txt**

Append to `requirements.txt`:
```
python-multipart>=0.0.9
langchain-community>=0.3
pypdf>=4.0
docx2txt>=0.8
sentence-transformers>=3.0
```

- [ ] **Step 2: Install them (check disk space first)**

Run: `df -h /c 2>/dev/null || powershell -Command "Get-Volume -DriveLetter C | Select-Object SizeRemaining"`
If free space is under ~2GB, stop and free space before continuing — `sentence-transformers`'s `torch` dependency will otherwise fail mid-install with `OSError: No space left on device`.

Run: `.venv/Scripts/python.exe -m pip install -r requirements.txt`
Expected: completes without error.

- [ ] **Step 3: Write the failing test**

Create `tests/test_embeddings.py`:
```python
import asyncio
from unittest.mock import MagicMock, patch

import numpy as np


def test_embed_text_returns_list_of_floats():
    from backend.rag.embeddings import LocalSentenceEmbedder

    with patch("backend.rag.embeddings.SentenceTransformer") as MockST:
        MockST.return_value.encode.return_value = np.array([0.1, 0.2, 0.3])
        embedder = LocalSentenceEmbedder()
        result = embedder.embed_text("hello")

    assert result == [0.1, 0.2, 0.3]


def test_embed_texts_returns_list_of_lists():
    from backend.rag.embeddings import LocalSentenceEmbedder

    with patch("backend.rag.embeddings.SentenceTransformer") as MockST:
        MockST.return_value.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])
        embedder = LocalSentenceEmbedder()
        result = embedder.embed_texts(["a", "b"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]


def test_get_model_name_returns_configured_name():
    from backend.rag.embeddings import LocalSentenceEmbedder

    with patch("backend.rag.embeddings.SentenceTransformer"):
        embedder = LocalSentenceEmbedder(model_name="all-MiniLM-L6-v2")

    assert embedder.get_model_name() == "all-MiniLM-L6-v2"


def test_a_embed_text_matches_sync_result():
    from backend.rag.embeddings import LocalSentenceEmbedder

    with patch("backend.rag.embeddings.SentenceTransformer") as MockST:
        MockST.return_value.encode.return_value = np.array([0.5, 0.6])
        embedder = LocalSentenceEmbedder()
        result = asyncio.run(embedder.a_embed_text("hello"))

    assert result == [0.5, 0.6]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rag'`

- [ ] **Step 5: Create the package and implementation**

Create `backend/rag/__init__.py` (empty file).

Create `backend/rag/embeddings.py`:
```python
"""Local, offline embedder for document chunking during golden-dataset
generation (see backend/rag/generate.py). Wraps sentence-transformers
directly in-process — no network call, no second API key — mirroring how
backend/judges/judge.py wraps DeepEval's LocalModel for the judge LLM.
"""
from __future__ import annotations

import asyncio

from deepeval.models.base_model import DeepEvalBaseEmbeddingModel
from sentence_transformers import SentenceTransformer

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class LocalSentenceEmbedder(DeepEvalBaseEmbeddingModel):
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        super().__init__(model_name)

    def load_model(self):
        return SentenceTransformer(self.name)

    def embed_text(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()

    async def a_embed_text(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_text, text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts).tolist()

    async def a_embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_texts, texts)

    def get_model_name(self) -> str:
        return self.name
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeddings.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt backend/rag/__init__.py backend/rag/embeddings.py tests/test_embeddings.py
git commit -m "feat: add local offline embedder for document golden generation"
```

---

### Task 2: Golden provenance fields

**Files:**
- Modify: `backend/datasets/goldens.py`
- Test: `tests/test_goldens.py`

**Interfaces:**
- Consumes: none new.
- Produces: `goldens_store.add_golden(theme, question, expected_answer, context=None, categories=None, source="manual", source_document=None) -> dict` — every returned/stored row now always has `source` and `source_document` keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_goldens.py`:
```python
def test_add_golden_defaults_source_to_manual(tmp_path, monkeypatch):
    from backend.datasets import goldens

    path = tmp_path / "goldens.json"
    path.write_text("[]")
    monkeypatch.setattr(goldens, "GOLDENS_PATH", str(path))

    row = goldens.add_golden("t", "q", "a")

    assert row["source"] == "manual"
    assert row["source_document"] is None


def test_add_golden_records_synthesized_source(tmp_path, monkeypatch):
    from backend.datasets import goldens

    path = tmp_path / "goldens.json"
    path.write_text("[]")
    monkeypatch.setattr(goldens, "GOLDENS_PATH", str(path))

    row = goldens.add_golden(
        "t", "q", "a", source="synthesized", source_document="policy.pdf"
    )

    assert row["source"] == "synthesized"
    assert row["source_document"] == "policy.pdf"
```

(If `tests/test_goldens.py` uses a different fixture pattern already, e.g. the `fixed_goldens` autouse fixture from `tests/conftest.py`, use that file's existing style instead of `monkeypatch` directly — check the top of the file before adding.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goldens.py -v -k source`
Expected: FAIL with `KeyError: 'source'`

- [ ] **Step 3: Implement**

In `backend/datasets/goldens.py`, replace the `add_golden` function:
```python
def add_golden(
    theme: str,
    question: str,
    expected_answer: str,
    context: list[str] | None = None,
    categories: list[str] | None = None,
    source: str = "manual",
    source_document: str | None = None,
) -> dict:
    with _LOCK:
        rows = _read_all()
        new_row = {
            "id": f"g_{uuid.uuid4().hex[:8]}",
            "theme": theme,
            "question": question,
            "expected_answer": expected_answer,
            "context": context or [],
            "categories": categories or [],
            "source": source,
            "source_document": source_document,
        }
        rows.append(new_row)
        _write_all(rows)
        return new_row
```

`update_golden` is unchanged — it only overwrites the keys it's given (`theme`, `question`, `expected_answer`, `context`, `categories`), so editing a synthesized golden through the existing sidebar form leaves its `source`/`source_document` tag intact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goldens.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/datasets/goldens.py tests/test_goldens.py
git commit -m "feat: tag goldens with their source (manual vs synthesized)"
```

---

### Task 3: `documents` table + storage CRUD

**Files:**
- Modify: `backend/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `storage.add_document(conn, theme, filename) -> int`, `storage.set_document_status(conn, document_id, status, error=None) -> None`, `storage.get_document(conn, document_id) -> dict | None`, `storage.list_documents(conn, theme=None) -> list[dict]`, `storage.delete_document(conn, document_id) -> None`. Row shape: `{id, theme, filename, uploaded_at, status, error}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_storage.py`:
```python
def test_documents_table_created(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "documents" in tables


def test_add_and_get_document(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    doc_id = storage.add_document(conn, "general_support", "policy.pdf")
    row = storage.get_document(conn, doc_id)
    assert row["theme"] == "general_support"
    assert row["filename"] == "policy.pdf"
    assert row["status"] == "processing"
    assert row["error"] is None


def test_set_document_status(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    doc_id = storage.add_document(conn, "t", "f.txt")
    storage.set_document_status(conn, doc_id, "ready")
    assert storage.get_document(conn, doc_id)["status"] == "ready"
    storage.set_document_status(conn, doc_id, "error", "boom")
    row = storage.get_document(conn, doc_id)
    assert row["status"] == "error"
    assert row["error"] == "boom"


def test_list_documents_filters_by_theme(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    storage.add_document(conn, "a", "f1.txt")
    storage.add_document(conn, "b", "f2.txt")
    assert len(storage.list_documents(conn, "a")) == 1
    assert len(storage.list_documents(conn)) == 2


def test_delete_document(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    doc_id = storage.add_document(conn, "t", "f.txt")
    storage.delete_document(conn, doc_id)
    assert storage.get_document(conn, doc_id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_storage.py -v -k document`
Expected: FAIL with `AttributeError: module 'backend.storage' has no attribute 'add_document'`

- [ ] **Step 3: Implement**

In `backend/storage.py`, add the import at the top (alongside the existing `import json`, `import sqlite3`, `import threading`):
```python
from datetime import datetime, timezone
```

In `init_db`, add the table right after the `runs` index line (after `conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_target_metric ...")`, before the `meta` table line):
```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                theme TEXT NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            )
            """
        )
```

At the end of `backend/storage.py`, add:
```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_document(conn: sqlite3.Connection, theme: str, filename: str) -> int:
    with _LOCK:
        cur = conn.execute(
            "INSERT INTO documents (theme, filename, uploaded_at, status, error) VALUES (?, ?, ?, ?, ?)",
            (theme, filename, _now_iso(), "processing", None),
        )
        conn.commit()
        return cur.lastrowid


def set_document_status(
    conn: sqlite3.Connection, document_id: int, status: str, error: str | None = None
) -> None:
    with _LOCK:
        conn.execute(
            "UPDATE documents SET status = ?, error = ? WHERE id = ?",
            (status, error, document_id),
        )
        conn.commit()


def get_document(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    with _LOCK:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return None if row is None else dict(row)


def list_documents(conn: sqlite3.Connection, theme: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        if theme is None:
            rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE theme = ? ORDER BY id DESC", (theme,)
            ).fetchall()
    return [dict(r) for r in rows]


def delete_document(conn: sqlite3.Connection, document_id: int) -> None:
    with _LOCK:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_storage.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/storage.py tests/test_storage.py
git commit -m "feat: add documents table for uploaded reference documents"
```

---

### Task 4: Synthesizer wrapper (`backend/rag/generate.py`)

**Files:**
- Create: `backend/rag/generate.py`
- Test: `tests/test_rag_generate.py`

**Interfaces:**
- Consumes: `backend.rag.embeddings.LocalSentenceEmbedder` (Task 1), `backend.datasets.goldens.add_golden` (Task 2, with `source`/`source_document`).
- Produces: `generate.generate_goldens_from_document(path: str, theme: str, filename: str, judge) -> int` — returns the count of goldens created; raises on failure (caller in Task 5 catches it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_rag_generate.py`:
```python
from unittest.mock import MagicMock, patch


def test_generate_goldens_from_document_saves_goldens(tmp_path, monkeypatch):
    from backend.datasets import goldens as goldens_store

    goldens_path = tmp_path / "goldens.json"
    goldens_path.write_text("[]")
    monkeypatch.setattr(goldens_store, "GOLDENS_PATH", str(goldens_path))

    from backend.rag import generate

    fake_golden = MagicMock(
        input="What is the refund window?",
        expected_output="7 days.",
        context=["Refunds must be requested within 7 days."],
    )
    with patch("backend.rag.generate.Synthesizer") as MockSynthesizer, \
         patch("backend.rag.generate.LocalSentenceEmbedder"):
        MockSynthesizer.return_value.generate_goldens_from_docs.return_value = [fake_golden]
        count = generate.generate_goldens_from_document(
            "doc.pdf", "general_support", "doc.pdf", judge=MagicMock()
        )

    assert count == 1
    saved = goldens_store.load_goldens(theme="general_support")
    assert len(saved) == 1
    assert saved[0]["question"] == "What is the refund window?"
    assert saved[0]["expected_answer"] == "7 days."
    assert saved[0]["context"] == ["Refunds must be requested within 7 days."]
    assert saved[0]["source"] == "synthesized"
    assert saved[0]["source_document"] == "doc.pdf"


def test_generate_goldens_handles_missing_expected_output(tmp_path, monkeypatch):
    from backend.datasets import goldens as goldens_store

    goldens_path = tmp_path / "goldens.json"
    goldens_path.write_text("[]")
    monkeypatch.setattr(goldens_store, "GOLDENS_PATH", str(goldens_path))

    from backend.rag import generate

    fake_golden = MagicMock(input="Q?", expected_output=None, context=None)
    with patch("backend.rag.generate.Synthesizer") as MockSynthesizer, \
         patch("backend.rag.generate.LocalSentenceEmbedder"):
        MockSynthesizer.return_value.generate_goldens_from_docs.return_value = [fake_golden]
        generate.generate_goldens_from_document("doc.txt", "t", "doc.txt", judge=MagicMock())

    saved = goldens_store.load_goldens(theme="t")
    assert saved[0]["expected_answer"] == ""
    assert saved[0]["context"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rag_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rag.generate'`

- [ ] **Step 3: Implement**

Create `backend/rag/generate.py`:
```python
"""Generates a golden dataset from an uploaded reference document, using
DeepEval's own Synthesizer — not a custom retrieval pipeline. See
docs/superpowers/specs/2026-09-17-document-golden-generation-design.md.
"""
from __future__ import annotations

from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import ContextConstructionConfig

from backend.datasets import goldens as goldens_store
from backend.rag.embeddings import LocalSentenceEmbedder


def generate_goldens_from_document(path: str, theme: str, filename: str, judge) -> int:
    """Runs the Synthesizer on one document and saves what it produces as
    goldens under theme, tagged with their source document. Returns the
    count of goldens created. Raises on any Synthesizer/parsing failure —
    the caller (the /api/documents endpoint) is responsible for catching it
    and recording a document status of "error"."""
    synthesizer = Synthesizer(model=judge)
    config = ContextConstructionConfig(embedder=LocalSentenceEmbedder())
    generated = synthesizer.generate_goldens_from_docs(
        document_paths=[path],
        context_construction_config=config,
    )
    for golden in generated:
        goldens_store.add_golden(
            theme=theme,
            question=golden.input,
            expected_answer=golden.expected_output or "",
            context=golden.context or [],
            categories=[],
            source="synthesized",
            source_document=filename,
        )
    return len(generated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rag_generate.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/rag/generate.py tests/test_rag_generate.py
git commit -m "feat: generate goldens from a document via DeepEval Synthesizer"
```

---

### Task 5: `/api/documents` endpoints

**Files:**
- Modify: `backend/dashboard/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `storage.add_document/get_document/list_documents/delete_document/set_document_status` (Task 3), `generate.generate_goldens_from_document` (Task 4), existing `build_judge()`.
- Produces: `POST /api/documents` (multipart `theme`, `file`) → `{id, theme, filename, uploaded_at, status, error, goldens_created}`; `GET /api/documents?theme=` → `list[dict]`; `DELETE /api/documents/{id}` → `{"deleted": id}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py` (uses the existing `app_module`/`client` fixtures already defined at the top of this file):
```python
from unittest.mock import patch


def test_upload_document_creates_goldens(app_module, client):
    with patch.object(app_module, "generate_goldens_from_document", return_value=3) as mock_gen:
        response = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"Refunds within 7 days.", "text/plain")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["goldens_created"] == 3
    assert body["status"] == "ready"
    assert body["filename"] == "policy.txt"
    mock_gen.assert_called_once()


def test_upload_document_rejects_unsupported_extension(app_module, client):
    response = client.post(
        "/api/documents",
        data={"theme": "general_support"},
        files={"file": ("slides.pptx", b"fake", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_document_reports_generation_error(app_module, client):
    with patch.object(
        app_module, "generate_goldens_from_document", side_effect=RuntimeError("model unavailable")
    ):
        response = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"text", "text/plain")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "model unavailable" in body["error"]


def test_list_and_delete_documents(app_module, client):
    with patch.object(app_module, "generate_goldens_from_document", return_value=1):
        created = client.post(
            "/api/documents",
            data={"theme": "general_support"},
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
    listing = client.get("/api/documents?theme=general_support").json()
    assert any(d["id"] == created["id"] for d in listing)
    response = client.delete(f"/api/documents/{created['id']}")
    assert response.status_code == 200
    assert client.get("/api/documents?theme=general_support").json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -v -k document`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement**

In `backend/dashboard/app.py`, add these imports near the top (alongside the existing `from fastapi import FastAPI, HTTPException`):
```python
import shutil
from pathlib import Path

from fastapi import File, Form, UploadFile
```

And alongside the other `from backend...` imports:
```python
from backend.rag.generate import generate_goldens_from_document
```

Near the top-level constants (alongside `TARGET_TYPES`), add:
```python
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".docx", ".md", ".markdown", ".mdx"}
DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "datasets" / "documents"
```

After the existing `/api/targets` routes (right after `api_delete_target`), add the three new routes:
```python
@app.post("/api/documents")
def api_upload_document(theme: str = Form(...), file: UploadFile = File(...)):
    try:
        judge = build_judge()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    safe_filename = Path(file.filename or "document").name
    extension = Path(safe_filename).suffix.lower()
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {extension!r}: supported types are "
            f"{', '.join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))}",
        )
    document_id = storage.add_document(_conn, theme, safe_filename)
    theme_dir = DOCUMENTS_DIR / theme
    theme_dir.mkdir(parents=True, exist_ok=True)
    dest = theme_dir / f"{document_id}_{safe_filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        created = generate_goldens_from_document(str(dest), theme, safe_filename, judge)
        storage.set_document_status(_conn, document_id, "ready")
    except Exception as e:  # noqa: BLE001 - any generation failure is reported, not a 500
        storage.set_document_status(_conn, document_id, "error", f"{type(e).__name__}: {e}")
        return {**storage.get_document(_conn, document_id), "goldens_created": 0}
    return {**storage.get_document(_conn, document_id), "goldens_created": created}


@app.get("/api/documents")
def api_list_documents(theme: str | None = None):
    return storage.list_documents(_conn, theme)


@app.delete("/api/documents/{document_id}")
def api_delete_document(document_id: int):
    if storage.get_document(_conn, document_id) is None:
        raise HTTPException(status_code=404, detail="document not found")
    storage.delete_document(_conn, document_id)
    return {"deleted": document_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -v -k document`
Expected: 4 passed

- [ ] **Step 5: Run the full non-live suite to confirm no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/ -k "not live" -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/dashboard/app.py tests/test_app.py
git commit -m "feat: add /api/documents endpoints for document-to-golden generation"
```

---

### Task 6: Sidebar UI — upload a document, generate goldens

**Files:**
- Modify: `ChatbotExtension/sidebar/sidebar.html`
- Modify: `ChatbotExtension/sidebar/sidebar.js`
- Modify: `ChatbotExtension/sidebar/sidebar.css`

**Interfaces:**
- Consumes: `POST /api/documents`, `GET /api/documents?theme=`, `DELETE /api/documents/{id}` (Task 5); existing `themeOf`, `currentTarget`, `el`, `confirmButton`, `api`, `BACKEND_URL` helpers from `lib/ui.js`/`lib/api.js`.

This project has no JavaScript test runner (no `package.json` test script exists) — verification here is `node --check` for syntax plus a manual click-through in the loaded extension, matching how every other UI change in this project has been verified.

- [ ] **Step 1: Add the markup**

In `ChatbotExtension/sidebar/sidebar.html`, insert a new section right after the closing `</section>` of the "Golden answers" section (before the "Judge" section):
```html
  <section class="card" aria-labelledby="documents-heading">
    <h2 id="documents-heading">Generate from a document</h2>
    <p class="muted small">
      Upload a reference or requirement document (PDF, TXT, DOCX, MD) and DeepEval
      generates a golden set from it into the golden answers above.
    </p>
    <form id="document-form" class="stack">
      <input id="document-file" type="file" accept=".pdf,.txt,.docx,.md,.markdown,.mdx" required>
      <div class="row">
        <button type="submit" id="document-submit" class="primary">Generate goldens</button>
        <span id="document-form-error" class="status-error small" role="alert"></span>
      </div>
    </form>
    <ul id="document-list" class="document-list"></ul>
  </section>
```

- [ ] **Step 2: Add the behavior**

In `ChatbotExtension/sidebar/sidebar.js`, add near `loadGoldens`:
```js
function documentRow(doc) {
  const icon = doc.status === "ready" ? "✓" : doc.status === "error" ? "!" : "…";
  return el(
    "li",
    { className: `document-row status-${doc.status}` },
    el("span", {}, `${icon} ${doc.filename}`),
    doc.status === "error" ? el("span", { className: "status-error small" }, doc.error) : null,
    confirmButton("Delete", `Delete document: ${doc.filename}`, async () => {
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
      await loadDocuments();
    }),
  );
}

async function loadDocuments() {
  const target = currentTarget();
  const list = $("document-list");
  if (!target) {
    list.replaceChildren();
    return;
  }
  const docs = await api(`/api/documents?theme=${encodeURIComponent(themeOf(target))}`);
  list.replaceChildren(...docs.map(documentRow));
}
```

Add the submit handler near the other form handlers (e.g. after the `golden-form` submit handler):
```js
$("document-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("document-form-error").textContent = "";
  const target = currentTarget();
  if (!target) return;
  const fileInput = $("document-file");
  const file = fileInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("theme", themeOf(target));
  formData.append("file", file);
  $("document-submit").disabled = true;
  $("document-submit").textContent = "Generating… (real LLM calls, can take a while)";
  try {
    const response = await fetch(`${BACKEND_URL}/api/documents`, { method: "POST", body: formData });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Upload failed.");
    if (result.status === "error") {
      $("document-form-error").textContent = result.error;
    } else {
      fileInput.value = "";
      await loadGoldens();
    }
    await loadDocuments();
  } catch (error) {
    $("document-form-error").textContent = error.message;
  } finally {
    $("document-submit").disabled = false;
    $("document-submit").textContent = "Generate goldens";
  }
});
```

In `onTargetChanged`, add `loadDocuments()` alongside the existing `loadGoldens()`/`renderLatest()` call:
```js
  await Promise.all([loadGoldens(), renderLatest(), loadDocuments()]);
```
(Replace the existing `await Promise.all([loadGoldens(), renderLatest()]);` line with this one.)

- [ ] **Step 3: Add the styling**

In `ChatbotExtension/sidebar/sidebar.css`, add:
```css
.document-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}

.document-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--grid);
}

.document-row span:first-child {
  flex: 1;
  overflow-wrap: anywhere;
}
```

- [ ] **Step 4: Syntax-check**

Run: `node --check ChatbotExtension/sidebar/sidebar.js`
Expected: no output (success)

- [ ] **Step 5: Manual verification**

Reload the extension, open the side panel, pick a chatbot, expand "Generate from a document," upload a small `.txt` file with a couple of factual sentences, click "Generate goldens." Confirm: the button shows the running state, a document row appears with a status icon, and on success new rows appear in the "Golden answers" list above tagged as synthesized.

- [ ] **Step 6: Commit**

```bash
git add ChatbotExtension/sidebar/sidebar.html ChatbotExtension/sidebar/sidebar.js ChatbotExtension/sidebar/sidebar.css
git commit -m "feat: add document-to-golden-dataset upload UI to the sidebar"
```
