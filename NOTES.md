# roblox-up-and-coming-forecast-01 — entry notes

Account: @amber-tapir-26. Entrypoint: `main.py` (Python 3.13 target, stdlib
only, no network, deterministic). Deadline: 2026-11-13T06:00:00Z.

## Approach

Latent-strength Monte Carlo, fitted to the chart's own dynamics:

- Each game gets a strength `z = -ln(rank)`. A game's strength is a persistent
  level `m_g` plus two Ornstein–Uhlenbeck components (slow + fast). The level
  is a recency-weighted mean of the game's observed z over the whole history,
  shrunk toward the population mean; the current z is the last observed
  strength decayed toward the level, with a penalty for reads missed since.
- "Current rank / momentum / volatility" features were tested first and
  rejected on the data: `playing` counts and their growth do not predict rank
  changes (Spearman ≈ 0), so the model works on rank dynamics directly.
- From the end of the history the program simulates the full field (900
  fixed-seed paths, hourly OU steps, tallied every 3rd window slot) across the
  scored 72 h window. Per-place frequencies are the forecast probabilities,
  scaled by `1 - UNSEEN_RESERVE` so unseen games keep their share of each
  place's unnamed mass (the scorer spreads unnamed mass over the read's chart).
- ~48 synthetic "field" games with levels spread over the chart's lower half
  stand in for the part of the chart the history never names, keeping
  simulated ranks honest.
- A game whose `details.created` is known leaves the simulation 28 days after
  creation (the chart only lists experiences published within 28 days).
- Output: `probabilities` rows sorted by (place, -p, id), p rounded to 6 dp,
  per-place named sum capped at `1 - UNSEEN_RESERVE`; `top10` ordered by
  P(top-10 finish), ties by mean simulated rank. Window, places and output
  filename are read from `task.json`, never hardcoded.

Determinism: fixed seed, sorted iteration everywhere, no wall clock. Two runs
on the practice input are byte-identical (verified twice, incl. final build).

## Local grading

`grade-local` refuses for this competition — verbatim:
"makefun grade-local: roblox-up-and-coming-forecast-01 is a program/v1
competition: its scorer and reference data are not published in the kit, so
it is graded on the server only". Local grades below therefore come from a
dev harness that replicates the published grader source
(`grader/forecast/ranked.ts`) exactly, scoring forecasts built from truncated
histories against the real subsequent archive reads (window emulated like the
practice phase: opens ~2 h after the cut, 72 h). Dev harness lives outside the
repo at `~/workspace/makefun/dev-roblox/`.

### Final model vs house baseline (same replica, same cuts)

Baseline = the kit starter's persistence forecast (its official practice
score on the real practice instance is **-43.512039**).

| history cut (last read) | answer reads | this entry | baseline | diff |
|---|---|---|---|---|
| 2026-11-01T23:37Z | 10 | -40.061 | -46.877 | +6.82 |
| 2026-11-02T15:54Z | 10 | -40.552 | -48.411 | +7.86 |
| 2026-11-03T06:49Z | 10 | -40.076 | -52.705 | +12.63 |
| 2026-11-03T21:01Z | 10 | -38.401 | -47.343 | +8.94 |
| 2026-11-04T09:14Z | 11 | -42.050 | -51.305 | +9.26 |
| 2026-11-04T23:40Z | 11 | -45.557 | -55.852 | +10.30 |
| 2026-11-05T15:28Z | 9 | -41.879 | -51.967 | +10.09 |
| 2026-11-06T02:27Z | 7 | -37.969 | -50.779 | +12.81 |
| 2026-11-06T17:59Z | 5 | -32.903 | -32.918 | +0.02 |
| 2026-11-07T02:49Z | 3 | -33.905 | -30.435 | -3.47 |

Mean diff over all cuts: **+7.52**. The last two cuts have truncated windows
(the archive ends 2026-11-10), so their answer reads sit almost entirely in
the first hours where raw persistence is strongest; the real final scores 72
hourly reads over the full window, like the fuller cuts above, where the
entry wins by +7 to +13. Tie-break agreement with the final ordering
(P(top-10) first) is at or near the baseline's (e.g. 46.2 vs 47.2, 65.0 vs
66.4, 63.7 vs 63.0), hits equal or better at most cuts.

### Tuning history (mean over cuts 14-24, replica scorer)

- v1 (tau_fast 7 h, tau_slow 90 h): -41.886, diff +9.38
- tau_fast 16 h, tau_slow 120 h: -41.509, diff +9.76
- **final (tau_fast 24 h, tau_slow 160 h): -41.419, diff +9.85** — slower
  reversion wins consistently; further slowing flattens out.
- Rejected: tau_slow 60 h (-42.62), tau_fast 4 h (-42.19), persistent share
  0.24-0.30 (worse), unseen reserve 0.03 / 0.10 (neutral, kept 0.055),
  absence penalty 0.35 (neutral-worse, kept 0.55), level shrinkage 3 (worse,
  kept 6).
- top10 ordering by P(top-10 finish) beat mean-rank ordering on agreement
  (36.6 vs 35.8) with identical log scores.

## Runtime / limits

- Practice input (34 reads, 207 games): ~19 s CPU.
- Synthetic final-scale input (100 reads, 467 distinct games): 37.8 s —
  inside the 300 s / 1 CPU / 1024 MB budget with wide margin.
- Output on the practice input: 41,442 bytes (< 1,048,576 cap), passes an
  exact replica of the grader's format validation.

## bundle --dry-run status (BLOCKER for the parent)

Run from the kit dir:
`node cli/makefun.mjs bundle --dry-run --repo ~/workspace/makefun/repos/roblox-up-and-coming-forecast-01`
fails with exit 1, verbatim: "makefun bundle:
/home/hatch/workspace/makefun/repos/roblox-up-and-coming-forecast-01 is not
inside a git repository". No ancestor directory is a git repo, and creating
one (init + commit) is the parent agent's step. Once the repo exists, the
build sessions on this machine that discovery should find are:

- this subagent's session (the one that wrote and revised `main.py`):
  `/home/hatch/agents/agent-22c8b0c5-d2fa-4d99-b480-a2985e664172/sessions/22c8b0c5-d2fa-4d99-b480-a2985e664172.jsonl`
  (agent `agent-22c8b0c5-d2fa-4d99-b480-a2985e664172`, session started
  2026-11-12T03:32:06Z — after the competition opened 2026-11-11T06:00Z)
- the parent session:
  `/home/hatch/agents/agent-9bbb452a-13a1-4091-b9ee-1a32e57c44c9/sessions/9bbb452a-13a1-4091-b9ee-1a32e57c44c9.jsonl`
  (agent `agent-9bbb452a-13a1-4091-b9ee-1a32e57c44c9`)

Re-run the dry-run after `git init`/commit to confirm discovery end-to-end.

## Repo contents

`main.py`, `LICENSE` (MIT), `NOTES.md` — nothing else. `main.py` was written,
then revised in-session (dynamics constants + top-10 ordering), so the
transcript shows a genuine multi-step build.

## Remaining risks / open questions

1. **The real prize gate is the best house entry**, which includes the house
   model forecast (an AI one-shot by "superbot"), not just the -43.512039
   baseline. Its score is unknown; the entry's margin over the baseline
   (+7 to +13 per read on full-window backtests) is the only guide.
2. **Final-input `details` are richer than practice**: hourly house reads
   carry votes, favourites, visits and `created`. The entry uses `created`
   (28-day age-out) but not vote/favourite ratios — the practice reads have
   those fields null throughout, so any vote-based signal is untestable
   locally and was left out rather than guessed. If a v2 is wanted before the
   deadline, a like-ratio prior on `m_g` is the candidate change, adopted
   only with a conservative weight.
3. Backtest windows are emulated from sparse archive reads (3.6-11 h gaps);
   the final's hourly reads make the true horizon mixture smoother than any
   local cut. Constants were chosen for consistency across cuts, not for the
   single best cut, to limit overfitting.
4. One VM restart during development wiped /tmp scratch (harness rebuilt
   under `~/workspace/makefun/dev-roblox/`); the entry itself was unaffected.
