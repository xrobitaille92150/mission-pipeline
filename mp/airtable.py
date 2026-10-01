"""Client Airtable minimal : records (lecture, upsert, patch), pièces jointes, Meta API.

Les champs sont adressés par NOM (lisible dans l'UI Airtable, `typecast=True` résout les
options de sélection). La liste des champs attendus par le pipeline vit dans `OFFRES_FIELDS`
et `CANDIDATURES_FIELDS` ; `mp airtable-setup` crée ceux qui manquent.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path

import requests

API = "https://api.airtable.com/v0"
CONTENT = "https://content.airtable.com/v0"
META = "https://api.airtable.com/v0/meta/bases"


class AirtableError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Schéma attendu (créé si absent par `mp airtable-setup --apply`)
# ---------------------------------------------------------------------------

STATUTS = ["Nouvelle", "À étudier", "Dossier prêt", "Postulée", "Écartée", "Expirée"]
REPONSES = ["Néant", "Envoyé", "A/R", "Oui", "Non"]
CLUSTERS = ["A — Assurance/Passif", "B — Investissement/Actif", "C — Transformation/PMO", "Hors-axe"]


def _select(name: str, choices: list[str], description: str = "") -> dict:
    return {"name": name, "type": "singleSelect", "description": description,
            "options": {"choices": [{"name": c} for c in choices]}}


def _text(name: str, description: str = "", multi: bool = False) -> dict:
    return {"name": name, "type": "multilineText" if multi else "singleLineText", "description": description}


def _date(name: str, description: str = "") -> dict:
    return {"name": name, "type": "date", "description": description,
            "options": {"dateFormat": {"name": "european", "format": "D/M/YYYY"}}}


def _number(name: str, description: str = "") -> dict:
    return {"name": name, "type": "number", "description": description, "options": {"precision": 0}}


def _attach(name: str, description: str = "") -> dict:
    return {"name": name, "type": "multipleAttachments", "description": description}


# Champs de la table Offres (ex-« Veille 2 »). Les champs historiques (jobId, Employeur, Poste,
# Lieu, Mode, Score, Easy Apply, URL, Date 1ère vue, Préparer dossier, Je postule, J'écarte,
# Recherche LI) sont réutilisés tels quels.
OFFRES_FIELDS: list[dict] = [
    _select("Statut", STATUTS, "Cycle de vie de l'offre, tenu par le pipeline."),
    _select("Verdict IA", ["Postuler", "Étudier", "Écarter"], "Recommandation de Claude."),
    _select("Cluster", CLUSTERS, "Axe d'expertise (même nomenclature que Candidatures.Axe)."),
    _select("Posture", ["Projet", "Production", "Mixte"]),
    _select("Langue", ["FR", "EN", "Autre"], "Langue de l'annonce."),
    _text("Pays"),
    _select("Contrat", ["Freelance", "CDI", "Intérim", "CDD", "Non précisé"]),
    _select("Source", ["Alerte", "Recommandation", "Enregistrée", "Manuelle"], "Email LinkedIn d'origine."),
    _text("Pourquoi", "3 raisons du score, en français.", multi=True),
    _text("Red flags", multi=True),
    _text("Mots-clés", "Mots-clés de l'annonce à refléter dans le CV."),
    _select("Profil CV", ["FinanceTransformation", "AssetManagement", "IFRS17SolvencyII"]),
    _text("Description", "Fiche de poste (téléchargée, ou collée à la main si LinkedIn bloque).", multi=True),
    _attach("CV (fichiers)", "CV adapté : PDF + DOCX éditable."),
    _attach("Lettre (fichiers)", "Lettre / cover text : PDF + DOCX éditable."),
    _text("Lettre texte", "Texte de la lettre, à coller dans le formulaire de candidature.", multi=True),
    _text("Objections", "Objections prévisibles + réponse en une ligne (hors lettre).", multi=True),
    _date("Scoré le"),
    _date("Dossier le"),
    _date("Date postulé"),
    _select("Réponse", REPONSES, "Entonnoir : Néant → Envoyé → A/R → Oui / Non (jamais de retour en arrière)."),
    _date("Date réponse"),
    _date("Dernière vue", "Dernière apparition dans un email LinkedIn."),
    _number("Rang du jour", "1 = meilleure offre du run."),
    _text("Erreur", "Dernière erreur rencontrée par le pipeline sur cette offre.", multi=True),
]

CANDIDATURES_FIELDS: list[dict] = [
    _text("Job ID", "Identifiant LinkedIn (lien avec la table Offres)."),
    _text("Dernier email", "Id Gmail du dernier email pris en compte."),
]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class Airtable:
    def __init__(self, pat: str, base: str, dry_run: bool = False):
        self.pat = pat
        self.base = base
        self.dry_run = dry_run
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {pat}"})

    # -- bas niveau ----------------------------------------------------------
    def _req(self, method: str, url: str, *, retries: int = 4, **kw) -> dict:
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                r = self._s.request(method, url, timeout=60, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(2 * attempt)
                continue
            if r.status_code == 429:
                time.sleep(31)
                continue
            if r.status_code >= 500:
                last = AirtableError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(3 * attempt)
                continue
            if r.status_code >= 400:
                raise AirtableError(f"HTTP {r.status_code} {method} {url.split('/v0/')[-1][:60]}: {r.text[:400]}")
            time.sleep(0.21)  # 5 req/s max
            return r.json() if r.content else {}
        raise AirtableError(f"échec après {retries} tentatives : {last}")

    # -- records -------------------------------------------------------------
    def list(self, table: str, *, fields: list[str] | None = None, formula: str | None = None,
             sort: list[tuple[str, str]] | None = None, max_records: int | None = None,
             view: str | None = None) -> list[dict]:
        params: list[tuple[str, str]] = [("pageSize", "100")]
        for f in fields or []:
            params.append(("fields[]", f))
        if formula:
            params.append(("filterByFormula", formula))
        if view:
            params.append(("view", view))
        for i, (f, d) in enumerate(sort or []):
            params.append((f"sort[{i}][field]", f))
            params.append((f"sort[{i}][direction]", d))
        out: list[dict] = []
        offset = None
        while True:
            p = list(params) + ([("offset", offset)] if offset else [])
            data = self._req("GET", f"{API}/{self.base}/{table}", params=p)
            out.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset or (max_records and len(out) >= max_records):
                break
        return out[:max_records] if max_records else out

    def get(self, table: str, record_id: str) -> dict:
        return self._req("GET", f"{API}/{self.base}/{table}/{record_id}")

    def create(self, table: str, records: list[dict]) -> list[dict]:
        if self.dry_run or not records:
            return []
        out = []
        for i in range(0, len(records), 10):
            body = {"records": [{"fields": f} for f in records[i:i + 10]], "typecast": True}
            out.extend(self._req("POST", f"{API}/{self.base}/{table}", json=body).get("records", []))
        return out

    def upsert(self, table: str, records: list[dict], merge_on: list[str]) -> tuple[int, int]:
        if self.dry_run or not records:
            return 0, 0
        created = updated = 0
        for i in range(0, len(records), 10):
            body = {"performUpsert": {"fieldsToMergeOn": merge_on},
                    "records": [{"fields": f} for f in records[i:i + 10]], "typecast": True}
            data = self._req("PATCH", f"{API}/{self.base}/{table}", json=body)
            created += len(data.get("createdRecords", []))
            updated += len(data.get("updatedRecords", []))
        return created, updated

    def patch(self, table: str, record_id: str, fields: dict) -> dict:
        if self.dry_run:
            return {"id": record_id, "fields": fields}
        return self._req("PATCH", f"{API}/{self.base}/{table}/{record_id}",
                         json={"fields": fields, "typecast": True})

    def patch_many(self, table: str, updates: list[dict]) -> int:
        """updates = [{"id": rec, "fields": {...}}]"""
        if self.dry_run or not updates:
            return 0
        n = 0
        for i in range(0, len(updates), 10):
            data = self._req("PATCH", f"{API}/{self.base}/{table}",
                             json={"records": updates[i:i + 10], "typecast": True})
            n += len(data.get("records", []))
        return n

    def delete(self, table: str, record_ids: list[str]) -> int:
        if self.dry_run or not record_ids:
            return 0
        n = 0
        for i in range(0, len(record_ids), 10):
            params = [("records[]", r) for r in record_ids[i:i + 10]]
            data = self._req("DELETE", f"{API}/{self.base}/{table}", params=params)
            n += sum(1 for x in data.get("records", []) if x.get("deleted"))
        return n

    # -- pièces jointes --------------------------------------------------------
    def attach(self, table: str, record_id: str, field: str, path: Path) -> dict:
        """Upload direct d'un fichier (≤ 5 Mo) dans un champ pièce jointe. Les fichiers
        précédents du champ sont conservés (Airtable ajoute)."""
        if self.dry_run:
            return {}
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = {"contentType": ctype, "filename": path.name,
                "file": base64.b64encode(path.read_bytes()).decode("ascii")}
        return self._req("POST", f"{CONTENT}/{self.base}/{record_id}/{requests.utils.quote(field)}/uploadAttachment",
                         json=body)

    def replace_attachments(self, table: str, record_id: str, field: str, paths: list[Path]) -> None:
        """Vide le champ puis y dépose les fichiers (pour ne pas empiler les versions)."""
        if self.dry_run:
            return
        self.patch(table, record_id, {field: []})
        for p in paths:
            self.attach(table, record_id, field, p)

    # -- schéma (Meta API) -----------------------------------------------------
    def tables(self) -> list[dict]:
        return self._req("GET", f"{META}/{self.base}/tables").get("tables", [])

    def table_fields(self, table_id: str) -> dict[str, dict]:
        for t in self.tables():
            if t["id"] == table_id or t["name"] == table_id:
                return {f["name"]: f for f in t["fields"]}
        raise AirtableError(f"table introuvable : {table_id}")

    def create_field(self, table_id: str, spec: dict) -> dict:
        if self.dry_run:
            return spec
        body = {k: v for k, v in spec.items() if k != "description" or v}
        return self._req("POST", f"{META}/{self.base}/tables/{table_id}/fields", json=body)

    def missing_fields(self, table_id: str, wanted: list[dict]) -> list[dict]:
        existing = {n.lower() for n in self.table_fields(table_id)}
        return [f for f in wanted if f["name"].lower() not in existing]


# ---------------------------------------------------------------------------
# Helpers de formules (échappement)
# ---------------------------------------------------------------------------


def fstr(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def record_url(base: str, table: str, record_id: str) -> str:
    return f"https://airtable.com/{base}/{table}/{record_id}"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
