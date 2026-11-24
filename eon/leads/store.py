"""SQLite store for leads, outreach drafts and an audit trail."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, industry TEXT, location TEXT,
  website TEXT, social TEXT, problem TEXT, opportunity TEXT, evidence TEXT, contacts TEXT,
  fit_score INTEGER, confidence TEXT, uncertainties TEXT, est_value TEXT,
  status TEXT NOT NULL, source_query TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id),
  channel TEXT NOT NULL, subject TEXT, body TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, detail TEXT);
"""
_JSON_FIELDS = ("social", "evidence", "contacts", "uncertainties")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LeadStore:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def audit(self, event: str, detail: str = "") -> None:
        self._db.execute("INSERT INTO audit(ts,event,detail) VALUES(?,?,?)", (_now(), event, detail))
        self._db.commit()

    def _lead(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        for f in _JSON_FIELDS:
            d[f] = json.loads(d[f]) if d.get(f) else ([] if f != "contacts" else {})
        return d

    def exists(self, name: str, location: str = "") -> bool:
        row = self._db.execute(
            "SELECT 1 FROM leads WHERE lower(name)=lower(?) AND lower(coalesce(location,''))=lower(?)",
            (name.strip(), (location or "").strip()),
        ).fetchone()
        return row is not None

    def add_lead(self, lead: Dict[str, Any]) -> int:
        now = _now()
        data = {k: (json.dumps(v) if k in _JSON_FIELDS else v) for k, v in lead.items()}
        data.update(created_at=now, updated_at=now)
        cols = ",".join(data)
        cur = self._db.execute(f"INSERT INTO leads({cols}) VALUES({','.join('?' * len(data))})", list(data.values()))
        self._db.commit()
        self.audit("lead_added", f"id={cur.lastrowid} name={lead.get('name')} status={lead.get('status')}")
        return int(cur.lastrowid)

    def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        return self._lead(self._db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())

    def list_leads(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        q, a = ("SELECT * FROM leads WHERE status=? ORDER BY fit_score DESC, id", (status,)) if status else (
            "SELECT * FROM leads ORDER BY fit_score DESC, id", ())
        return [self._lead(r) for r in self._db.execute(q, a).fetchall()]

    def set_status(self, lead_id: int, status: str) -> None:
        self._db.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, _now(), lead_id))
        self._db.commit()
        self.audit("lead_status", f"id={lead_id} status={status}")

    def add_draft(self, lead_id: int, channel: str, body: str, subject: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO drafts(lead_id,channel,subject,body,status,created_at) VALUES(?,?,?,?,?,?)",
            (lead_id, channel, subject, body, "pending", _now()))
        self._db.commit()
        self.audit("draft_created", f"id={cur.lastrowid} lead={lead_id} channel={channel}")
        return int(cur.lastrowid)

    def get_draft(self, draft_id: int) -> Optional[Dict[str, Any]]:
        row = self._db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def list_drafts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        q, a = ("SELECT * FROM drafts WHERE status=? ORDER BY id", (status,)) if status else (
            "SELECT * FROM drafts ORDER BY id", ())
        return [dict(r) for r in self._db.execute(q, a).fetchall()]

    def decide_draft(self, draft_id: int, status: str) -> None:
        self._db.execute("UPDATE drafts SET status=?, decided_at=? WHERE id=?", (status, _now(), draft_id))
        self._db.commit()
        self.audit("draft_" + status, f"id={draft_id}")
