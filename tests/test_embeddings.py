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
