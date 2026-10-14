"""Multi-gameweek squad / transfer optimiser (mixed-integer program via PuLP + CBC).

Decides, for each gameweek in the horizon: transfers in/out, the starting XI,
captain, and whether to take hits, maximising discounted expected points.
Tracks free-transfer banking (max 5), bank balance with real selling prices,
squad rules (2/5/5/3, max 3 per club, valid formations) and optional chips.
"""
import pulp

POS_COUNT = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
MAX_FT = 5
HIT = 4
POOL_SIZE = {1: 12, 2: 35, 3: 40, 4: 25}


def _pool(players, gws, keep_ids, decay):
    """Restrict to the players worth considering, to keep the MIP small."""
    score = lambda p: sum(x * decay ** i for i, x in enumerate(p["xpts"]))
    keep = set(keep_ids)
    for pos, n in POOL_SIZE.items():
        group = [p for p in players if p["pos_id"] == pos]
        keep.update(p["id"] for p in sorted(group, key=score, reverse=True)[:n])
        # cheap bench fodder
        cheapest = min(p["price"] for p in group)
        cheap = [p for p in group if p["price"] <= cheapest + 0.5]
        keep.update(p["id"] for p in sorted(cheap, key=score, reverse=True)[:4])
    return [p for p in players if p["id"] in keep]


def optimise(projection, squad=None, bank=100.0, free_transfers=1, horizon=None, chips=None,
             locked=(), banned=(), decay=0.9, bench_weight=0.1, max_hits_per_gw=2,
             transfer_penalty=0.3, max_transfers_total=None, time_limit=60):
    """
    projection: output of model.project
    squad: list of {"id", "sell"} for the current squad, or None to build from scratch
           (first gameweek then behaves like a wildcard with `bank` as the budget).
    chips: {gw: "wildcard" | "freehit" | "bboost" | "3xc"}
    transfer_penalty: small cost per transfer so marginal moves (<this many points)
                      are not made; rolling a transfer has option value.
    """
    chips = {int(k): v for k, v in (chips or {}).items()}
    gws = projection["gws"][:horizon] if horizon else projection["gws"]
    initial = {s["id"]: s["sell"] for s in (squad or [])}
    from_scratch = not initial
    if from_scratch:
        chips.setdefault(gws[0], "wildcard")

    players = _pool(projection["players"], gws, list(initial) + list(locked), decay)
    players = [p for p in players if p["id"] not in set(banned) or p["id"] in initial]
    P = [p["id"] for p in players]
    info = {p["id"]: p for p in players}
    xp = {(p["id"], g): p["xpts"][projection["gws"].index(g)] for p in players for g in gws}
    price = {p["id"]: p["price"] for p in players}
    sell = {i: initial.get(i, price[i]) for i in P}
    teams = sorted({p["team"] for p in players})

    prob = pulp.LpProblem("fpl", pulp.LpMaximize)
    B = pulp.LpBinary
    sq = pulp.LpVariable.dicts("squad", (P, gws), cat=B)
    xi = pulp.LpVariable.dicts("xi", (P, gws), cat=B)
    cap = pulp.LpVariable.dicts("cap", (P, gws), cat=B)
    tin = pulp.LpVariable.dicts("tin", (P, gws), cat=B)
    tout = pulp.LpVariable.dicts("tout", (P, gws), cat=B)
    fh = {g: pulp.LpVariable.dicts(f"fh{g}", P, cat=B) for g in gws if chips.get(g) == "freehit"}
    money = pulp.LpVariable.dicts("bank", gws, lowBound=0)
    ft = pulp.LpVariable.dicts("ft", gws + [gws[-1] + 1], lowBound=0, upBound=MAX_FT, cat="Integer")
    hits = pulp.LpVariable.dicts("hits", gws, lowBound=0, upBound=max_hits_per_gw, cat="Integer")
    n_tr = {g: pulp.lpSum(tin[i][g] for i in P) for g in gws}

    prob += ft[gws[0]] == (0 if from_scratch else free_transfers)

    obj = []
    for k, g in enumerate(gws):
        chip = chips.get(g)
        prev = (lambda i: 1 if i in initial else 0) if k == 0 else (lambda i, gp=gws[k - 1]: sq[i][gp])
        prev_bank = bank if k == 0 else money[gws[k - 1]]

        for i in P:
            prob += sq[i][g] == prev(i) + tin[i][g] - tout[i][g]
            prob += tin[i][g] + tout[i][g] <= 1
            if i in locked:
                prob += sq[i][g] == 1
            if i in banned:
                prob += tin[i][g] == 0
        prob += money[g] == prev_bank + pulp.lpSum(sell[i] * tout[i][g] - price[i] * tin[i][g] for i in P)

        for pos, n in POS_COUNT.items():
            prob += pulp.lpSum(sq[i][g] for i in P if info[i]["pos_id"] == pos) == n
        for t in teams:
            prob += pulp.lpSum(sq[i][g] for i in P if info[i]["team"] == t) <= 3

        # the squad that actually plays this week (free hit uses a temporary one)
        if chip == "freehit":
            play = fh[g]
            prob += n_tr[g] == 0
            prob += pulp.lpSum(price[i] * play[i] for i in P) <= prev_bank + pulp.lpSum(
                sell[i] * prev(i) for i in P)
            for pos, n in POS_COUNT.items():
                prob += pulp.lpSum(play[i] for i in P if info[i]["pos_id"] == pos) == n
            for t in teams:
                prob += pulp.lpSum(play[i] for i in P if info[i]["team"] == t) <= 3
        else:
            play = {i: sq[i][g] for i in P}

        for i in P:
            prob += xi[i][g] <= play[i]
            prob += cap[i][g] <= xi[i][g]
        prob += pulp.lpSum(cap[i][g] for i in P) == 1
        if chip == "bboost":
            for i in P:
                prob += xi[i][g] == play[i]
        else:
            prob += pulp.lpSum(xi[i][g] for i in P) == 11
            for pos in POS_COUNT:
                n = pulp.lpSum(xi[i][g] for i in P if info[i]["pos_id"] == pos)
                prob += n >= XI_MIN[pos]
                prob += n <= XI_MAX[pos]

        # free transfers and hits
        if chip in ("wildcard", "freehit"):
            prob += hits[g] == 0
            prob += ft[g + 1] <= ft[g] + 1
        else:
            prob += hits[g] >= n_tr[g] - ft[g]
            prob += ft[g + 1] <= ft[g] - n_tr[g] + hits[g] + 1

        w = decay ** k
        cap_mult = 2 if chip == "3xc" else 1
        obj.append(w * pulp.lpSum(xp[i, g] * (xi[i][g] + cap_mult * cap[i][g] + bench_weight * (play[i] - xi[i][g]))
                                  for i in P))
        obj.append(-HIT * hits[g])
        if chip not in ("wildcard", "freehit"):
            obj.append(-transfer_penalty * n_tr[g])
        obj.append(0.001 * ft[g + 1])  # tie-break so banked FTs are reported correctly

    if max_transfers_total is not None:
        prob += pulp.lpSum(n_tr[g] for g in gws if chips.get(g) not in ("wildcard", "freehit")) <= max_transfers_total

    prob += pulp.lpSum(obj)
    status = prob.solve(pulp.HiGHS(msg=False, timeLimit=time_limit, gapRel=0.002))
    if pulp.LpStatus[status] not in ("Optimal", "Not Solved") or prob.objective.value() is None:
        raise ValueError(f"Optimiser failed: {pulp.LpStatus[status]}")

    v = lambda x: round(x.value() or 0) if not isinstance(x, (int, float)) else x
    plan = []
    for k, g in enumerate(gws):
        chip = chips.get(g)
        play_ids = [i for i in P if v(fh[g][i] if chip == "freehit" else sq[i][g])]
        xi_ids = [i for i in play_ids if v(xi[i][g])]
        cap_id = next(i for i in P if v(cap[i][g]))
        vice_id = max((i for i in xi_ids if i != cap_id), key=lambda i: xp[i, g])
        bench = [i for i in play_ids if i not in xi_ids]
        bench = sorted(bench, key=lambda i: (info[i]["pos_id"] != 1, -xp[i, g]))
        cap_mult = 3 if chip == "3xc" else 2
        pts = sum(xp[i, g] for i in xi_ids) + (cap_mult - 1) * xp[cap_id, g]
        n_hits = v(hits[g])
        plan.append({
            "gw": g,
            "chip": chip,
            "free_transfers": v(ft[g]),
            "transfers_in": [_pl(info[i], xp, g) for i in P if v(tin[i][g])],
            "transfers_out": [dict(_pl(info[i], xp, g), sell=sell[i]) for i in P if v(tout[i][g])],
            "hits": n_hits,
            "bank": round(money[g].value(), 1),
            "xi": sorted([_pl(info[i], xp, g) for i in xi_ids], key=lambda r: r["pos_id"]),
            "bench": [_pl(info[i], xp, g) for i in bench],
            "captain": cap_id,
            "vice": vice_id,
            "xpts": round(pts, 2),
            "net_xpts": round(pts - HIT * n_hits, 2),
        })
    return {
        "status": pulp.LpStatus[status],
        "gws": gws,
        "plan": plan,
        "total_xpts": round(sum(r["net_xpts"] for r in plan), 2),
    }


def _pl(p, xp, g):
    return {"id": p["id"], "name": p["name"], "team": p["team_short"], "pos": p["pos"], "pos_id": p["pos_id"],
            "price": p["price"], "xpts": round(xp[p["id"], g], 2)}
