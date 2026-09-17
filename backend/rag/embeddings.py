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
