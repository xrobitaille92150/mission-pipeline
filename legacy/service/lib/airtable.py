"""Client Airtable REST minimal — list (pagination) + patch. Champs par NOM (compatibles inter-tables)."""
import json
import urllib.parse
import urllib.request
import urllib.error

from .config import AIRTABLE_BASE, load_env
from .net import urlopen_retry

_PAT = None


def _pat() -> str:
    global _PAT
    if _PAT is None:
        _PAT = load_env("airtable.env", "AIRTABLE_PAT")
    return _PAT


def _headers(json_body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {_pat()}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def list_records(table: str, fields=None, formula: str = None, page_size: int = 100) -> list:
    """Liste tous les records (pagination). Retourne [{id, fields:{...}}]."""
    out, offset = [], None
    base_params = []
    for f in (fields or []):
        base_params.append(("fields[]", f))
    if formula:
        base_params.append(("filterByFormula", formula))
    base_params.append(("pageSize", str(page_size)))
    while True:
        params = list(base_params)
        if offset:
            params.append(("offset", offset))
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=_headers())
        with urlopen_retry(req, timeout=60) as r:
            data = json.loads(r.read())
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return out


def create(table: str, fields_list: list) -> int:
    """CREATE en batch de 10 (limite Airtable), typecast=true (résout les selects par nom).
    Retourne le nombre de records créés. Lève sur erreur (fail-loud)."""
    created = 0
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}"
    for i in range(0, len(fields_list), 10):
        batch = [{"fields": f} for f in fields_list[i:i + 10]]
        body = json.dumps({"records": batch, "typecast": True}).encode()
        req = urllib.request.Request(url, data=body, headers=_headers(json_body=True), method="POST")
        with urlopen_retry(req, timeout=60) as r:
            created += len(json.loads(r.read()).get("records", []))
    return created


def upsert(table: str, fields_list: list, merge_on: list) -> tuple:
    """UPSERT en batch de 10 (PATCH + performUpsert, typecast). merge_on = noms de champs.
    Retourne (créés, mis à jour). Lève sur erreur (fail-loud)."""
    created, updated = 0, 0
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}"
    for i in range(0, len(fields_list), 10):
        batch = [{"fields": f} for f in fields_list[i:i + 10]]
        body = json.dumps({"performUpsert": {"fieldsToMergeOn": merge_on},
                           "records": batch, "typecast": True}).encode()
        req = urllib.request.Request(url, data=body, headers=_headers(json_body=True), method="PATCH")
        with urlopen_retry(req, timeout=60) as r:
            data = json.loads(r.read())
        created += len(data.get("createdRecords", []))
        updated += len(data.get("updatedRecords", []))
    return created, updated


def delete(table: str, record_ids: list) -> int:
    """DELETE en batch de 10 (limite Airtable). Retourne le nombre supprimé. Lève sur erreur (fail-loud)."""
    import time
    deleted = 0
    for i in range(0, len(record_ids), 10):
        batch = record_ids[i:i + 10]
        qs = urllib.parse.urlencode([("records[]", rid) for rid in batch])
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}?{qs}"
        req = urllib.request.Request(url, headers=_headers(), method="DELETE")
        with urlopen_retry(req, timeout=60) as r:
            data = json.loads(r.read())
        deleted += sum(1 for x in data.get("records", []) if x.get("deleted"))
        time.sleep(0.25)  # rate-limit Airtable (5 req/s)
    return deleted


def patch(table: str, record_id: str, fields: dict) -> dict:
    """PATCH un record. Lève sur erreur (fail-loud)."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}/{record_id}"
    body = json.dumps({"fields": fields}).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(json_body=True), method="PATCH")
    with urlopen_retry(req, timeout=60) as r:
        return json.loads(r.read())
