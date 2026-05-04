import time
import json



_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = 60  # seconds


def _evict_expired():
    """Remove all entries older than TTL. Called on every set."""
    now = time.time()
    expired = [k for k, (ts, _) in _cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _cache[k]


def cache_get(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > CACHE_TTL:
        del _cache[key]
        return None
    return value


def cache_set(key: str, value):
    _evict_expired()
    _cache[key] = (time.time(), value)


def cache_invalidate_all():
    """Call this after any write (create/delete/upload) to flush stale results."""
    _cache.clear()




def normalize_filters(filters: dict) -> dict:
    
    normalized = {}

    if filters.get("gender"):
        normalized["gender"] = filters["gender"].lower().strip()

    if filters.get("age_group"):
        normalized["age_group"] = filters["age_group"].lower().strip()

    if filters.get("country_id"):
        normalized["country_id"] = filters["country_id"].upper().strip()

    if filters.get("min_age") is not None:
        normalized["min_age"] = int(filters["min_age"])

    if filters.get("max_age") is not None:
        normalized["max_age"] = int(filters["max_age"])

    if filters.get("min_gender_probability") is not None:
        normalized["min_gender_probability"] = float(filters["min_gender_probability"])

    if filters.get("min_country_probability") is not None:
        normalized["min_country_probability"] = float(filters["min_country_probability"])

    return normalized


def make_cache_key(endpoint: str, filters: dict, page: int, limit: int) -> str:
    
    normalized = normalize_filters(filters)
    return f"{endpoint}:{json.dumps(normalized, sort_keys=True)}:p{page}:l{limit}"