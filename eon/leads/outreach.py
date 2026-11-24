"""Outreach drafts. Drafts are never sent: they wait for the owner's approval.

The model may only say what the verified facts and the approved services support.
Code checks the result: unsupported claims about the business, risky promises and
prices that are not on the price list trigger one rewrite, then a refusal.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from eon.leads import llm
from eon.workspaces import Workspace

SYSTEM = (
    "You write short, honest first-contact messages for MD Studios, a small digital agency run by "
    "Maren Danjuma. Reply with ONE JSON object only. You know only the verified facts and approved "
    "services you are given. Do NOT say or imply anything about the business beyond the verified facts (no 'I noticed', "
    "'I saw', 'you handle X manually'). You may mention a verified fact politely, without blame, as "
    "a helpful heads-up or a question; otherwise ask a short, curious question. Describe only what the chosen package actually includes, in plain words. "
    "Never say it 'automates' things it does not. Never offer demos, timelines, guarantees, "
    "discounts or free items, and never call MD Studios' work a 'prototype'. Quote a price only if "
    "it appears in the approved services. Be polite and brief. End with a low-pressure question and "
    "a line saying they can reply 'no' and you will not message again."
)

# Wording that asserts something about the business or promises something. Allowed only if the
# same word already appears in the verified facts or the approved services.
_RISKY = [re.compile(p, re.I) for p in (
    r"\bI (?:noticed|saw|see|found|read|understand)\b",
    r"\bit (?:seems|looks|appears)\b",
    r"\byou (?:currently|still|only|rely|handle|manage|lack|struggle|don't|do not)\b",
    r"\bmanual(?:ly)?\b",
    r"\bautomat\w+",
    r"\bdemo\b",
    r"\bprototype\b",
    r"\bguarantee\w*",
    r"\b(?:within|in) \d+ (?:hours?|days?|weeks?)\b",
    r"\b\d+\s?%",
    r"\bfree (?:consultation|demo|trial|quote|website|audit|setup)\b",
    r"\bdiscount\w*",
    r"\breservations?\b",
    r"\bbooks\b",
)]
_AMOUNT = re.compile(r"(?:₦|\bN)\s?\d[\d,]*")


def unsupported(drafts: Dict[str, str], services: str, facts: str) -> List[str]:
    """Return wording in ``drafts`` that the facts and approved services do not back."""
    text = " ".join(drafts.values())
    # Vocabulary comes from the packages and the facts only. The Rules section contains words such
    # as "prototype" and "discount" that describe policy, not things to say to a prospect.
    allowed = f"{services.split('## Rules')[0]} {facts}".lower()
    known_numbers = {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d,]*", services)}
    bad: List[str] = []
    for rx in _RISKY:
        for m in rx.finditer(text):
            word = m.group(0).lower()
            if word not in allowed and word not in bad:
                bad.append(word)
    for m in _AMOUNT.finditer(text):
        if re.sub(r"\D", "", m.group(0)) not in known_numbers:
            bad.append(f"price {m.group(0).strip()}")
    return bad


OPT_OUT = 'If you\'d rather not hear from me, just reply "no" and I won\'t message again.'
_HAS_OPT_OUT = re.compile(r"reply\s*[\"\u201c'\u2018]?no\b", re.I)
_SIGN_OFF = re.compile(r"\n\s*(?:best regards|kind regards|warm regards|regards|sincerely|yours)\b[\s\S]*$", re.I)


def signature(services: str) -> str:
    """Signature built from the owner line of the services file, not from the model."""
    for line in services.splitlines():
        if line.lower().startswith("owner:"):
            parts = [p.strip() for p in line.split(":", 1)[1].split("|") if p.strip()]
            return "Best regards,\n" + "\n".join([parts[0], "MD Studios"] + parts[1:]) if parts else ""
    return ""


def finish(drafts: Dict[str, str], services: str) -> Dict[str, str]:
    """Add what must always be there, whatever the model wrote: an opt-out line and the owner's contact."""
    out = dict(drafts)
    if not _HAS_OPT_OUT.search(out["whatsapp"]):
        out["whatsapp"] = out["whatsapp"].rstrip() + "\n\n" + OPT_OUT
    body = _SIGN_OFF.sub("", out["email_body"]).rstrip()
    if not _HAS_OPT_OUT.search(body):
        body += "\n\n" + OPT_OUT
    sig = signature(services)
    out["email_body"] = body + ("\n\n" + sig if sig else "")
    return out


def _ask(ws: Workspace, user: str, chat: Callable[..., str]) -> Dict[str, str]:
    raw = llm.extract_json(chat(ws, SYSTEM, user, max_tokens=900, temperature=0.4))
    return {k: str(raw.get(k) or "").strip() for k in ("whatsapp", "email_subject", "email_body")}


def draft(ws: Workspace, lead: Dict[str, Any], services: str,
          chat: Optional[Callable[..., str]] = None) -> Dict[str, str]:
    chat = chat or llm.chat
    facts = "\n".join(f'- "{e["quote"]}" ({e["url"]})' for e in lead["evidence"])
    user = (f"Business: {lead['name']} ({lead.get('location') or 'unknown location'})\n"
            f"Verified facts from their public presence:\n{facts}\n"
            f"Opportunity: {lead.get('opportunity')}\n\n"
            f"MD Studios services (approved; mention prices only if they appear here):\n{services[:5000]}\n\n"
            'Return JSON: {"whatsapp": str (max 600 chars), "email_subject": str, "email_body": str (max 1200 chars)}')
    context = facts  # only verified facts widen the allowed wording, never the model's own suggestion
    result = _ask(ws, user, chat)
    bad = unsupported(result, services, context)
    if bad:
        result = _ask(ws, user + f"\n\nYour previous draft used wording that the verified facts and approved "
                      f"services do not support: {bad}. Rewrite without any of it. Do not claim anything "
                      "about how the business currently operates; describe only what the package includes.", chat)
        bad = unsupported(result, services, context)
    if bad:
        raise ValueError(f"draft still contains unsupported claims {bad}; edit by hand or try again")
    return finish(result, services)


def followup(ws: Workspace, lead: Dict[str, Any], services: str, days: int,
             chat: Optional[Callable[..., str]] = None) -> str:
    """A short, polite follow-up to a message sent ``days`` ago. Same guard as first contact."""
    chat = chat or llm.chat
    facts = "\n".join(f'- "{e["quote"]}"' for e in lead["evidence"])
    user = (f"You messaged {lead['name']} ({lead.get('location') or 'Jos'}) about {days} days ago and have "
            f"had no reply.\nVerified facts:\n{facts}\n\nApproved services:\n{services[:5000]}\n\n"
            "Write one short, friendly WhatsApp follow-up (max 400 characters) that politely checks whether "
            "they saw your earlier message and offers to send details. No pressure, no new claims, no prices. "
            'Return JSON: {"whatsapp": str}')
    bad: List[str] = []
    for _ in range(2):
        text = str(llm.extract_json(chat(ws, SYSTEM, user, max_tokens=500, temperature=0.4)).get("whatsapp") or "").strip()
        bad = unsupported({"whatsapp": text}, services, facts) if text else ["empty message"]
        if not bad:
            return finish({"whatsapp": text, "email_subject": "", "email_body": ""}, services)["whatsapp"]
        user += f"\n\nYour previous draft used unsupported wording {bad}. Rewrite without it."
    raise ValueError(f"follow-up still contains unsupported claims {bad}; edit by hand or try again")
