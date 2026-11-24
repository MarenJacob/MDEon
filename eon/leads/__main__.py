"""CLI:  python -m eon.leads <command>   (workspace: md-studios)

  init-knowledge            create the services/prices file EON must use
  scout --query "..."       discover businesses from a search and research them
  scout --names "A; B"      research businesses you name  [--city Jos]
  list [--status S]         leads, best first
  show ID                   one lead with its evidence
  draft ID                  write outreach drafts (need approval before use)
  drafts                    pending drafts
  approve DRAFT_ID          approve and print the message to copy and send
  reject DRAFT_ID | sent DRAFT_ID
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from eon.leads import outreach, pipeline, proposal, qualify, sitecheck
from eon.leads.store import LeadStore
from eon.workspaces import WORKSPACES, home

TEMPLATE = """# MD Studios services and prices (APPROVED BY OWNER)
# Replace every TODO with real information, then delete this comment. EON refuses to
# draft outreach or estimate value until no TODO remains.

## Services
- TODO service name: TODO what the client gets, TODO price range in naira
- TODO service name: TODO what the client gets, TODO price range in naira

## Rules
- TODO discount policy (e.g. no discounts without owner approval)
- TODO payment terms
- TODO how to contact MD Studios (name, WhatsApp number)
"""


def _store(ws) -> LeadStore:
    home(ws).mkdir(parents=True, exist_ok=True)
    return LeadStore(home(ws) / "leads.db")


def _card(lead) -> str:
    ev = "\n".join(f'   - "{e["quote"]}"\n     {e["url"]}' for e in lead["evidence"]) or "   (none verified)"
    c = lead["contacts"]
    contact = ", ".join(f"{k}: {', '.join(v)}" for k, v in c.items() if v) or "none verified"
    unc = "\n".join(f"   ? {u}" for u in lead["uncertainties"])
    return (f"[{lead['id']}] {lead['name']}  ({lead.get('location') or '?'})  "
            f"fit {lead['fit_score']}/10  {lead['confidence']}  status: {lead['status']}\n"
            f"   problem: {lead.get('problem') or 'not established'}\n"
            f"   suggested opportunity (model's idea, not verified): {lead.get('opportunity') or '-'}\n"
            f"   value: {lead.get('est_value') or '-'}\n"
            f"   contact (verified): {contact}\n   evidence:\n{ev}\n{unc}")


def scout(ws, store: LeadStore, query: Optional[str], names: List[str], city: str, limit: int) -> int:
    services = qualify.load_services(ws)
    target_city = city
    targets = [{"name": n, "url": None} for n in names]
    if query:
        print(f"Searching: {query}", flush=True)
        targets += [dict(b, discovered=True) for b in qualify.discover(ws, query, limit, target_city)]
        city = city or query
    if not targets:
        print("Nothing to research (no search results or names).")
        return 1
    added = 0
    for t in targets[:limit]:
        if pipeline.known(store, t["name"]):
            print(f"skip (already known): {t['name']}")
            continue
        print(f"Researching: {t['name']} ...", flush=True)
        try:
            docs = qualify.gather_docs(t["name"], city if names else "", t.get("url"))
            if t.get("discovered") and target_city and not qualify.in_city(docs, target_city, t["name"]):
                print(f"  skipped: no sign this business is in {target_city}")
                store.audit("scout_skipped", f"{t['name']}: not in {target_city}")
                continue
            lead = qualify.qualify(ws, t["name"], city if names else "", docs, services)
            lead = sitecheck.apply(lead, sitecheck.observe(t["name"], docs))
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {exc.__class__.__name__}: {str(exc)[:120]}")
            store.audit("scout_error", f"{t['name']}: {exc.__class__.__name__}")
            continue
        lead["source_query"] = query or "named by owner"
        lid = store.add_lead(lead)
        print(_card(store.get_lead(lid)), flush=True)
        added += 1
        time.sleep(6)
    print(f"\nDone: {added} new lead(s). Nothing was contacted.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="eon.leads", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--workspace", default="md-studios", choices=list(WORKSPACES))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-knowledge")
    s = sub.add_parser("scout")
    s.add_argument("--query")
    s.add_argument("--names", default="")
    s.add_argument("--city", default="")
    s.add_argument("--limit", type=int, default=5)
    l = sub.add_parser("list")
    l.add_argument("--status")
    for name in ("show", "draft"):
        sub.add_parser(name).add_argument("id", type=int)
    sub.add_parser("drafts")
    for name in ("approve", "reject", "sent"):
        sub.add_parser(name).add_argument("id", type=int)
    b = sub.add_parser("batch")
    b.add_argument("--city", required=True)
    b.add_argument("--categories", default=",".join(pipeline.DEFAULT_CATEGORIES))
    b.add_argument("--per", type=int, default=3)
    b.add_argument("--region", default="")
    for name in ("proposal", "followup"):
        sub.add_parser(name).add_argument("id", type=int)
    sub.add_parser("today")
    m = sub.add_parser("mark")
    m.add_argument("id", type=int)
    m.add_argument("status", choices=pipeline.STATUSES)
    e = sub.add_parser("export")
    e.add_argument("--out")
    a = ap.parse_args(argv)
    ws = WORKSPACES[a.workspace]

    if a.cmd == "init-knowledge":
        p = qualify.services_path(ws)
        if p.exists():
            print(f"Already exists: {p}")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(TEMPLATE, encoding="utf-8")
            print(f"Created {p}. Fill it in (every TODO), then run draft.")
        return 0

    store = _store(ws)
    try:
        if a.cmd == "batch":
            cats = [c.strip() for c in a.categories.split(",") if c.strip()]
            return pipeline.batch(ws, store, scout, a.city, cats, a.per, region=a.region)
        if a.cmd in ("proposal", "followup"):
            return proposal.run(a.cmd, ws, store, a.id)
        if a.cmd == "today":
            print(pipeline.today_report(store))
            return 0
        if a.cmd == "mark":
            return pipeline.mark(store, a.id, a.status)
        if a.cmd == "export":
            print(pipeline.export_csv(store, a.out or str(home(ws) / "leads.csv")))
            return 0
        if a.cmd == "scout":
            names = [n.strip() for n in a.names.split(";") if n.strip()]
            if not a.query and not names:
                print("Give --query or --names.")
                return 2
            return scout(ws, store, a.query, names, a.city, a.limit)
        if a.cmd == "list":
            for ld in store.list_leads(a.status):
                print(f"[{ld['id']}] {ld['fit_score']}/10 {ld['status']:<10} {ld['name']} ({ld.get('location') or '?'})")
            return 0
        if a.cmd in ("show", "draft"):
            lead = store.get_lead(a.id)
            if not lead:
                print("No such lead.")
                return 1
            if a.cmd == "show":
                print(_card(lead))
                return 0
            if lead["status"] not in ("qualified", "review"):
                print(f"Lead is '{lead['status']}': no verified evidence to build a message on.")
                return 1
            services = qualify.load_services(ws)
            if not qualify.services_approved(services):
                print(f"Fill in {qualify.services_path(ws)} first (run init-knowledge). "
                      "EON only drafts from approved services and prices.")
                return 1
            try:
                d = outreach.draft(ws, lead, services)
            except Exception as exc:  # noqa: BLE001
                print(f"Drafting failed ({exc.__class__.__name__}): {str(exc)[:300]}")
                return 1
            w = store.add_draft(a.id, "whatsapp", d["whatsapp"])
            e = store.add_draft(a.id, "email", d["email_body"], d["email_subject"])
            print(f"Drafts created for approval: whatsapp #{w}, email #{e}\n\nWHATSAPP #{w}:\n{d['whatsapp']}\n\n"
                  f"EMAIL #{e}: {d['email_subject']}\n{d['email_body']}\n\n"
                  "Check names, prices and claims. Then: approve <id>")
            return 0
        if a.cmd == "drafts":
            for d in store.list_drafts("pending"):
                print(f"#{d['id']} lead {d['lead_id']} {d['channel']}: {d['body'][:70]}...")
            return 0
        d = store.get_draft(a.id)
        if not d:
            print("No such draft.")
            return 1
        if a.cmd == "approve":
            store.decide_draft(a.id, "approved")
            print(f"Approved. Copy and send it yourself, then run: sent {a.id}\n\n"
                  f"{d['subject'] + chr(10) if d['subject'] else ''}{d['body']}")
        elif a.cmd == "reject":
            store.decide_draft(a.id, "rejected")
            print("Rejected.")
        elif a.cmd == "sent":
            if d["status"] != "approved":
                print("Only approved drafts can be marked sent.")
                return 1
            store.decide_draft(a.id, "sent")
            store.set_status(d["lead_id"], "contacted")
            print("Marked as sent. Lead status: contacted.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
