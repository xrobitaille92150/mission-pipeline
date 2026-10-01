"""Récupération d'une fiche de poste LinkedIn sans authentification (endpoint public « jobs-guest »).

Zone grise connue : l'endpoint est non documenté, rate-limité par IP et peut renvoyer une
page vide. Le pipeline est conçu pour fonctionner en mode dégradé (titre + employeur + lieu)
quand la description n'est pas disponible, et la description peut être collée à la main
dans le champ Airtable « Description » : elle n'est alors jamais re-téléchargée.
"""
from __future__ import annotations

import html as html_lib
import random
import re
import time

import requests

from mp.gmail import clean_mode
from mp.models import JobDescription

JOBS_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
JOB_ID_RE = re.compile(r"(?:jobs/view/|currentJobId=|jobPosting/)(\d{6,})")
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]


def extract_job_id(text: str) -> str | None:
    m = JOB_ID_RE.search(text or "")
    return m.group(1) if m else None


def _clean(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def parse_job_html(html: str) -> JobDescription:
    jd = JobDescription()
    if not html or len(html) < 500:
        jd.error = "page vide"
        return jd
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)(?:<button|</section>|</div>\s*</div>)', html, re.S | re.I)
    if m:
        jd.text = _clean(m.group(1))[:12000]
    tm = re.search(r'top-card-layout__title[^>]*>(.*?)</h', html, re.S | re.I)
    if tm:
        jd.title = _clean(tm.group(1))[:200]
    cm = re.search(r'topcard__org-name-link[^>]*>(.*?)</a', html, re.S | re.I)
    if cm:
        jd.company = _clean(cm.group(1))[:120]
    lm = re.search(r'topcard__flavor--bullet[^>]*>(.*?)</span', html, re.S | re.I)
    if lm:
        jd.location = _clean(lm.group(1))[:120]
    for h, v in re.findall(
        r'job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*job-criteria-text[^>]*>(.*?)</span>',
        html, re.S | re.I,
    ):
        jd.criteria[_clean(h)] = _clean(v)
    for key, val in jd.criteria.items():
        if re.search(r"télétravail|remote|workplace|distance", key, re.I):
            jd.mode = clean_mode(val)
    if not jd.mode:
        jd.mode = clean_mode(" ".join([jd.location, jd.text[:600]]))
    jd.easy_apply = bool(re.search(r"candidature simplifiée|easy apply", html, re.I))
    jd.ok = len(jd.text) > 200
    if not jd.ok and not jd.error:
        jd.error = "description absente du HTML"
    return jd


def fetch_jd(job_id: str, timeout: int = 25, retries: int = 2, pause: float = 1.5) -> JobDescription:
    """Télécharge et parse la fiche. Jamais d'exception : `ok=False` + `error` en cas d'échec."""
    if not job_id:
        return JobDescription(error="jobId vide")
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                JOBS_GUEST.format(job_id), timeout=timeout,
                headers={"User-Agent": random.choice(USER_AGENTS),
                         "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8", "Accept": "text/html"},
            )
            if r.status_code == 200:
                jd = parse_job_html(r.text)
                if jd.ok or attempt == retries:
                    return jd
                last = jd.error
            elif r.status_code in (429, 999, 503):
                last = f"HTTP {r.status_code} (rate-limit LinkedIn)"
                time.sleep(pause * 4 * attempt)
                continue
            else:
                last = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    return JobDescription(error="offre introuvable (404) — probablement expirée")
        except requests.RequestException as e:
            last = f"réseau : {e.__class__.__name__}"
        time.sleep(pause * attempt)
    return JobDescription(error=last or "échec inconnu")
