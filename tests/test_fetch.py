from unittest.mock import MagicMock, patch

import pytest

from backend.rag.fetch import MAX_BYTES, fetch_page_text, html_to_text

PAGE = """<html><head><title>Help</title><style>.x{color:red}</style>
<script>var secret = 1;</script></head>
<body><nav>Home | Pricing</nav><header>Acme Bank</header>
<h1>Card help</h1><p>Lost cards are blocked within 5 minutes.</p>
<ul><li>Call 1800 000</li><li>Or use the app.</li></ul>
<footer>(c) Acme</footer></body></html>"""


def test_html_to_text_keeps_content_and_drops_chrome():
    text = html_to_text(PAGE)
    assert "Card help" in text
    assert "Lost cards are blocked within 5 minutes." in text
    assert "Call 1800 000" in text
    for dropped in ("secret", "color:red", "Home | Pricing", "Acme Bank", "(c) Acme"):
        assert dropped not in text
    assert "\n" in text  # block elements become line breaks


def _response(body: bytes, content_type="text/html; charset=utf-8", status=200, url="https://help.example/card"):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.url = url
    resp.headers = {"Content-Type": content_type}
    resp.encoding = "utf-8"
    resp.iter_content.return_value = [body]
    return resp


def _long_page():
    return ("<p>" + "Lost cards are blocked within five minutes of the call. " * 10 + "</p>").encode()


@patch("backend.rag.fetch.requests.get")
def test_fetch_returns_text_and_final_url(mock_get):
    mock_get.return_value = _response(_long_page(), url="https://help.example/final")
    text, final_url = fetch_page_text("https://help.example/card")
    assert "Lost cards are blocked" in text
    assert final_url == "https://help.example/final"


@pytest.mark.parametrize("url", ["ftp://help.example/x", "file:///C:/secrets.txt", "help.example", ""])
def test_fetch_rejects_non_http_urls(url):
    with pytest.raises(ValueError, match="http"):
        fetch_page_text(url)


@patch("backend.rag.fetch.requests.get")
def test_fetch_rejects_non_text_content(mock_get):
    mock_get.return_value = _response(b"%PDF-1.7", content_type="application/pdf")
    with pytest.raises(ValueError, match="not a web page"):
        fetch_page_text("https://help.example/file.pdf")


@patch("backend.rag.fetch.requests.get")
def test_fetch_rejects_http_errors(mock_get):
    mock_get.return_value = _response(b"nope", status=404)
    with pytest.raises(ValueError, match="404"):
        fetch_page_text("https://help.example/missing")


@patch("backend.rag.fetch.requests.get")
def test_fetch_rejects_oversized_pages(mock_get):
    resp = _response(b"")
    resp.iter_content.return_value = [b"x" * (MAX_BYTES // 2), b"x" * (MAX_BYTES // 2), b"x"]
    mock_get.return_value = resp
    with pytest.raises(ValueError, match="larger than"):
        fetch_page_text("https://help.example/huge")


@patch("backend.rag.fetch.requests.get")
def test_fetch_rejects_pages_without_readable_text(mock_get):
    mock_get.return_value = _response(b"<html><body><div id='root'></div><script>app()</script></body></html>")
    with pytest.raises(ValueError, match="JavaScript"):
        fetch_page_text("https://help.example/spa")
