"""Expected-points projection model.

Everything is derived from per-gameweek player histories so the model can be
re-run "as of" any past gameweek for backtesting.

Pipeline:
  1. Team ratings: multiplicative attack/defence model fitted to per-match
     team xG (blended with real goals), recency-weighted and shrunk to average.
  2. Player rates: recency-weighted per-90 xG, xA, defensive contributions,
     saves, bonus and cards, shrunk towards a price-based positional prior.
  3. Minutes: recency-weighted P(plays), P(60+ mins), minutes when playing,
     multiplied by an availability curve parsed from the injury news.
  4. Per fixture, convert everything into FPL points using the official
     scoring rules (supports blank and double gameweeks).
"""
import math
import re
from datetime import datetime, timezone

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
DC_THRESHOLD = {1: None, 2: 10, 3: 12, 4: 12}
DC_PTS = 2

HOME_ADV = 1.10          # multiplier on home team's expected goals (away gets 1/HOME_ADV)
RATE_DECAY = 0.90        # per-gameweek recency weight for per-90 rates
MINS_DECAY = 0.70        # minutes are much more recency-sensitive
TEAM_DECAY = 0.90
TEAM_SHRINK = 3.0        # pseudo-matches of "average" in team ratings
XG_GOAL_BLEND = 0.75     # weight on xG vs real goals in team ratings
SHRINK_MINUTES = {"xg": 450, "xa": 450, "dc": 270, "saves": 270, "bonus": 450, "yc": 900}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# Probability helpers
# --------------------------------------------------------------------------

def _poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_ge(k, lam):
    """P(X >= k) for X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    return max(0.0, 1.0 - sum(_poisson_pmf(i, lam) for i in range(k)))


def expected_floor_div(lam, d, kmax=20):
    """E[floor(X / d)] for X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    return sum((k // d) * _poisson_pmf(k, lam) for k in range(d, kmax))


# --------------------------------------------------------------------------
# Team ratings
# --------------------------------------------------------------------------

def team_ratings(fixtures, histories, as_of_gw):
    """Fit attack (A) and defence (D) multipliers: E[goals t vs o] = mu*A_t*D_o*H."""
    fx_by_id = {f["id"]: f for f in fixtures}
    xg = {}  # (fixture_id, team_id) -> team xG in that match
    for rows in histories.values():
        for r in rows:
            f = fx_by_id.get(r["fixture"])
            if not f or r["round"] > as_of_gw or not f["finished"]:
                continue
            team = f["team_h"] if r["was_home"] else f["team_a"]
            xg[(f["id"], team)] = xg.get((f["id"], team), 0.0) + _f(r["expected_goals"])

    matches = []  # (team, opp, home, value, weight)
    for f in fixtures:
        if not f["finished"] or f["event"] is None or f["event"] > as_of_gw:
            continue
        w = TEAM_DECAY ** (as_of_gw - f["event"])
        for team, opp, home, goals in ((f["team_h"], f["team_a"], True, f["team_h_score"]),
                                       (f["team_a"], f["team_h"], False, f["team_a_score"])):
            val = XG_GOAL_BLEND * xg.get((f["id"], team), goals) + (1 - XG_GOAL_BLEND) * goals
            matches.append((team, opp, home, val, w))

    teams = sorted({f["team_h"] for f in fixtures} | {f["team_a"] for f in fixtures})
    A = {t: 1.0 for t in teams}
    D = {t: 1.0 for t in teams}
    if not matches:
        return 1.35, A, D
    mu = sum(m[3] * m[4] for m in matches) / sum(m[4] for m in matches)
    H = lambda home: HOME_ADV if home else 1 / HOME_ADV

    for _ in range(30):
        num = {t: TEAM_SHRINK * mu for t in teams}
        den = {t: TEAM_SHRINK * mu for t in teams}
        for team, opp, home, val, w in matches:
            num[team] += w * val
            den[team] += w * mu * D[opp] * H(home)
        A = {t: num[t] / den[t] for t in teams}
        num = {t: TEAM_SHRINK * mu for t in teams}
        den = {t: TEAM_SHRINK * mu for t in teams}
        for team, opp, home, val, w in matches:
            num[opp] += w * val
            den[opp] += w * mu * A[team] * H(home)
        D = {t: num[t] / den[t] for t in teams}
    return mu, A, D


# --------------------------------------------------------------------------
# Availability from news
# --------------------------------------------------------------------------

_NEWS_DATE = re.compile(r"(?:expected back|until)\s+(\d{1,2})\s+([A-Za-z]{3})", re.I)


def availability_fn(p, now):
    """Return f(kickoff_datetime) -> probability player is available."""
    status = p["status"]
    chance = p["chance_of_playing_next_round"]
    if status == "a" or (chance is None and status not in ("u", "i", "s", "n")):
        return lambda ko: 1.0
    if status in ("u", "n"):  # left the club / not eligible
        return lambda ko: 0.0
    c = (chance if chance is not None else 0) / 100.0
    m = _NEWS_DATE.search(p.get("news") or "")
    if m:
        try:
            ret = datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year}", "%d %b %Y").replace(tzinfo=timezone.utc)
            if (now - ret).days > 60:  # date is next year
                ret = ret.replace(year=now.year + 1)
            return lambda ko: c if ko < ret else max(c, 0.9)
        except ValueError:
            pass
    if status == "d":  # doubtful: flag applies to the coming week only
        return lambda ko: c if (ko - now).days < 7 else max(c, 0.9)
    # injured/suspended without a date: assume ~3 weeks out
    return lambda ko: c if (ko - now).days < 21 else max(c, 0.75)


# --------------------------------------------------------------------------
# Player rates
# --------------------------------------------------------------------------

def _linear_prior(points):
    """Weighted least squares y = a + b*x. points: [(x, y, w)]."""
    sw = sum(w for _, _, w in points)
    if sw == 0:
        return lambda x: 0.0
    mx = sum(x * w for x, _, w in points) / sw
    my = sum(y * w for _, y, w in points) / sw
    vx = sum(w * (x - mx) ** 2 for x, _, w in points)
    b = sum(w * (x - mx) * (y - my) for x, y, w in points) / vx if vx else 0.0
    a = my - b * mx
    return lambda x: max(0.0, a + b * x)


STATS = {
    "xg": lambda r: _f(r["expected_goals"]),
    "xa": lambda r: _f(r["expected_assists"]),
    "dc": lambda r: _f(r.get("defensive_contribution", 0)),
    "saves": lambda r: _f(r["saves"]),
    "bonus": lambda r: _f(r["bonus"]),
    "yc": lambda r: _f(r["yellow_cards"]) + 3 * _f(r["red_cards"]),
}


def player_rates(bs, histories, as_of_gw):
    raw = {}
    for p in bs["elements"]:
        rows = [r for r in histories.get(p["id"], []) if r["round"] <= as_of_gw]
        wm = 0.0
        ws = {k: 0.0 for k in STATS}
        for r in rows:
            w = RATE_DECAY ** (as_of_gw - r["round"])
            wm += w * r["minutes"]
            for k, fn in STATS.items():
                ws[k] += w * fn(r)
        raw[p["id"]] = (wm, ws, sum(r["minutes"] for r in rows))

    # price-based positional priors from players with a decent sample
    priors = {}
    for pos in POS:
        priors[pos] = {}
        for k in STATS:
            pts = []
            for p in bs["elements"]:
                wm, ws, mins = raw[p["id"]]
                if p["element_type"] == pos and mins >= 300 and wm > 0:
                    pts.append((p["now_cost"] / 10, ws[k] / wm * 90, mins))
            priors[pos][k] = _linear_prior(pts)

    rates = {}
    for p in bs["elements"]:
        wm, ws, _ = raw[p["id"]]
        price = p["now_cost"] / 10
        rates[p["id"]] = {
            k: (ws[k] + priors[p["element_type"]][k](price) * SHRINK_MINUTES[k] / 90)
            / (wm + SHRINK_MINUTES[k]) * 90
            for k in STATS
        }
    return rates


def minutes_profile(p, histories, as_of_gw):
    """Recency-weighted (p_play, p60, minutes when playing) for one player."""
    rows = [r for r in histories.get(p["id"], []) if r["round"] <= as_of_gw]
    rows = sorted(rows, key=lambda r: r["round"])[-8:]
    if not rows:
        return 0.0, 0.0, 0.0
    sw = s_play = s_60 = s_min = 0.0
    for r in rows:
        w = MINS_DECAY ** (as_of_gw - r["round"])
        sw += w
        s_play += w * (r["minutes"] > 0)
        s_60 += w * (r["minutes"] >= 60)
        s_min += w * r["minutes"]
    p_play = s_play / sw
    p60 = s_60 / sw
    mins_if_play = s_min / s_play if s_play else 0.0
    return p_play, p60, mins_if_play


def profile_from_minutes(m):
    """Map a user-supplied expected-minutes figure onto (p_play, p60, mins_if_play)."""
    m = max(0.0, min(90.0, m))
    p_play = min(1.0, m / 60)
    p60 = max(0.0, min(1.0, (m - 15) / 72))
    return p_play, p60, (m / p_play if p_play else 0.0)


# --------------------------------------------------------------------------
# Fixture-level expected points
# --------------------------------------------------------------------------

def fixture_points(pos, rates, prof, avail, team_xg_mult, opp_attack_mult, team_xga):
    """Expected points for one player in one fixture, with breakdown."""
    p_play, p60, mins_if = prof
    p_play *= avail
    p60 *= avail
    m = p_play * mins_if
    m_frac = mins_if / 90

    goals = rates["xg"] * m / 90 * team_xg_mult
    assists = rates["xa"] * m / 90 * team_xg_mult
    bd = {
        "app": 2 * p60 + 1 * (p_play - p60),
        "goals": GOAL_PTS[pos] * goals,
        "assists": 3 * assists,
        "cs": CS_PTS[pos] * p60 * math.exp(-team_xga),
        "bonus": rates["bonus"] * m / 90 * (0.7 + 0.3 * team_xg_mult),
        "cards": -rates["yc"] * m / 90,
    }
    if pos in (1, 2):
        bd["conceded"] = -p_play * expected_floor_div(team_xga * m_frac, 2)
    if pos == 1:
        bd["saves"] = p_play * expected_floor_div(rates["saves"] * m_frac * opp_attack_mult, 3)
    thr = DC_THRESHOLD[pos]
    if thr:
        bd["dc"] = DC_PTS * p_play * poisson_ge(thr, rates["dc"] * m_frac)
    return sum(bd.values()), bd, m


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def project(bs, fixtures, histories, as_of_gw=None, horizon=6, minutes_override=None, now=None):
    """Project expected points for every player for the next `horizon` gameweeks."""
    minutes_override = minutes_override or {}
    events = bs["events"]
    if as_of_gw is None:
        as_of_gw = max([e["id"] for e in events if e["finished"]] or [0])
    gws = [g for g in range(as_of_gw + 1, as_of_gw + 1 + horizon) if g <= len(events)]
    if now is None:
        now = datetime.now(timezone.utc) if as_of_gw == max([e["id"] for e in events if e["finished"]] or [0]) \
            else _parse(next(e for e in events if e["id"] == as_of_gw + 1)["deadline_time"])

    mu, A, D = team_ratings(fixtures, histories, as_of_gw)
    rates = player_rates(bs, histories, as_of_gw)
    teams = {t["id"]: t for t in bs["teams"]}

    # fixtures per team per gameweek
    team_fx = {}
    for f in fixtures:
        if f["event"] in gws:
            ko = _parse(f["kickoff_time"]) if f["kickoff_time"] else now
            team_fx.setdefault((f["team_h"], f["event"]), []).append((f["team_a"], True, ko))
            team_fx.setdefault((f["team_a"], f["event"]), []).append((f["team_h"], False, ko))

    players = []
    for p in bs["elements"]:
        pos = p["element_type"]
        override = minutes_override.get(p["id"], minutes_override.get(str(p["id"])))
        prof = profile_from_minutes(float(override)) if override is not None \
            else minutes_profile(p, histories, as_of_gw)
        avail = availability_fn(p, now) if override is None else (lambda ko: 1.0)
        t = p["team"]
        xpts, xmins, breakdown = [], [], {}
        for g in gws:
            tot = mins = 0.0
            bd_g = {}
            for opp, home, ko in team_fx.get((t, g), []):
                h = HOME_ADV if home else 1 / HOME_ADV
                team_xg_mult = D[opp] * h                       # vs the player's usual output
                team_xga = mu * A[opp] * D[t] / h                # goals expected against
                opp_attack_mult = A[opp] / h
                pts, bd, m = fixture_points(pos, rates[p["id"]], prof, avail(ko),
                                            team_xg_mult, opp_attack_mult, team_xga)
                tot += pts
                mins += m
                for k, v in bd.items():
                    bd_g[k] = bd_g.get(k, 0) + v
            xpts.append(round(tot, 2))
            xmins.append(round(mins))
            if g == gws[0]:
                breakdown = {k: round(v, 2) for k, v in bd_g.items()}
        players.append({
            "id": p["id"],
            "name": p["web_name"],
            "full_name": f"{p['first_name']} {p['second_name']}",
            "team": t,
            "team_short": teams[t]["short_name"],
            "pos": POS[pos],
            "pos_id": pos,
            "price": p["now_cost"] / 10,
            "selected_by": _f(p["selected_by_percent"]),
            "status": p["status"],
            "news": p["news"],
            "total_points": p["total_points"],
            "form": _f(p["form"]),
            "xpts": xpts,
            "xmins": xmins,
            "total_xpts": round(sum(xpts), 2),
            "breakdown": breakdown,
            "rates": {k: round(v, 3) for k, v in rates[p["id"]].items()},
            "overridden": override is not None,
        })

    team_out = []
    for tid, tm in teams.items():
        row = {"id": tid, "name": tm["name"], "short": tm["short_name"],
               "attack": round(A.get(tid, 1), 3), "defence": round(D.get(tid, 1), 3), "fixtures": []}
        for g in gws:
            row["fixtures"].append([
                {"opp": teams[o]["short_name"], "home": h,
                 "xg": round(mu * A[tid] * D[o] * (HOME_ADV if h else 1 / HOME_ADV), 2),
                 "xga": round(mu * A[o] * D[tid] / (HOME_ADV if h else 1 / HOME_ADV), 2)}
                for o, h, _ in team_fx.get((tid, g), [])
            ])
        team_out.append(row)

    return {"as_of_gw": as_of_gw, "gws": gws, "mu": round(mu, 3), "players": players, "teams": team_out}


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
