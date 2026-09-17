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
         patch("backend.rag.generate.LocalSentenceEmbedder"), \
         patch("backend.rag.generate.ContextConstructionConfig"):
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
         patch("backend.rag.generate.LocalSentenceEmbedder"), \
         patch("backend.rag.generate.ContextConstructionConfig"):
        MockSynthesizer.return_value.generate_goldens_from_docs.return_value = [fake_golden]
        generate.generate_goldens_from_document("doc.txt", "t", "doc.txt", judge=MagicMock())

    saved = goldens_store.load_goldens(theme="t")
    assert saved[0]["expected_answer"] == ""
    assert saved[0]["context"] == []
