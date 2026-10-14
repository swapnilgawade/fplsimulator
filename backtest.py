"""Backtest the projection model: project each past GW using only data before it.

Usage: .venv/bin/python backtest.py
Note: injury news isn't available historically, so availability is ignored for
all players (which flatters nobody; both model and baseline see the same info).
"""
import copy
import math

from fpl import data, model


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb)


def main():
    bs = data.bootstrap()
    fx = data.fixtures()
    hist = data.player_histories(bs)
    bs_bt = copy.deepcopy(bs)
    for p in bs_bt["elements"]:
        p["status"], p["chance_of_playing_next_round"], p["news"] = "a", None, ""

    last = max(e["id"] for e in bs["events"] if e["finished"])
    print(f"{'GW':>3} {'n':>4} {'model r':>8} {'ppg r':>8} {'model MAE':>10} {'ppg MAE':>8} {'top-50 hit':>10}")
    for gw in range(3, last + 1):
        proj = model.project(bs_bt, fx, hist, as_of_gw=gw - 1, horizon=1)
        pred = {p["id"]: p["xpts"][0] for p in proj["players"]}
        actual, ppg = {}, {}
        for pid, rows in hist.items():
            cur = [r for r in rows if r["round"] == gw]
            prev = [r for r in rows if r["round"] < gw]
            if not cur or not prev:
                continue
            actual[pid] = sum(r["total_points"] for r in cur)
            ppg[pid] = sum(r["total_points"] for r in prev) / len(prev)
        # evaluate on players who had played this season before the GW
        ids = [i for i in actual if any(r["minutes"] > 0 for r in hist[i] if r["round"] < gw)]
        a = [actual[i] for i in ids]
        m = [pred.get(i, 0) for i in ids]
        b = [ppg[i] for i in ids]
        top_model = sorted(ids, key=lambda i: -pred.get(i, 0))[:50]
        top_actual = set(sorted(ids, key=lambda i: -actual[i])[:50])
        print(f"{gw:>3} {len(ids):>4} {corr(m, a):>8.3f} {corr(b, a):>8.3f} "
              f"{sum(abs(x - y) for x, y in zip(m, a)) / len(a):>10.3f} "
              f"{sum(abs(x - y) for x, y in zip(b, a)) / len(a):>8.3f} "
              f"{len(top_actual & set(top_model)):>10}")


if __name__ == "__main__":
    main()
