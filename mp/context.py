"""Contexte d'exécution partagé par toutes les étapes + rapport de run (pour le digest)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

from mp.airtable import Airtable
from mp.claude import Claude
from mp.config import Settings, settings
from mp.gmail import Gmail

log = logging.getLogger("mp")


@dataclass
class RunReport:
    started: datetime = field(default_factory=datetime.now)
    ingest: dict = field(default_factory=dict)          # stats d'ingestion
    scored: list[dict] = field(default_factory=list)    # {record, title, employer, score, verdict, why, url, rank}
    dossiers: list[dict] = field(default_factory=list)  # {record, title, employer, cv, letter}
    events: list[dict] = field(default_factory=list)    # {status, company, title}
    decisions: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        log.error(msg)
        self.errors.append(msg)

    def note(self, msg: str) -> None:
        log.info(msg)
        self.notes.append(msg)

    @property
    def has_content(self) -> bool:
        return bool(self.scored or self.dossiers or self.events or self.errors
                    or self.ingest.get("nouvelles") or self.decisions)


class Context:
    def __init__(self, s: Settings | None = None, dry_run: bool = False):
        self.s = s or settings()
        self.dry_run = dry_run
        self.report = RunReport()
        self.s.out_dir.mkdir(parents=True, exist_ok=True)

    @cached_property
    def at(self) -> Airtable:
        if not self.s.airtable_pat:
            raise RuntimeError("AIRTABLE_PAT manquant")
        return Airtable(self.s.airtable_pat, self.s.airtable_base, dry_run=self.dry_run)

    @cached_property
    def claude(self) -> Claude:
        if not self.s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY manquant")
        return Claude(self.s.anthropic_api_key, self.s.model_main, self.s.model_fast)

    def gmail(self) -> Gmail:
        if not (self.s.gmail_user and self.s.gmail_app_password):
            raise RuntimeError("GMAIL_USER / GMAIL_APP_PASSWORD manquants")
        return Gmail(self.s.gmail_user, self.s.gmail_app_password, label_done=self.s.label_done)

    @cached_property
    def profile_md(self) -> str:
        return self.s.profile_file.read_text(encoding="utf-8")

    @property
    def offres(self) -> str:
        return self.s.t_offres

    @property
    def candidatures(self) -> str:
        return self.s.t_candidatures

    @property
    def a_traiter(self) -> str:
        return self.s.t_a_traiter

    def out(self, *parts: str) -> Path:
        p = self.s.out_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p
