"""Search and fetch helpers. Everything returned here is UNTRUSTED web content."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

import httpx

_URL_RE = re.compile(r"https?://[^\s\)\]>\"'<]+")
_SKIP_HOSTS = ("google.", "bing.", "duckduckgo.", "you.com", "youtube.com", "wikipedia.org")
MAX_BYTES = 300_000
MAX_TEXT = 3500


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self.links: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description"):
                self.parts.append(a.get("content") or "")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("mailto:", "tel:")) or any(
                h in href for h in ("wa.me", "api.whatsapp.com", "facebook.com", "instagram.com")
            ):
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = " ".join(p.parts)
    if p.links:
        text += " LINKS: " + " ".join(dict.fromkeys(p.links))
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]


def urls_in(text: str, limit: int = 5) -> List[str]:
    out: List[str] = []
    for u in _URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if any(h in u for h in _SKIP_HOSTS) or u in out:
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


def search(query: str) -> str:
    """Search via OpenJarvis' web_search tool (keyless by default). Returns text."""
    from openjarvis.tools.web_search import WebSearchTool

    result = WebSearchTool().execute(query=query)
    return (result.content or "") if result.success else ""


def fetch_text(url: str, timeout: float = 8.0) -> Optional[str]:
    from openjarvis.security.ssrf import check_ssrf

    if check_ssrf(url):
        return None
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; EON-research)"}) as c:
            r = c.get(url)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code != 200 or not ("html" in ctype or "text" in ctype):
            return None
        return html_to_text(r.content[:MAX_BYTES].decode(r.encoding or "utf-8", errors="ignore"))
    except Exception:
        return None


def fetch_html(url: str, timeout: float = 8.0) -> Optional[Tuple[str, str]]:
    """Return (final_url, raw_html) for a page, or None."""
    from openjarvis.security.ssrf import check_ssrf

    if check_ssrf(url):
        return None
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; EON-research)"}) as c:
            r = c.get(url)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code != 200 or not ("html" in ctype or "text" in ctype):
            return None
        return str(r.url), r.content[:MAX_BYTES].decode(r.encoding or "utf-8", errors="ignore")
    except Exception:
        return None
