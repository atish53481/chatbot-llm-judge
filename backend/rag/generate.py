"""Generates a golden dataset from an uploaded reference document, using
DeepEval's own Synthesizer — not a custom retrieval pipeline. See
docs/superpowers/specs/2026-09-17-document-golden-generation-design.md.
"""
from __future__ import annotations

from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import ContextConstructionConfig

from backend.datasets import goldens as goldens_store
from backend.rag.embeddings import LocalSentenceEmbedder


def generate_goldens_from_document(
    path: str, theme: str, filename: str, judge, document_id: int | None = None
) -> int:
    """Runs the Synthesizer on one document and saves what it produces as
    goldens under theme, tagged with their source document. Returns the
    count of goldens created. Raises on any Synthesizer/parsing failure —
    the caller (the /api/documents endpoint) is responsible for catching it
    and recording a document status of "error"."""
    synthesizer = Synthesizer(model=judge)
    # critic_model must be set explicitly: ContextConstructionConfig resolves
    # it in __post_init__ (construction time), before Synthesizer ever gets a
    # chance to backfill a None with its own model — an unset critic_model
    # otherwise silently tries to build a default OpenAI model and needs
    # OPENAI_API_KEY, defeating the "reuse the judge, no second key" goal.
    config = ContextConstructionConfig(embedder=LocalSentenceEmbedder(), critic_model=judge)
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
            source_document_id=document_id,
        )
    return len(generated)
