"""Daily workflow: batch scouting, a one-screen 'today' list, status tracking and CSV export."""

from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from eon.leads import qualify
from eon.leads.store import LeadStore

DEFAULT_CATEGORIES = ["hotels", "event centres", "restaurants", "schools", "clinics", "salons", "pharmacies"]
STATUSES = ("contacted", "replied", "meeting", "proposal", "won", "lost", "dead")
FOLLOW_UP_DAYS = 7
TOP = 5


def known(store: LeadStore, name: str) -> bool:
    """True if a lead with the same distinctive name words already exists (names drift between searches)."""
    want = set(qualify.name_tokens(name))
    return any(want and set(qualify.name_tokens(lead["name"])) == want for lead in store.list_leads())


def batch(ws: Any, store: LeadStore, scout_fn: Callable[..., int], city: str, categories: List[str],
          per: int, pause: float = 20.0, sleep: Optional[Callable[[float], None]] = None,
          region: str = "") -> int:
    """Scout several business categories in one city. The pause keeps free-tier rate limits happy."""
    sleep = sleep or time.sleep
    before = len(store.list_leads())
    for i, cat in enumerate(categories):
        print(f"\n=== {cat} in {city}", flush=True)
        where = f"{city}, {region}, Nigeria" if region else f"{city}, Nigeria"
        scout_fn(ws, store, f"{cat} in {where}", [], city, per)
        if i < len(categories) - 1:
            sleep(pause)
    leads = store.list_leads()
    counts: Dict[str, int] = {}
    for lead in leads:
        counts[lead["status"]] = counts.get(lead["status"], 0) + 1
    print(f"\nBatch done: {len(leads) - before} new lead(s). All leads by status: "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("Next: python -m eon.leads today")
    return 0


def _age_days(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 86400


def today_report(store: LeadStore) -> str:
    leads, drafts = store.list_leads(), store.list_drafts()
    drafted = {d["lead_id"] for d in drafts}
    names = {lead["id"]: lead["name"] for lead in leads}
    lines: List[str] = []

    def section(title: str, items: List[Any], fmt: Callable[[Any], str]) -> None:
        if not items:
            return
        lines.append(f"{title} ({len(items)})")
        lines.extend(f"  {fmt(i)}" for i in items[:TOP])
        if len(items) > TOP:
            lines.append(f"  ... and {len(items) - TOP} more")

    section("1. Replied: answer these first",
            [x for x in leads if x["status"] == "replied"], lambda x: f"[{x['id']}] {x['name']}: proposal {x['id']}")
    section("2. Approved: send, then run 'sent <draft id>'",
            [d for d in drafts if d["status"] == "approved"],
            lambda d: f"draft #{d['id']} ({d['channel']}) to {names.get(d['lead_id'], '?')}")
    section("3. Drafts waiting for your approval",
            [d for d in drafts if d["status"] == "pending"],
            lambda d: f"draft #{d['id']} ({d['channel']}) to {names.get(d['lead_id'], '?')}: approve {d['id']}")
    section(f"4. Follow up ({FOLLOW_UP_DAYS}+ days since you contacted them)",
            [x for x in leads if x["status"] == "contacted" and _age_days(x["updated_at"]) >= FOLLOW_UP_DAYS],
            lambda x: f"[{x['id']}] {x['name']}: followup {x['id']}  (or mark {x['id']} replied)")
    section("5. Worth drafting a message for",
            [x for x in leads if x["status"] in ("qualified", "review") and x["id"] not in drafted
             and x["fit_score"] >= 5 and x["evidence"]],
            lambda x: f"[{x['id']}] {x['name']} fit {x['fit_score']}/10: draft {x['id']}")
    if not lines:
        lines.append("Nothing needs you today. Run: python -m eon.leads batch --city <city>")
    tally: Dict[str, int] = {}
    for lead in leads:
        tally[lead["status"]] = tally.get(lead["status"], 0) + 1
    lines.append("\nPipeline: " + (", ".join(f"{k} {v}" for k, v in sorted(tally.items())) or "empty"))
    return "\n".join(lines)


def mark(store: LeadStore, lead_id: int, status: str) -> int:
    lead = store.get_lead(lead_id)
    if not lead:
        print("No such lead.")
        return 1
    store.set_status(lead_id, status)
    print(f"[{lead_id}] {lead['name']}: {lead['status']} -> {status}")
    return 0


def export_csv(store: LeadStore, out: str) -> str:
    cols = ["id", "name", "location", "status", "fit_score", "phone", "whatsapp", "email", "problem",
            "est_value", "evidence_count"]
    leads = store.list_leads()
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for x in leads:
            c = x["contacts"]
            writer.writerow([x["id"], x["name"], x.get("location") or "", x["status"], x["fit_score"],
                             "; ".join(c.get("phone", [])), "; ".join(c.get("whatsapp", [])),
                             "; ".join(c.get("email", [])), x.get("problem") or "", x.get("est_value") or "",
                             len(x["evidence"])])
    return f"Exported {len(leads)} lead(s) to {out}"
