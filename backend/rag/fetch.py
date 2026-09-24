"""Fetches a help / FAQ page and turns it into plain text for golden generation.

Only static HTML (or plain text) is readable: pages that build their content
with JavaScript come back nearly empty and are refused with a hint to save
the page as a PDF instead.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

TIMEOUT = 20
MAX_BYTES = 5 * 1024 * 1024
MIN_TEXT_CHARS = 200

# Page chrome and code, not content.
_SKIPPED = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "head"}
# Elements that start a new line of text.
_BLOCKS = {
    "p", "div", "section", "article", "main", "li", "ul", "ol", "br", "tr", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "blockquote", "pre",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self.skip_depth += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIPPED and self.skip_depth:
            self.skip_depth -= 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def fetch_page_text(url: str) -> tuple[str, str]:
    """Returns (text, final URL after redirects); raises ValueError with a
    message the side panel can show when the page cannot be used."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("enter a full web address starting with http:// or https://")
    try:
        response = requests.get(
            url.strip(), timeout=TIMEOUT, stream=True, allow_redirects=True,
            headers={"User-Agent": "LLM-Judge golden generator"},
        )
    except requests.RequestException as e:
        raise ValueError(f"could not fetch the page: {type(e).__name__}: {e}") from e
    if not response.ok:
        raise ValueError(f"the page answered HTTP {response.status_code}")
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith(("text/html", "text/plain", "application/xhtml")):
        raise ValueError(
            f"that address is not a web page ({content_type or 'unknown type'}): "
            "download the file and upload it instead"
        )
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise ValueError(f"the page is larger than {MAX_BYTES // (1024 * 1024)} MB")
    raw = bytes(body).decode(response.encoding or "utf-8", errors="replace")
    text = raw if content_type.startswith("text/plain") else html_to_text(raw)
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError(
            f"the page has almost no readable text (under {MIN_TEXT_CHARS} characters). "
            "If its content is built with JavaScript, save it as a PDF (Ctrl+P) and upload that instead."
        )
    return text, response.url or url
