# Base breakout — specification

*Written 19 Aug 2026 in answer to worksheet Q12. This is what the code does **today**, stated
exactly, so you can confirm it or replace it. Every number is given with where it came from,
because most of them were chosen, not measured.*

Implementation: `detect_base_breakout` in `engines/setups.py`.

---

## The idea in one line

A stock goes quiet in a tight range, then leaves that range on a **volume surge**. The
volume is the signal; the price break on its own is worthless and common.

This is deliberately *not* the generic breakout the spec rejects elsewhere. "Price made a
new high" fires constantly and mostly has no asymmetry left. Requiring the range to have
been genuinely tight, and the exit from it to come on multiplied volume, is what makes it
selective — it fired on **6 of 473 liquid names** on 14 Aug 2026.

## Timeframe

**Daily bars.** Detected in stage one from stored EOD data, no network. Entry is then
located on the 15-minute chart by `build_v3_plan`, like every other setup.

## The rule, exactly

Let the most recent daily bar be the **breakout bar**. Everything else is measured on the
**8 bars before it** — the base.

**1. Enough history.** At least 33 daily bars (`base_window + 25`), else no signal.

**2. Define the base** — the 8 bars *ending one bar before the last*:

    base_high = max(high) over those 8 bars
    base_low  = min(low)  over those 8 bars
    depth%    = (base_high − base_low) / base_high × 100

**3. The base must be tight.** `depth% ≤ 8.0`, else rejected as *"not a base, just a
range"*.

**4. The breakout bar must clear it.**
- long: `close > base_high`
- short: `close < base_low`

Note it is the **close**, not an intraday poke through.

**5. The volume test — the discriminator.**

    relative_volume = breakout_bar.volume / mean(volume over the 8 base bars)

Requires `relative_volume ≥ 2.0×`, else rejected as *"a drift, not a change of hands"*.

**6. Quality score (0–100)**, used for ranking only, never to reject:

    tightness = 100 − (depth% / 8.0) × 100          → tighter base scores higher
    surge     = log10(relative_volume) × 100        → 2× ≈ 30, 10× = 100, capped
    closing   = where in the bar's range it closed  → 100 = closed at the extreme

    quality = 0.35 × tightness + 0.35 × surge + 0.30 × closing

`closing` is inverted for shorts. The intent is that a bar which surges and then gives the
move back into the close is worth less than one that finishes on its high.

## Why it is measured on the bars *before* the last one

This is the integrity property, and it is the reason the base excludes the breakout bar. The
base has to have existed *before* the move, or the "setup" is just a relabelling of an
explosion after the fact. LGEINDIA is the worked example: it fires on 14 Aug 2026 and is
silent on the 13th, from identical code on the same frame.

## What it does *not* require

Worth stating, because these are the obvious things you might assume are in there:

- **No trend or moving-average filter of its own.** A base breakout in a downtrend still
  fires — though the separate weekly/daily trend veto will refuse it for a long.
- **No minimum base *duration* beyond 8 bars.** The window is exactly 8; a 3-week base and a
  perfect 8-day base are treated identically.
- **No prior uptrend or "flat base after advance" requirement.**
- **No retest.** Entry is on the breakout, not on a pullback to the broken level.
- **No gap handling.** A bar that gaps out of the base and closes higher qualifies the same
  as one that grinds out.

## Where the numbers came from — read this before trusting them

| Parameter | Value | Provenance |
| --- | --- | --- |
| base window | 8 bars | **Chosen.** Never tested against 5, 10, 15. |
| max depth | 8.0% | **Chosen.** |
| min volume multiple | 2.0× | **Chosen** — the one with a stated rationale: below ~2× it is not a change of hands. Still untested against 1.5× or 3×. |
| quality weights (.35/.35/.30) | — | **Chosen.** |
| `log10` surge scaling | — | **Chosen**, so 36× volume does not swamp the score. |

**None of the thresholds has been tuned or validated.** They are engineering defaults that
have never been varied, so the setup's measured performance is the performance of *one
untested parameter set*, not of the best version of this idea.

### The settings are now wired (19 Aug 2026)

They were not. `config.py` defined `base_breakout_window`,
`base_breakout_max_depth_pct` and `base_breakout_min_volume_mult`, and **nothing read
them** — the detector had the values hardcoded, so tuning the config changed nothing.

They are resolved inside the function body rather than as default arguments: a default
binds once at import and `settings` is a module-level singleton, so `base_window: int =
settings.x` would freeze the value at import time and silently ignore later changes. That
is the same trap that made `upstox_auth` verify against an expired token.

Verified against real stored data — `min_volume_mult` at 1.2 / 2.0 / 6.0 yields 7 / 6 / 5
base-breakout candidates across the NIFTY 500 on 14 Aug 2026.

## What it has actually done

Replay of 2,579 triggers, 80 symbols, 50 sessions, costs of 0.17R subtracted:

| Cohort | n | Win | Net/trade | Total |
| --- | --: | --: | --: | --: |
| base-breakout, all triggers | 63 | 31.1% | **+0.44R** | +27.9R |
| — carry-gate admitted | 9 | 37.5% | **+0.87R** | +7.8R |
| — carry-gate rejected | 54 | 30.2% | +0.37R | +20.2R |
| long | 38 | 29.7% | +0.37R | +13.9R |
| short | 25 | 33.3% | +0.56R | +14.1R |

Against the other two setups, admitted:

| | n | Win | Net/trade |
| --- | --: | --: | --: |
| reclaim | 669 | 26.0% | +0.18R |
| continuation | 45 | 20.5% | −0.13R |

**Per trade it is the best thing in the system — roughly 2.4× reclaim's expectancy.** It is
also the rarest: 63 triggers against reclaim's 669, and only **9** survive the carry gate.

### How much of that to believe

- **9 admitted trades establishes nothing.** The +0.87R agrees with the mechanism and is
  statistically meaningless on its own.
- Even the ungated 63 is thin, and 31% of the total R comes from a handful of winners.
- Same hindsight-selected 80-symbol universe as every other measurement here, so treat the
  level as an upper bound.
- **The carry gate rejects 54 of 63 while those rejects still made +0.37R.** That is worth
  investigating: the gate applies to base-breakout by configuration, and this is weak
  evidence that it should not — the same shape as the reclaim exemption, which was granted
  for exactly this reason.

## Decisions I need from you

These are the places where my chosen defaults may not match how you actually trade it:

1. **Is 8 bars your base?** Mark the base start and end on 2–3 real examples — the number
   that repeats is your rule, and it is very likely not 8.
2. **Is 8% tight enough to call a base?** On a 3% ADR stock, 8% is three days of normal
   movement.
3. **Is 2× volume your bar?** Against the base's *mean* volume, or against a longer 20/50-day
   average? The current choice makes a quiet base easier to surge out of.
4. **Close beyond the level, or intraday break?** Currently the daily close.
5. **Do you want the retest variant?** Entering on the pullback to the broken level is a
   materially different trade and is not built.
6. **Should the carry gate apply to it at all?** The 54 rejected trades made +0.37R.

Answer 1–3 with marked examples and I can fit the parameters to what you actually do rather
than to what I guessed.
