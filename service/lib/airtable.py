"""Client Airtable REST minimal — list (pagination) + patch. Champs par NOM (compatibles inter-tables)."""
import json
import urllib.parse
import urllib.request
import urllib.error

from .config import AIRTABLE_BASE, load_env

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
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return out


def patch(table: str, record_id: str, fields: dict) -> dict:
    """PATCH un record. Lève sur erreur (fail-loud)."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{table}/{record_id}"
    body = json.dumps({"fields": fields}).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(json_body=True), method="PATCH")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())
