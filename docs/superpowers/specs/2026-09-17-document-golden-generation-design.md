# Document → Golden Dataset Generation (DeepEval Synthesizer)

**Date:** 2026-09-17
**Status:** proposed, pending review

## Problem

Golden answer sets (question / expected_answer / context, per theme)
currently require hand-authoring every row in the sidebar. When a reference
or requirement document already exists for the chatbot's domain, the user
wants to point the plugin at that document instead and have a golden dataset
generated from it — using DeepEval's own generation machinery
(`deepeval.synthesizer.Synthesizer`), not a custom pipeline, per the explicit
ask to "implement the DeepEval framework."

This is **not** live per-question RAG retrieval layered on top of judging.
It is a one-time (per document) golden-dataset generation step: upload a doc
→ get golden rows → they land in the same editable golden store that manual
goldens already use.

## Scope

**In:**
- Upload a reference document (PDF / TXT / DOCX / MD / MDX) scoped to a theme.
- Generate goldens from it via `Synthesizer.generate_goldens_from_docs()`.
- A local, offline embedding backend for the Synthesizer's internal chunking
  step, so no second API key is required (matches the project's existing
  zero-setup Sample-chatbot philosophy).
- Reuse the existing judge model (`GroqJudge` / whatever `JUDGE_*` env vars
  configure) as the Synthesizer's generation LLM — no second paid key there
  either.
- Generated goldens are ordinary goldens: same `goldens.json` store, same
  sidebar list/edit/delete UI, tagged `source: "synthesized"` +
  `source_document` for traceability and quick review.

**Out (explicitly, for now):**
- Live per-question RAG retrieval at judge time. Nothing persists as a
  queryable vector index after generation; the Synthesizer's embedding use is
  transient, internal to one generation call.
- OCR / scanned-PDF support, and non-text formats (pptx, xlsx, html, json,
  images). DeepEval's document loaders (LangChain-backed) only cover
  `.pdf .txt .docx .md .markdown .mdx` — stating this plainly rather than
  promising "any file type."
- A dedicated vector database. Not needed since nothing is retrieved later.
- A background job queue. Generation blocks the request, the same way
  `/api/run` already blocks for a golden sweep — consistent with the existing
  synchronous style, and simpler to reason about for a single-user local tool.

## Architecture

- **`backend/rag/embeddings.py`** — a `DeepEvalBaseEmbeddingModel` subclass
  wrapping a local `sentence-transformers` model (`all-MiniLM-L6-v2`),
  mirroring how `backend/judges/judge.py` wraps DeepEval's `LocalModel`.
  Downloaded once on first use, cached in the venv's model cache.
- **`backend/rag/generate.py`** — `generate_goldens_from_document(path, theme,
  judge) -> list[dict]`. Builds `Synthesizer(model=judge)`, calls
  `generate_goldens_from_docs([path], context_construction_config=
  ContextConstructionConfig(embedder=LocalEmbedder()))`, maps the returned
  DeepEval `Golden` objects into this project's existing golden dict shape
  (`question`, `expected_answer`, `context`, `categories: []`, plus new
  `source` / `source_document` fields).
- **New SQLite table `documents`** in `judge.db` (id, theme, filename,
  uploaded_at, status, error) — tracks uploads and generation status/errors.
  Raw file bytes live on disk under
  `backend/datasets/documents/<theme>/<id>_<filename>`, consistent with
  `goldens.json` being a local file store rather than a DB blob column.
- **`backend/datasets/goldens.py`** — `add_golden` gains optional `source`
  (default `"manual"`) and `source_document` fields. Backward compatible;
  existing rows without these fields still load fine.

## API

- `POST /api/documents` (multipart: `theme`, `file`) — saves the file,
  creates a `documents` row, runs generation synchronously, and returns
  `{id, filename, theme, status: "ready"|"error", goldens_created, error}`.
  Blocking, like `/api/run` — this is a real LLM operation and can take a
  while; the UI needs the same "still working" affordance already built for
  metric runs.
- `GET /api/documents?theme=` — list uploaded documents + status for a theme.
- `DELETE /api/documents/{id}` — removes the file + DB row. Does **not**
  cascade-delete the goldens it generated — they're independent, editable
  rows now (matches how deleting a target doesn't cascade-delete its run
  history: an established pattern in this codebase, not a new inconsistency).

## UI (sidebar)

New collapsible section, "Generate from a document," near Golden Answers:
- File picker (`accept=".pdf,.txt,.docx,.md,.markdown,.mdx"`) + "Generate
  goldens" button.
- Running state reuses the pulsing-bar pattern already built for metric runs
  (real LLM call, real wait).
- On success, new goldens simply appear in the existing golden list (already
  live via `loadGoldens()`), each with a small "synthesized" badge + source
  filename in its tooltip. Editable/deletable exactly like manual goldens —
  synthesis output is a starting point to review, not a black box.

## Dependencies added

- DeepEval's synthesizer path pulls in `langchain`, `langchain-community`,
  `pypdf`, `docx2txt` — a real, one-time install-size increase.
- `sentence-transformers` (+ `torch` transitively) for the local embedder —
  the heaviest addition: a meaningful download (embedding model + PyTorch)
  and first-use load latency. This is the main cost of "no API key required,"
  and is called out here so it's a known tradeoff, not a surprise.

## Risks / things to keep honest about

- PyTorch is a heavy dependency for a small local tool; accepted knowingly
  because "no second API key" was the explicit requirement.
- Synthesized golden quality is unreviewed by default — the UI treats it as
  a fast starting point (editable immediately), not authoritative ground
  truth, and that framing should stay explicit in any UI copy.
- Generation is a real LLM-cost operation, same category as a golden sweep —
  needs the same visible-progress treatment already built for metric runs,
  or it will read as "stuck" the same way slow metric runs did earlier.

## Explicitly deferred (not built now, noted for later if needed)

- Swapping the local embedder for a remote one (e.g. OpenAI
  `text-embedding-3-small`) via env var, if embedding/chunk quality ever
  matters more than zero-setup. The embedder is isolated behind one small
  module (`backend/rag/embeddings.py`), so this is a contained change later,
  not a redesign.
- True live RAG retrieval at judge time (auto-fetch relevant chunks per
  question, feed as `context`/`retrieval_context`) — a different, larger
  feature than "generate a golden set from a doc." Worth its own design pass
  if wanted later.
