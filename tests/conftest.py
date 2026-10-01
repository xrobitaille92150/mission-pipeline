"""Doubles de test : Airtable et Claude en mémoire, contexte sans secrets."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("MP_OUT_DIR", str(Path(__file__).parent / "_out"))


class FakeAirtable:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self.tables_data = tables or {}
        self.calls: list[tuple] = []
        self.dry_run = False
        self._n = 0

    def _new_id(self) -> str:
        self._n += 1
        return f"rec{self._n:014d}"

    def list(self, table, *, fields=None, formula=None, sort=None, max_records=None, view=None):
        self.calls.append(("list", table, formula))
        recs = list(self.tables_data.get(table, []))
        return recs[:max_records] if max_records else recs

    def create(self, table, records):
        out = []
        for f in records:
            rec = {"id": self._new_id(), "fields": dict(f)}
            self.tables_data.setdefault(table, []).append(rec)
            out.append(rec)
        self.calls.append(("create", table, records))
        return out

    def patch(self, table, record_id, fields):
        self.calls.append(("patch", table, record_id, fields))
        for r in self.tables_data.get(table, []):
            if r["id"] == record_id:
                r["fields"].update(fields)
                return r
        return {"id": record_id, "fields": fields}

    def patch_many(self, table, updates):
        for u in updates:
            self.patch(table, u["id"], u["fields"])
        return len(updates)

    def upsert(self, table, records, merge_on):
        self.calls.append(("upsert", table, records, merge_on))
        return len(records), 0

    def replace_attachments(self, table, record_id, field, paths):
        self.calls.append(("attach", table, record_id, field, [str(p) for p in paths]))

    def tables(self):
        return [{"id": t, "name": t, "fields": [{"name": n} for n in (self.tables_data.get(t, [{}])[0].get("fields", {}) if self.tables_data.get(t) else {})]} for t in self.tables_data]


class FakeClaude:
    """Renvoie les réponses JSON dans l'ordre fourni ; enregistre les prompts reçus."""

    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses or [])
        self.prompts: list[dict] = []
        self.model_main = "fake-main"
        self.model_fast = "fake-fast"

    def json(self, *, system, user, schema, **kw):
        self.prompts.append({"system": system, "user": user, "schema": schema, **kw})
        if not self.responses:
            raise AssertionError("FakeClaude : plus de réponse en file")
        return self.responses.pop(0)

    def text(self, *, system, user, **kw):
        self.prompts.append({"system": system, "user": user, **kw})
        return self.responses.pop(0)

    def usage_line(self):
        return f"{len(self.prompts)} appel(s) (fake)"


class FakeContext:
    def __init__(self, at: FakeAirtable | None = None, claude: FakeClaude | None = None, tmp: Path | None = None):
        from mp.config import settings
        from mp.context import RunReport
        self.s = settings()
        self.at = at or FakeAirtable()
        self.claude = claude or FakeClaude()
        self.dry_run = False
        self.report = RunReport()
        self.offres = "OFFRES"
        self.candidatures = "CANDIDATURES"
        self.profile_md = "Profil de test."
        self._tmp = tmp or Path(os.environ["MP_OUT_DIR"])

    def out(self, *parts):
        p = self._tmp.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


@pytest.fixture
def fake_at():
    return FakeAirtable()


@pytest.fixture
def ctx(fake_at, tmp_path):
    return FakeContext(at=fake_at, tmp=tmp_path)
