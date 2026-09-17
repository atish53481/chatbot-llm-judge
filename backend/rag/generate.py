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
