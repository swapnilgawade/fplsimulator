"""FPL Simulator web server.

Usage: .venv/bin/python server.py [port]   (default 8000)
"""
import json
import os
import shutil
import sys
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from fpl import data, model, optimizer, team

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
MAX_HORIZON = 8

_lock = threading.Lock()
_state = {}


def load_data(force=False):
    with _lock:
        if force or "bs" not in _state:
            if force and os.path.isdir(data.CACHE_DIR):
                shutil.rmtree(data.CACHE_DIR)
            bs = data.bootstrap()
            _state.update(bs=bs, fx=data.fixtures(), hist=data.player_histories(bs), proj={})
        return _state


def projections(overrides=None):
    st = load_data()
    overrides = {int(k): v for k, v in (overrides or {}).items() if v not in (None, "")}
    key = json.dumps(sorted(overrides.items()))
    if key not in st["proj"]:
        st["proj"][key] = model.project(st["bs"], st["fx"], st["hist"], horizon=MAX_HORIZON,
                                        minutes_override=overrides)
    return st["proj"][key]


def api_projections(body):
    p = projections(body.get("minutes_override"))
    events = {e["id"]: e for e in load_data()["bs"]["events"]}
    nxt = events.get(p["gws"][0]) if p["gws"] else None
    return dict(p, next_deadline=nxt and nxt["deadline_time"])


def api_team(body):
    return team.load_team(int(body["entry_id"]), load_data()["bs"])


def api_optimise(body):
    proj = projections(body.get("minutes_override"))
    squad = body.get("squad")
    kwargs = dict(
        bank=float(body.get("bank", 100.0)),
        free_transfers=int(body.get("free_transfers", 1)),
        horizon=min(int(body.get("horizon", 5)), MAX_HORIZON),
        chips=body.get("chips") or {},
        locked=[int(x) for x in body.get("locked", [])],
        banned=[int(x) for x in body.get("banned", [])],
        decay=float(body.get("decay", 0.9)),
        max_hits_per_gw=int(body.get("max_hits", 1)),
        transfer_penalty=float(body.get("transfer_penalty", 0.3)),
    )
    result = optimizer.optimise(proj, squad=squad, **kwargs)
    if squad:
        # baseline: keep the current squad, only pick XI and captain
        base_kwargs = dict(kwargs, chips={k: v for k, v in kwargs["chips"].items() if v in ("bboost", "3xc")},
                           locked=[])
        base = optimizer.optimise(proj, squad=squad, max_transfers_total=0, **base_kwargs)
        result["baseline_xpts"] = base["total_xpts"]
        result["baseline_by_gw"] = [g["net_xpts"] for g in base["plan"]]
    return result


def api_refresh(body):
    load_data(force=True)
    return {"ok": True}


ROUTES = {"/api/projections": api_projections, "/api/team": api_team,
          "/api/optimise": api_optimise, "/api/refresh": api_refresh}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC, **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, body):
        path = urlparse(self.path).path
        fn = ROUTES.get(path)
        if not fn:
            return self._json(404, {"error": "not found"})
        try:
            self._json(200, fn(body))
        except Exception as e:  # surface errors to the UI
            traceback.print_exc()
            msg = str(e)
            if "404" in msg:
                msg = "Team not found. Check the team ID (the number in your FPL points page URL)."
            self._json(400, {"error": msg})

    def do_GET(self):
        if self.path.startswith("/api/"):
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            return self._dispatch(q)
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        self._dispatch(body)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("Loading FPL data (first run fetches every player's history, ~15s)...")
    load_data()
    projections()
    print(f"FPL Simulator running at http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
