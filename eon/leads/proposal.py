"""Proposals built from approved services and verified facts.

Code fills in package names, prices, inclusions and terms straight from the approved services
file. The model only picks a package and explains the fit in plain words, and that wording is
checked by the same guard as outreach drafts.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from eon.leads import llm, outreach, qualify
from eon.workspaces import Workspace, home

TIERS = ("Starter", "Standard", "Premium")
_SOLUTION = re.compile(r"^(\d+)\s+(.+?):\s+(.*)$")
_TIER = re.compile(r"^\s*([\d,]+)\s*\((.*)\)\s*$")

PICK_SYSTEM = (
    "You help a small digital agency write a proposal. Reply with ONE JSON object only. Pick the "
    "single best-fitting solution number and package tier from the catalogue. Write 'why' in 2 or 3 "
    "plain sentences using only the verified facts: say what the package does for them and never "
    "claim anything about how their business currently works. No discounts, guarantees, timelines "
    "or demos."
)


def _section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def catalog(services: str) -> Dict[int, Dict[str, Any]]:
    """{1: {"name": ..., "tiers": {"Starter": (price, features), ...}}} parsed from the services file."""
    out: Dict[int, Dict[str, Any]] = {}
    for line in _section(services, "Solutions").splitlines():
        m = _SOLUTION.match(line.strip())
        if not m:
            continue
        tiers = {}
        for tier, part in zip(TIERS, m.group(3).split(" / ")):
            t = _TIER.match(part)
            if t:
                tiers[tier] = (int(t.group(1).replace(",", "")), t.group(2).strip())
        if len(tiers) == 3:
            out[int(m.group(1))] = {"name": m.group(2).strip(), "tiers": tiers}
    return out


def owner(services: str) -> Tuple[str, List[str]]:
    for line in services.splitlines():
        if line.lower().startswith("owner:"):
            parts = [p.strip() for p in line.split(":", 1)[1].split("|") if p.strip()]
            return (parts[0] if parts else ""), parts[1:]
    return "", []


def terms(services: str) -> Dict[str, str]:
    rules = _section(services, "Rules").splitlines()

    def rule(prefix: str) -> str:
        return next((r[2:].strip() for r in rules if r.startswith(f"- {prefix}")), "")

    return {"payment": rule("Payment:"), "content": rule("Client supplies"), "revisions": rule("Revisions:"),
            "monthly": _section(services, "Monthly care").replace("\n", " ")}


def _plain(quote: str) -> str:
    return quote[len("CHECK: "):] if quote.startswith("CHECK: ") else quote


def choose(ws: Workspace, lead: Dict[str, Any], services: str,
           chat: Optional[Callable[..., str]] = None) -> Dict[str, Any]:
    chat = chat or llm.chat
    cat = catalog(services)
    if not cat:
        raise ValueError("the services file has no solutions this tool can read")
    facts = "\n".join(f'- "{_plain(e["quote"])}"' for e in lead["evidence"])
    listing = "\n".join(f"{n}. {c['name']}" for n, c in cat.items())
    user = (f"Business: {lead['name']} ({lead.get('location') or 'unknown location'})\n"
            f"Verified facts:\n{facts}\n\nSolutions:\n{listing}\n"
            "Tiers: Starter, Standard, Premium (pick the smallest that fits).\n\n"
            'Return JSON: {"solution": int, "tier": "Starter|Standard|Premium", "why": str}')
    bad: List[str] = []
    for _ in range(2):
        raw = llm.extract_json(chat(ws, PICK_SYSTEM, user, max_tokens=800, temperature=0.3))
        try:
            n, tier, why = int(raw.get("solution")), str(raw.get("tier")).strip().title(), str(raw.get("why") or "").strip()
        except (TypeError, ValueError):
            n, tier, why = 0, "", ""
        bad = ["invalid package choice"] if (n not in cat or tier not in TIERS or not why) else \
            outreach.unsupported({"why": why}, services, facts)
        if not bad:
            return {"solution": n, "tier": tier, "why": why}
        user += (f"\n\nYour previous answer was rejected ({bad}). Choose from the list only and rewrite "
                 "'why' without unsupported wording.")
    raise ValueError(f"could not produce a supported package choice {bad}")


def render(lead: Dict[str, Any], choice: Dict[str, Any], cat: Dict[int, Dict[str, Any]], services: str,
           today: Optional[date] = None) -> str:
    today = today or date.today()
    pack = cat[choice["solution"]]
    price, features = pack["tiers"][choice["tier"]]
    name, contact = owner(services)
    t = terms(services)
    found = []
    for e in lead["evidence"][:6]:
        label = " (checked on their site)" if e["quote"].startswith("CHECK: ") else ""
        found.append(f"- {_plain(e['quote'])[:220]}{label} [{e['url']}]")
    lines = [f"# Proposal for {lead['name']}",
             f"Prepared by {name}, MD Studios" + (" | " + " | ".join(contact) if contact else ""),
             f"Date: {today:%d %B %Y}", "",
             "## What we found (verified)", *found, "",
             "## Recommendation",
             f"**{pack['name']}, {choice['tier']} package: \u20a6{price:,}**",
             choice["why"], f"Includes: {features}.", "",
             "All three packages for this solution:"]
    lines += [f"- {tier}: \u20a6{p:,} ({d})" for tier, (p, d) in pack["tiers"].items()]
    lines += ["", "Prices are starting prices. The final quote is confirmed in writing after a short "
              "conversation about your needs.", "", "## Monthly care (optional)", t["monthly"], "", "## Terms"]
    lines += [f"- {t[k]}" for k in ("payment", "content", "revisions") if t[k]]
    lines += ["", "## Next step", "Reply to this message and we will arrange a short call."]
    return "\n".join(lines)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "lead"


def run(cmd: str, ws: Workspace, store: Any, lead_id: int) -> int:
    lead = store.get_lead(lead_id)
    if not lead:
        print("No such lead.")
        return 1
    if not lead["evidence"]:
        print("This lead has no verified evidence, so there is nothing true to build on.")
        return 1
    services = qualify.load_services(ws)
    if not qualify.services_approved(services):
        print(f"Fill in {qualify.services_path(ws)} first.")
        return 1
    try:
        if cmd == "proposal":
            text = render(lead, choose(ws, lead, services), catalog(services), services)
            folder = home(ws) / "proposals"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{lead_id}-{_slug(lead['name'])}.md"
            path.write_text(text, encoding="utf-8")
            store.audit("proposal_created", f"lead={lead_id} file={path.name}")
            print(f"{text}\n\nSaved: {path}\nCheck every line, then send it yourself.")
        else:
            sent = datetime.fromisoformat(lead["updated_at"])
            days = max(1, (datetime.now(timezone.utc) - sent).days)
            msg = outreach.followup(ws, lead, services, days)
            did = store.add_draft(lead_id, "whatsapp", msg)
            print(f"Follow-up drafted for approval: whatsapp #{did}\n\n{msg}\n\nThen: approve {did}")
    except Exception as exc:  # noqa: BLE001
        print(f"{cmd} failed ({exc.__class__.__name__}): {str(exc)[:300]}")
        return 1
    return 0
