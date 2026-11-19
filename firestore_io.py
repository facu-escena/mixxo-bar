"""Lee y escribe los documentos de Banner Director en Cloud Firestore."""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = "banner-director-mixxo"
ACCOUNT = "fv@bannerdirector.com"
# Cliente público de Firebase CLI. El refresh token queda en la sesión local.
CLIENT_ID = "563584335869-fgrhgmd47bqnekij5i8b5pr03ho849e6.apps.googleusercontent.com"
CLIENT_SECRET = "j9iVZfS8kkCEFUPaAeJV0sAi"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CONFIG_PATH = Path.home() / ".config" / "configstore" / "firebase-tools.json"

_lock = threading.Lock()
_access = {"token": "", "expires_at": 0}


def _refresh_token():
    env = os.environ.get("FIREBASE_REFRESH_TOKEN", "").strip()
    if env:
        return env
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    accounts = [data]
    accounts.extend(data.get("additionalAccounts") or [])
    for account in accounts:
        user = account.get("user") or {}
        tokens = account.get("tokens") or {}
        if user.get("email") == ACCOUNT and tokens.get("refresh_token"):
            return tokens["refresh_token"]
    raise RuntimeError(f"No hay sesión de Firebase para {ACCOUNT}")


def _access_token():
    with _lock:
        if _access["token"] and _access["expires_at"] > time.time() + 60:
            return _access["token"]
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token(),
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }).encode()
        request = urllib.request.Request(TOKEN_URL, data=body)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        _access["token"] = payload["access_token"]
        _access["expires_at"] = time.time() + int(payload.get("expires_in", 3600))
        return _access["token"]


def _encode(value):
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if value is None:
        return {"nullValue": None}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_encode(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {key: _encode(item) for key, item in value.items()}}}
    return {"stringValue": str(value)}


def _decode(value):
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "nullValue" in value:
        return None
    if "arrayValue" in value:
        return [_decode(item) for item in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        fields = value["mapValue"].get("fields", {})
        return {key: _decode(item) for key, item in fields.items()}
    raise ValueError("valor de Firestore no reconocido")


def _document(doc_id):
    return (
        f"projects/{PROJECT}/databases/(default)/documents/state/{doc_id}"
    )


def _request(url, method, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + _access_token(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        if error.code == 404:
            return 404, {}
        raise RuntimeError(f"Firestore respondió {error.code}: {detail[:240]}") from error


def pull(doc_id):
    status, payload = _request(
        "https://firestore.googleapis.com/v1/" + _document(doc_id),
        "GET",
    )
    if status == 404 or "fields" not in payload:
        return None
    return _decode({"mapValue": {"fields": payload["fields"]}})


def push(doc_id, data):
    _request(
        f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents:commit",
        "POST",
        {
            "writes": [{
                "update": {
                    "name": _document(doc_id),
                    "fields": _encode(data)["mapValue"]["fields"],
                }
            }]
        },
    )
