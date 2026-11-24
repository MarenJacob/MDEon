"""Deterministic website checks.

Code, not the model, produces these observations, so each one is true of the page we
fetched and the owner can verify it in seconds. They give EON real, checkable hooks.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from urllib.parse import urlparse

from eon.leads import web
from eon.leads.qualify import _GENERIC_WORDS, Doc, name_tokens, norm

_NOT_OWN_SITE = ("facebook.", "instagram.", "twitter.", "x.com", "tiktok.", "linkedin.", "youtube.",
                 "tripadvisor.", "finelib.", "wikipedia.", "google.", "yellowpages", "vconnect",
                 "jiji.", "linktr.ee", "whatsapp.", "wa.me")
_WHATSAPP = re.compile(r"(?:wa\.me/|whatsapp\.com/send\?phone=)(\+?\d+)", re.I)
_ACTION = re.compile(r"\b(book(?:ing)?|reserv\w*|enquir\w*|inquir\w*|order online|add to cart|"
                     r"buy now|get a quote|request a quote)\b", re.I)
_YEAR = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,40}((?:19|20)\d{2})", re.I)
_TAGS = re.compile(r"<(?:script|style)[\s\S]*?</(?:script|style)>|<[^>]+>")
MAX_CHECKS = 4


def _label(url: str) -> str:
    """The registrable part of a web address: crispanhotel.com -> crispanhotel."""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p and p != "www"]
    if len(parts) >= 3 and parts[-2] in ("com", "co", "org", "net", "edu", "gov"):
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _leftover(label: str, name: str) -> int:
    """Characters of the address left once the business's own words are removed."""
    rest = re.sub(r"[^a-z0-9]", "", label)
    words = {w for w in re.findall(r"[a-z0-9]+", norm(name)) if len(w) >= 2} | _GENERIC_WORDS
    for w in sorted(words, key=len, reverse=True):
        rest = rest.replace(w, "")
    return len(rest)


def own_site(name: str, urls: List[str]) -> Optional[str]:
    """The business's own website. Wrong attribution is worse than no check, so be strict: the address
    must be built from the business's name (crispanhotel.com), not merely contain one word of it
    (jennychinese.com is not Jenny's Kitchen)."""
    tokens = name_tokens(name)
    for u in urls:
        low = u.lower()
        if not u.startswith("http") or any(b in low for b in _NOT_OWN_SITE):
            continue
        label = _label(u)
        if any(t in label for t in tokens) and _leftover(label, name) <= 3:
            return u
    return None


def checks(final_url: str, html: str, today: Optional[date] = None) -> List[str]:
    """Observations about one fetched homepage. Returns [] when the page looks script-rendered."""
    today = today or date.today()
    visible = re.sub(r"\s+", " ", _TAGS.sub(" ", html)).strip()
    if len(visible) < 200:
        return []  # probably built by JavaScript, so the raw HTML would mislead us
    out: List[str] = []
    if final_url.lower().startswith("http://"):
        out.append("The site loads over http, not https, so browsers may warn visitors it is not secure.")
    if not re.search(r"<meta[^>]+name=[\"']viewport[\"']", html, re.I):
        out.append("The homepage has no mobile viewport tag, so phones may show the desktop layout.")
    numbers = _WHATSAPP.findall(html)
    if numbers:
        bad = next((n for n in numbers if n.lstrip("+").startswith("0")), None)
        if bad:
            out.append(f"The homepage WhatsApp link uses a number starting with 0 ({bad}). It may open for visitors "
                       "in the same country, but WhatsApp links need the international number without the leading "
                       "zero, so it can fail for visitors elsewhere.")
    else:
        out.append("No WhatsApp chat link was found on the homepage.")
    clickable = " ".join(re.findall(r"<(?:a|button)\b[^>]*>([\s\S]*?)</(?:a|button)>", html, re.I))
    if "<form" not in html.lower() and not _ACTION.search(_TAGS.sub(" ", clickable)):
        out.append("The homepage has no form and no booking, enquiry or order link (checked forms, links and buttons).")
    years = [int(y) for y in _YEAR.findall(html)]
    if years and max(years) <= today.year - 3:
        out.append(f"The footer copyright year is {max(years)}.")
    return out[:MAX_CHECKS]


def observe(name: str, docs: List[Doc]) -> List[Dict[str, str]]:
    site = own_site(name, [u for u, _t in docs])
    if not site:
        return []
    fetched = web.fetch_html(site)
    if not fetched:
        return []
    final_url, html = fetched
    return [{"url": final_url, "quote": "CHECK: " + o} for o in checks(final_url, html)]


def apply(lead: Dict[str, Any], observations: List[Dict[str, str]]) -> Dict[str, Any]:
    """Merge code-verified observations into a lead and re-run the status rules."""
    if not observations:
        return lead
    lead = dict(lead)
    lead["evidence"] = list(lead["evidence"]) + observations
    if not lead.get("problem"):
        lead["problem"] = " ".join(o["quote"][len("CHECK: "):] for o in observations[:2])
    if lead["status"] == "unverified":
        lead["status"] = "review"
    if lead["status"] == "review" and lead["fit_score"] >= 6 and lead["confidence"] != "low":
        lead["status"] = "qualified"
    return lead
