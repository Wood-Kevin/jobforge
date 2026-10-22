"""HTML to plain text with the standard library (ported from ats_scanner/text.py, edd1528)."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0  # inside <script>/<style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    # Greenhouse double-encodes (&lt;p&gt;), so unescape once before parsing.
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    c = _Collector()
    c.feed(raw)
    text = "".join(c.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def collapse_ws(text: str | None) -> str:
    """Whitespace-insensitive form used by the material hash."""
    return " ".join((text or "").split())
