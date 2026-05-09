import os
import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"


def query(table, select="*", filters=None, order=None, limit=None):
    """GET query on a table."""
    params = {"select": select}
    headers = _headers()

    if filters:
        for key, val in filters.items():
            if isinstance(val, list):
                params[key] = f"in.({','.join(str(v) for v in val)})"
            elif isinstance(val, dict):
                for op, v in val.items():
                    params[key] = f"{op}.{v}"
            else:
                params[key] = f"eq.{val}"

    if order:
        params["order"] = order
    if limit:
        params["limit"] = str(limit)

    resp = requests.get(_url(table), headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def query_count(table, filters=None):
    """Count rows in a table."""
    headers = _headers()
    headers["Prefer"] = "count=exact"
    params = {"select": "*", "head": "true"}

    if filters:
        for key, val in filters.items():
            if isinstance(val, list):
                params[key] = f"in.({','.join(str(v) for v in val)})"
            else:
                params[key] = f"eq.{val}"

    resp = requests.get(_url(table), headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    # Count is in content-range header
    cr = resp.headers.get("content-range", "")
    if "/" in cr:
        return int(cr.split("/")[1])
    return 0


def insert(table, data):
    """INSERT row(s)."""
    resp = requests.post(_url(table), headers=_headers(), json=data, timeout=10)
    resp.raise_for_status()
    return resp.json()


def update(table, filters, data):
    """UPDATE rows matching filters."""
    params = {}
    for key, val in filters.items():
        params[key] = f"eq.{val}"

    resp = requests.patch(_url(table), headers=_headers(), params=params, json=data, timeout=10)
    resp.raise_for_status()
    return resp.json()


def is_configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
