#!/usr/bin/env python3
"""roblox-up-and-coming-forecast-01 entry: a Monte Carlo forecast of Roblox's
Up-and-Coming chart.

Model
-----
Every game carries a latent strength z (z = -ln(rank) on the observed chart).
A game's strength is a persistent level m_g plus two mean-reverting (OU)
components, one slow and one fast, fitted to how the chart's own ranks
decorrelate over time in the history the program is given. Starting from each
game's state at the end of the history, the program simulates the whole field
across the scored window (a fixed-seed PRNG and a fixed number of simulations,
so the two grader runs agree byte for byte), sorts each simulated chart, and
tallies how often each game holds each of the scored places. Those
frequencies, shrunk by a reserve for games the history never saw, are the
forecast's probabilities; the top 10 is ordered by mean simulated rank.

Games whose details name a `created` date leave the chart 28 days after it
(the chart only lists experiences published within the last 28 days) and are
removed from the simulation at that hour. Synthetic field games, drawn from
the chart's own strength distribution, stand in for the part of the chart the
history's universe does not cover, so simulated ranks stay honest.

Standard library only, no network, deterministic.
"""

import json
import math
import os
import random
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Tuned constants (fitted on the practice history by backtest; see NOTES.md)
# ---------------------------------------------------------------------------
SEED = 20261113
N_SIMS = 900                # Monte Carlo paths
TALLY_EVERY_H = 3           # tally the simulated chart every 3rd window slot
FIELD_GAMES = 48            # synthetic competitors for the unseen chart tail

# Strength dynamics: z = m + u_slow + u_fast, each u an OU process.
PERSIST_SHARE = 0.16        # variance share of the persistent level m
SLOW_SHARE = 0.52           # variance share of the slow component
TAU_SLOW_H = 160.0          # slow component mean-reversion time, hours
TAU_FAST_H = 24.0           # fast component mean-reversion time, hours
Z_VAR = 1.10                # total variance of z across the chart population

SHRINK_READS = 6.0          # prior weight (in reads) for a game's level m_g
ABSENT_DROP = 0.55          # z penalty for missing the history's last reads
UNSEEN_RESERVE = 0.055      # per-place mass left unnamed, for brand-new games
MIN_P = 0.0008              # smallest named probability worth a row
MAX_ROWS_PER_PLACE = 100

CHART_DAYS = 28.0           # chart lists experiences published < 28 days ago


def parse_time(s):
    """ISO 8601 -> epoch seconds (float)."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def load_input(input_dir):
    with open(os.path.join(input_dir, "task.json"), "r", encoding="utf-8") as f:
        task = json.load(f)
    reads = []
    path = os.path.join(input_dir, "history.ndjson")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows = []
            for r in d.get("up_and_coming") or []:
                rows.append((int(r["rank"]), int(r["universe_id"])))
            rows.sort()
            reads.append({
                "t": parse_time(d["at"]),
                "ids": [g for _, g in rows],
                "rank": {g: rk for rk, g in rows},
                "details": d.get("details") or {},
            })
    reads.sort(key=lambda rd: rd["t"])
    return task, reads


# ---------------------------------------------------------------------------
# Per-game state at the end of the history
# ---------------------------------------------------------------------------
def game_states(reads):
    """Per-game dict: persistent level m, current strength z, and the epoch
    second it ages off the chart (or None). Also the typical chart size and
    the population mean of z."""
    chart_size = max(len(rd["ids"]) for rd in reads[-3:]) if reads else 97
    obs = {}   # game -> list of (t, z), in read order
    created = {}
    for rd in reads:
        for g, rk in rd["rank"].items():
            obs.setdefault(g, []).append((rd["t"], -math.log(rk)))
        det = rd["details"]
        if isinstance(det, dict):
            for gid, dd in det.items():
                if isinstance(dd, dict) and dd.get("created"):
                    try:
                        created[int(gid)] = parse_time(dd["created"])
                    except (ValueError, TypeError):
                        pass
    pop_z = []
    for rd in reads[-6:]:
        pop_z.extend(-math.log(rk) for rk in rd["rank"].values())
    pop_mean = sum(pop_z) / len(pop_z) if pop_z else -3.4

    last = reads[-1]
    t_end = last["t"]
    games = {}
    for g, series in obs.items():
        # persistent level: recency-weighted mean of observed z, shrunk
        # toward the population mean by the number of appearances
        wsum = 0.0
        zsum = 0.0
        k = len(series)
        for i, (_t, z) in enumerate(series):
            w = 0.85 ** (k - 1 - i)
            wsum += w
            zsum += w * z
        m = (zsum + SHRINK_READS * pop_mean) / (wsum + SHRINK_READS)
        # current z: last seen strength decayed toward the level over the
        # gap, minus a penalty for each read missed since
        t_seen, z_seen = series[-1]
        missed = sum(1 for rd in reads if rd["t"] > t_seen)
        gap_h = (t_end - t_seen) / 3600.0
        decay = math.exp(-gap_h / TAU_SLOW_H)
        z_now = m + (z_seen - m) * decay - ABSENT_DROP * (1.0 - 0.9 ** missed)
        if g in last["rank"]:
            z_now = -math.log(last["rank"][g])
        age_off = None
        if g in created:
            age_off = created[g] + CHART_DAYS * 86400.0
        games[g] = {"m": m, "z": z_now, "age_off": age_off, "seen": k}
    return games, chart_size, pop_mean


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def _ou(rho, sd, dh):
    """OU transition over dh hours: (multiplier, innovation sd)."""
    r = rho ** dh
    return r, sd * math.sqrt(max(0.0, 1.0 - r * r))


def simulate(games, pop_mean, t_start, tally_times, weights, rng):
    """Monte Carlo over the window. Returns (tally, rank_sum, total_w, ids):
    tally[g][k] = weighted count of g holding place k+1; rank_sum[g] the
    weighted sum of g's simulated ranks; total_w the total tally weight."""
    ids = sorted(games)
    n = len(ids)
    sd = math.sqrt(Z_VAR)
    sd_slow = sd * math.sqrt(SLOW_SHARE)
    sd_fast = sd * math.sqrt(1.0 - PERSIST_SHARE - SLOW_SHARE)
    m_arr = [games[g]["m"] for g in ids]
    age_off = [games[g]["age_off"] for g in ids]
    # field games: persistent levels spread like the chart's lower half
    field_m = [pop_mean - 0.35 * sd + 0.7 * sd * (i / max(FIELD_GAMES - 1, 1))
               for i in range(FIELD_GAMES)]
    all_m = m_arr + field_m
    n_all = n + FIELD_GAMES

    rho_slow = math.exp(-1.0 / TAU_SLOW_H)
    rho_fast = math.exp(-1.0 / TAU_FAST_H)
    fast_share = 1.0 - PERSIST_SHARE - SLOW_SHARE
    dev_share_slow = SLOW_SHARE / (SLOW_SHARE + fast_share)

    # one OU transition per distinct step length between tally times
    steps = []
    prev = t_start
    for tt in tally_times:
        steps.append((tt - prev) / 3600.0)
        prev = tt
    trans = {}
    for dh in steps:
        if dh not in trans:
            trans[dh] = (_ou(rho_slow, sd_slow, dh), _ou(rho_fast, sd_fast, dh))

    places = 10
    tally = {g: [0.0] * places for g in ids}
    rank_sum = {g: 0.0 for g in ids}
    total_w = 0.0
    gauss = rng.gauss

    for _ in range(N_SIMS):
        slow = []
        fast = []
        for i in range(n):
            dev = games[ids[i]]["z"] - m_arr[i]
            s0 = dev * dev_share_slow
            slow.append(s0)
            fast.append(dev - s0)
        for _i in range(FIELD_GAMES):
            slow.append(gauss(0.0, sd_slow))
            fast.append(gauss(0.0, sd_fast))
        for si, tt in enumerate(tally_times):
            dh = steps[si]
            if dh > 0.0:
                (rs, ss), (rf, sf) = trans[dh]
                for i in range(n_all):
                    slow[i] = slow[i] * rs + gauss(0.0, ss)
                    fast[i] = fast[i] * rf + gauss(0.0, sf)
            w = weights[si]
            chart = []
            for i in range(n_all):
                ao = age_off[i] if i < n else None
                if ao is not None and tt >= ao:
                    continue
                chart.append((-(all_m[i] + slow[i] + fast[i]), i))
            chart.sort()
            total_w += w
            for pos, (_nz, gi) in enumerate(chart):
                if gi < n:
                    g = ids[gi]
                    rank_sum[g] += w * (pos + 1)
                    if pos < places:
                        tally[g][pos] += w
    return tally, rank_sum, total_w, ids


# ---------------------------------------------------------------------------
# Forecast assembly
# ---------------------------------------------------------------------------
def build_forecast(task, reads):
    places = int(task.get("places", 10))
    window = task["window"]
    t_from = parse_time(window["from"])
    t_until = parse_time(window["until"])
    slot_minutes = int(window.get("slot_minutes", 60))
    n_slots = int(window.get(
        "slots", int((t_until - t_from) // (slot_minutes * 60))))
    slot_times = [t_from + i * slot_minutes * 60 for i in range(n_slots)]

    games, _chart_size, pop_mean = game_states(reads)
    t_start = reads[-1]["t"]

    # tally at every TALLY_EVERY_H-th slot; each tally stands for the slots
    # up to the next tally (the last one for the slots that remain)
    tally_idx = [i for i in range(n_slots) if i % TALLY_EVERY_H == 0]
    tally_times = [slot_times[i] for i in tally_idx]
    weights = []
    for j, i in enumerate(tally_idx):
        nxt = tally_idx[j + 1] if j + 1 < len(tally_idx) else n_slots
        weights.append(float(nxt - i))

    rng = random.Random(SEED)
    tally, rank_sum, total_w, ids = simulate(
        games, pop_mean, t_start, tally_times, weights, rng)

    rows = []
    for k in range(places):
        col = [(g, tally[g][k] / total_w) for g in ids if tally[g][k] > 0]
        col.sort(key=lambda gp: (-gp[1], gp[0]))
        named = 0.0
        for g, p in col:
            p *= (1.0 - UNSEEN_RESERVE)
            if p < MIN_P or named + p > 1.0 - UNSEEN_RESERVE:
                continue
            rows.append({"universe_id": g, "place": k + 1,
                         "p": round(p, 6)})
            named += p
            if sum(1 for r in rows if r["place"] == k + 1) >= MAX_ROWS_PER_PLACE:
                break
    rows.sort(key=lambda r: (r["place"], -r["p"], r["universe_id"]))

    # top 10: order by the probability of finishing in the top 10 at a scored
    # read (the tie-break rewards naming the right games at near places),
    # breaking ties by mean simulated rank
    mean_rank = {g: rank_sum[g] / total_w for g in ids}
    p_top = {g: sum(tally[g]) / total_w for g in ids}
    top10 = sorted(ids, key=lambda g: (-p_top[g], mean_rank[g], g))[:places]
    return {"probabilities": rows, "top10": top10}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    task, reads = load_input(input_dir)
    forecast = build_forecast(task, reads)
    os.makedirs(output_dir, exist_ok=True)
    out_name = task.get("output", "forecast.json")
    with open(os.path.join(output_dir, out_name), "w", encoding="utf-8") as f:
        f.write(json.dumps(forecast, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
