import hashlib
import json
from pathlib import Path
import sys

from django.conf import settings


DASHBOARD_CACHE_DIR = Path(settings.BASE_DIR) / "memory" / "dashboard_cache"
CACHE_FORMAT_VERSION = 1


def _normalize_params(params):
    normalized = []
    if hasattr(params, "lists"):
        items = params.lists()
    else:
        items = params.items()
    for key, values in items:
        if key == "refresh":
            continue
        if isinstance(values, (list, tuple)):
            cleaned = sorted(str(value) for value in values if str(value))
        else:
            cleaned = [str(values)] if str(values) else []
        if cleaned:
            normalized.append((str(key), cleaned))
    return sorted(normalized)


def cache_key(scope, params=None):
    payload = {
        "scope": scope,
        "params": _normalize_params(params or {}),
        "version": CACHE_FORMAT_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def cache_path(scope, params=None):
    return DASHBOARD_CACHE_DIR / scope / f"{cache_key(scope, params)}.json"


def read_json_cache(scope, params=None):
    path = cache_path(scope, params)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json_cache(scope, params, payload):
    path = cache_path(scope, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    return path


def load_or_build_json_cache(scope, params, builder, refresh=False):
    if "test" in sys.argv:
        return builder(), False
    if not refresh:
        cached = read_json_cache(scope, params)
        if cached is not None:
            return cached, True
    payload = builder()
    write_json_cache(scope, params, payload)
    return payload, False


def clear_dashboard_cache():
    if not DASHBOARD_CACHE_DIR.exists():
        return 0
    deleted = 0
    for path in DASHBOARD_CACHE_DIR.rglob("*.json"):
        path.unlink(missing_ok=True)
        deleted += 1
    return deleted
