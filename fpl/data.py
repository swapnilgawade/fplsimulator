"""Fetching and caching data from the public FPL API."""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://fantasy.premierleague.com/api"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
HEADERS = {"User-Agent": "Mozilla/5.0 (fplsimulator)"}


def _get(path, retries=3):
    url = f"{BASE}/{path}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def _cached(name, path, max_age):
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, name)
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < max_age:
        with open(f) as fh:
            return json.load(fh)
    data = _get(path)
    with open(f, "w") as fh:
        json.dump(data, fh)
    return data


def bootstrap(max_age=3600):
    return _cached("bootstrap.json", "bootstrap-static/", max_age)


def fixtures(max_age=3600):
    return _cached("fixtures.json", "fixtures/", max_age)


def player_histories(bs, max_age=6 * 3600):
    """Per-gameweek history for every player who has played this season.

    Returns {player_id: [history rows]}. Cached as one file; refreshed when stale
    or when a new gameweek has finished since the cache was written.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, "histories.json")
    last_finished = max([e["id"] for e in bs["events"] if e["finished"]] or [0])
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < max_age:
        with open(f) as fh:
            cached = json.load(fh)
        if cached.get("last_finished") == last_finished:
            return {int(k): v for k, v in cached["histories"].items()}

    ids = [p["id"] for p in bs["elements"] if p["minutes"] > 0]

    def fetch(pid):
        try:
            return pid, _get(f"element-summary/{pid}/")["history"]
        except Exception:
            return pid, []

    with ThreadPoolExecutor(max_workers=8) as ex:
        histories = dict(ex.map(fetch, ids))
    with open(f, "w") as fh:
        json.dump({"last_finished": last_finished, "histories": histories}, fh)
    return histories


# --- Manager (entry) endpoints: never cached long, they change every gameweek ---

def entry(entry_id):
    return _get(f"entry/{entry_id}/")


def entry_history(entry_id):
    return _get(f"entry/{entry_id}/history/")


def entry_picks(entry_id, gw):
    return _get(f"entry/{entry_id}/event/{gw}/picks/")


def entry_transfers(entry_id):
    return _get(f"entry/{entry_id}/transfers/")
