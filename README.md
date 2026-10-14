# FPL Simulator

Predicts Fantasy Premier League points and plans your transfers several gameweeks ahead.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py      # http://localhost:8000
```

The first start downloads every player's match history from the public FPL API (~15s). It is
cached in `cache/` and refreshed automatically after each gameweek, or with the **Refresh data** button.

## What's in it

| Tab | What it does |
|---|---|
| **Planner** | Enter your FPL team ID (or start from £100m). The optimiser picks transfers, starting XI and captain for the next 1–8 GWs, accounting for free-transfer banking (max 5), −4 hits, bank and real selling prices, 2/5/5/3 and 3-per-club rules, valid formations, and any chips you plan (WC, FH, BB, TC). Lock/ban players, set a transfer threshold and a future discount. It compares the plan with making no transfers. |
| **Projections** | Expected points per player per GW, sortable and filterable. Hover a cell for the breakdown. Click **xMins** to override expected minutes (fed into the planner too). |
| **Fixtures** | A ticker of each team's expected goals or clean-sheet probability, plus the fitted team attack/defence ratings. |

## How the projections work (`fpl/model.py`)

1. **Team ratings**: a multiplicative attack × defence × home-advantage model fitted to per-match team xG
   (blended 75/25 with real goals), recency-weighted and shrunk towards average. Opponent strength is
   accounted for.
2. **Player rates**: recency-weighted per-90 xG, xA, defensive contributions, saves, bonus and cards, shrunk
   towards a price-based prior for the position (so a 3-game sample doesn't dominate).
3. **Minutes**: recency-weighted P(plays), P(60+), minutes when playing, multiplied by availability parsed
   from FPL injury news ("Expected back 25 Oct", chance of playing %).
4. **Points**: official 2026/27 scoring per fixture: appearance, goals by position, assists, clean sheets
   (Poisson), goals-conceded penalty, saves per 3, defensive contribution threshold (10 CBIT for DEF, 12 CBIRT
   for MID/FWD), bonus and cards. Double and blank gameweeks are handled.

`backtest.py` re-runs the model as of each past gameweek and scores it against what actually happened:

```bash
.venv/bin/python backtest.py
```

## Layout

```
server.py          stdlib HTTP server + JSON API
fpl/data.py        FPL API fetching and caching
fpl/model.py       projection model
fpl/optimizer.py   multi-GW MIP (PuLP + HiGHS)
fpl/team.py        loads a manager's squad, bank, selling prices, free transfers
static/            single-page UI (no build step)
backtest.py        model evaluation
```

## Known limitations / ideas

- Price changes during the horizon aren't modelled (buy/sell at current prices).
- Free transfers are inferred from history; transfers already made for the upcoming GW aren't visible
  publicly, so set **Free transfers** and **Bank** by hand if you've already moved.
- Early-season projections lean on the price prior. Using last season's per-90 data as a prior would help.
- No set-piece/penalty-taker adjustment beyond what shows up in xG.
