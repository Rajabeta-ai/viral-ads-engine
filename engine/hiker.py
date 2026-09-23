"""Thin HikerAPI client: disk-cached, retrying, thread-safe."""
import os, json, time, hashlib, threading
import requests

BASE = "https://api.hikerapi.com"
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "raw", ".cache")
os.makedirs(CACHE, exist_ok=True)

_lock = threading.Lock()
STATS = {"calls": 0, "cache_hits": 0, "errors": 0}


def _key():
    k = os.environ.get("HIKER_API_KEY")
    if not k:
        raise RuntimeError("HIKER_API_KEY not set")
    return k


def get(path, params=None, ttl=6 * 3600, retries=3):
    """GET a HikerAPI path. Returns parsed JSON or None on failure."""
    params = params or {}
    sig = hashlib.sha1(f"{path}|{sorted(params.items())}".encode()).hexdigest()[:20]
    fp = os.path.join(CACHE, f"{sig}.json")

    if ttl and os.path.exists(fp) and time.time() - os.path.getmtime(fp) < ttl:
        with _lock:
            STATS["cache_hits"] += 1
        try:
            with open(fp) as f:
                return json.load(f)
        except Exception:
            pass

    last = None
    for attempt in range(retries):
        try:
            r = requests.get(
                f"{BASE}/{path.lstrip('/')}",
                params=params,
                headers={"accept": "application/json", "x-access-key": _key()},
                timeout=45,
            )
            with _lock:
                STATS["calls"] += 1
            if r.status_code == 200:
                data = r.json()
                with open(fp, "w") as f:
                    json.dump(data, f)
                return data
            # 404 = genuinely nothing there; don't burn retries
            if r.status_code == 404:
                return None
            last = f"HTTP {r.status_code}: {r.text[:180]}"
        except Exception as e:  # network/json
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))

    with _lock:
        STATS["errors"] += 1
    print(f"  ! hiker {path} {params} -> {last}")
    return None


# ---- typed helpers -------------------------------------------------------

def hashtag_top(name, amount=30):
    return get("/v1/hashtag/medias/top", {"name": name, "amount": amount}) or []


def hashtag_recent(name, amount=30):
    return get("/v1/hashtag/medias/recent", {"name": name, "amount": amount}, ttl=2 * 3600) or []


def user_by_username(username):
    return get("/v1/user/by/username", {"username": username}, ttl=7 * 86400)


def user_clips(user_id, amount=20):
    return get("/v1/user/clips", {"user_id": user_id, "amount": amount}) or []


def search_hashtags(query):
    return get("/v1/search/hashtags", {"query": query}, ttl=7 * 86400) or []
