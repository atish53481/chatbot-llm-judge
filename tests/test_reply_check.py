import pytest

from backend.targets.reply_check import service_message


@pytest.mark.parametrize("reply", [
    "I've reached my question limit for today – please check back tomorrow.",
    "Please log in to continue chatting.",
    "You need to sign in first.",
    "Too many requests. Try again in a minute.",
    "Your session has expired. Refresh the page.",
    "Rate limit exceeded.",
    "Monthly quota exceeded for this bot.",
    "Something went wrong. Please try again later.",
    "Service temporarily unavailable",
    "Internal Server Error",
])
def test_flags_short_service_messages(reply):
    assert service_message(reply)


def test_flags_an_empty_reply():
    assert service_message("   ") == "the chatbot sent an empty reply"


@pytest.mark.parametrize("reply", [
    "Refunds are processed within 7 business days.",
    "I can't help with that, but I can answer questions about your order.",
    "I'm an AI assistant. What can I help you with?",
    # A real answer that happens to explain limits is long enough to be kept.
    "Our API has a rate limit of 100 requests per minute per key. " * 6,
])
def test_keeps_real_answers(reply):
    assert service_message(reply) is None
