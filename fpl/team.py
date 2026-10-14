"""Load a manager's current squad, bank, selling prices and free transfers."""
from . import data

MAX_FT = 5


def load_team(entry_id, bs):
    entry = data.entry(entry_id)
    hist = data.entry_history(entry_id)
    transfers = data.entry_transfers(entry_id)
    finished = [e["id"] for e in bs["events"] if e["finished"]]
    current = entry.get("current_event") or (max(finished) if finished else None)
    if not current:
        raise ValueError("This team has not played a gameweek yet.")

    picks = data.entry_picks(entry_id, current)
    # A free hit squad reverts afterwards: the real squad is the one before it.
    squad_gw = current
    if picks.get("active_chip") == "freehit" and current > 1:
        squad_gw = current - 1
        squad_picks = data.entry_picks(entry_id, squad_gw)
    else:
        squad_picks = picks
    bank = picks["entry_history"]["bank"] / 10

    # purchase price = cost of the most recent transfer in, else GW1 price
    elements = {p["id"]: p for p in bs["elements"]}
    bought = {}
    for t in sorted(transfers, key=lambda t: t["event"]):
        bought[t["element_in"]] = t["element_in_cost"]
    squad = []
    for pk in squad_picks["picks"]:
        el = elements[pk["element"]]
        now = el["now_cost"]
        buy = bought.get(el["id"], now - el["cost_change_start"])
        sell = now if now <= buy else buy + (now - buy) // 2
        squad.append({"id": el["id"], "buy": buy / 10, "sell": sell / 10, "now": now / 10,
                      "position": pk["position"], "is_captain": pk["is_captain"]})

    # free transfers: 1 for GW2, +1 per GW (max 5); WC/FH weeks don't consume any
    chips_by_gw = {c["event"]: c["name"] for c in hist.get("chips", [])}
    ft = 1
    for row in hist["current"]:
        gw = row["event"]
        if gw < 2:
            continue
        if chips_by_gw.get(gw) not in ("wildcard", "freehit"):
            ft = max(0, ft - row["event_transfers"])
        ft = min(MAX_FT, ft + 1)

    used_chips = [{"name": c["name"], "event": c["event"]} for c in hist.get("chips", [])]
    return {
        "entry_id": entry_id,
        "name": entry["name"],
        "manager": f"{entry['player_first_name']} {entry['player_last_name']}",
        "overall_rank": entry.get("summary_overall_rank"),
        "total_points": entry.get("summary_overall_points"),
        "current_gw": current,
        "bank": round(bank, 1),
        "free_transfers": ft,
        "squad": squad,
        "chips_used": used_chips,
        "squad_value": round(sum(s["sell"] for s in squad) + bank, 1),
    }
