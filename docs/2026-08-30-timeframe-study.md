# Which timeframe do these strategies work in on NIFTY?

*30 August 2026. 80 liquid NIFTY 500 names. Intraday from 59 sessions (the feed's cap),
daily from 5 years, weekly from 10. Reproduce with `uv run asymmetry timeframe-study`.*

## The short answer

**Continuation gets less hostile as the bar gets longer, and the crossover sits between 30
and 60 minutes.** Below it, NSE single-stock returns tilt mean-reverting and the cost of a
tight stop is crushing; above it, returns tilt persistent and costs stop mattering.

**But almost none of that is trend-following alpha.** A random entry with the same stop and
the same target earns nearly everything the signal earns. What improves with timeframe is
mostly your ability to hold a long in a market that rose while paying less in costs — which
is real money, and is not the same claim as "trend-following works better on the daily".

The one place the signal does something detectable, it does it in the wrong direction: **on
5-minute bars an EMA-stack continuation entry is significantly worse than entering at
random.**

---

## 1. What the market does, before any rule is applied

Variance ratios on log returns, computed within sessions only so overnight gaps are not mixed
into an intraday statistic. VR(2) above 1 means a two-bar move exceeds two one-bar moves —
returns extend. Below 1 they revert. The z is Lo-MacKinlay heteroskedasticity-robust.

Cross-section of 80 stocks, median across names:

| Timeframe | Bars | Median bar range | VR(2) | robust z | VR(4) | AC(1) | % of names VR>1 |
| --- | --: | --: | --: | --: | --: | --: | --: |
| 5m | 346,357 | 0.17 % | 0.982 | −0.69 | 0.970 | −0.0187 | **31 %** |
| 15m | 113,003 | 0.31 % | 0.985 | −0.37 | 0.982 | −0.0161 | 35 % |
| 30m | 56,363 | 0.44 % | 0.992 | −0.17 | 0.989 | −0.0092 | 45 % |
| 60m | 28,043 | 0.62 % | 1.018 | +0.33 | 1.019 | +0.0144 | **62 %** |
| daily | 90,612 | 2.39 % | 1.011 | +0.30 | 0.996 | +0.0097 | 62 % |
| weekly | 35,380 | 6.17 % | 0.993 | −0.13 | 1.013 | −0.0146 | 43 % |

**Not one timeframe is statistically distinguishable from a random walk.** Every |z| is well
under 1.96, and that is the honest headline: on this data no bar size is provably trending or
provably reverting.

What is nevertheless hard to dismiss is the **gradient**, which is monotone across four
independent columns and turns in the same place in all of them:

- the share of names with VR > 1 climbs 31 % → 35 % → 45 % → **62 %** from 5m to 60m;
- first-order autocorrelation is negative on 5m/15m/30m and crosses to positive at 60m;
- VR(2) and VR(4) both cross 1 between 30m and 60m.

A negative autocorrelation on 5-minute bars is the expected microstructure result — bid-ask
bounce and the reversal of transient order-flow pressure — and it is precisely the condition
under which a continuation entry buys the top of a wiggle. That effect washes out by the hour
bar.

Weekly turns back down, which is also unsurprising: multi-week horizons are where longer-run
mean reversion begins to show. It rests on the fewest bars of any row here.

### The index disagrees mildly with its constituents

| Timeframe | VR(2) | robust z | AC(1) |
| --- | --: | --: | --: |
| 5m | 1.011 | +0.55 | +0.0103 |
| 15m | 1.061 | +1.42 | +0.0597 |
| 30m | 1.014 | +0.37 | +0.0115 |
| 60m | 1.075 | +1.50 | +0.0692 |
| daily | 0.979 | −0.45 | −0.0216 |
| weekly | 1.037 | +0.45 | +0.0327 |

NIFTY itself is above 1 at every intraday horizon while its constituents are below 1 below the
hour. That is a real and well-known pattern — idiosyncratic single-name reversal coexisting
with index-level continuation, because the common factor persists while the stock-specific
noise reverts. Still nothing significant; the largest |z| in the table is 1.50 at 60m.

**If you trade the index rather than single stocks, the intraday picture is less hostile than
the stock table suggests.** That is the one place these two instruments point different ways.

---

## 2. The same rule at every bar size

One deliberately plain continuation rule, unchanged everywhere: enter when the 5/9/13/21 EMAs
*become* stacked above the 200 EMA, stop one ATR(14) below, target 3R. No per-timeframe
tuning — a rule optimised separately at each bar size would measure the optimiser.

Intraday rows pay 0.07 % round trip (0.025 % STT sell-side, plus slippage); daily and weekly
pay 0.17 % delivery.

| Timeframe | Trades | Win % | Median stop | Cost in R | Gross R | Net R | 95% CI on net |
| --- | --: | --: | --: | --: | --: | --: | --- |
| 5m | 8,944 | 23.6 | 0.20 % | 0.373 | −0.052 | **−0.426** | −0.46 to −0.39 |
| 15m | 2,748 | 25.5 | 0.37 % | 0.197 | +0.032 | **−0.165** | −0.23 to −0.10 |
| 30m | 1,255 | 28.3 | 0.56 % | 0.130 | +0.143 | **+0.013** | −0.09 to +0.11 |
| 60m | 482 | 33.0 | 0.76 % | 0.095 | +0.362 | **+0.268** | +0.10 to +0.44 |
| daily | 2,414 | 34.3 | 2.51 % | 0.070 | +0.381 | **+0.311** | +0.24 to +0.39 |
| weekly | 722 | 37.8 | 6.03 % | 0.030 | +0.581 | **+0.551** | +0.41 to +0.69 |

Everything moves monotonically with bar size — win rate, stop distance, cost in R, gross and
net. Read alone, this table says "trade the weekly". **It should not be read alone.**

---

## 3. The control that changes the answer

A long-only rule with a wide stop, replayed over a market that rose, makes money for reasons
that have nothing to do with the rule — and the longer the bar, the more of that drift it
collects. So every signal entry is matched with a **randomly chosen entry bar in the same
frame**, carrying the identical stop, target and holding cap.

| Timeframe | Signal gross | Random entry | **Excess** | t | Verdict |
| --- | --: | --: | --: | --: | --- |
| 5m | −0.052 | +0.008 | **−0.060** | **−2.34** | **worse than random** |
| 15m | +0.032 | +0.070 | −0.038 | −0.79 | no detectable edge |
| 30m | +0.143 | +0.164 | −0.021 | −0.29 | no detectable edge |
| 60m | +0.362 | +0.271 | +0.091 | +0.76 | no detectable edge |
| daily | +0.381 | +0.306 | +0.075 | +1.39 | no detectable edge |
| weekly | +0.581 | +0.537 | +0.044 | +0.44 | no detectable edge |

This is the table that matters, and it reframes section 2 completely.

**The timeframe gradient in net R is drift capture plus the cost curve, not a sharper signal.**
A random entry on the daily earns +0.306R of the signal's +0.381R. On the weekly, +0.537R of
+0.581R. The EMA stack is contributing between 4 and 9 basis points of R, and none of it
clears significance.

**At 5 minutes the signal is significantly worse than random** (t = −2.34). That is the only
result in this study that passes a 95 % test, and it is a negative one. It is exactly what
VR < 1 and AC(1) = −0.019 predict: on 5-minute bars, buying because the EMAs just stacked
means buying the end of a move that tends to give back.

The excess is largest at 60m (+0.091R) and daily (+0.075R), which agrees with the persistence
gradient — but with t of 0.76 and 1.39, that agreement is suggestive, not evidence.

---

## 4. What this means for the trident specifically

The trident is a continuation setup: it requires the EMA stack, a daily uptrend, and an
imbalance that price is expected to continue away from. So the persistence table applies to it
directly, and it explains the v2 measurement rather than merely coinciding with it:

| Entry TF | Trident net R (4R) | Market VR(2) | Trident cost in R |
| --- | --: | --: | --: |
| 5m | −0.986 | 0.982 (reverting tilt) | 1.205 |
| 15m | −0.692 | 0.985 | 0.712 |
| 30m | −0.538 | 0.992 | 0.499 |

The strategy is being run at the three bar sizes where this study finds continuation least
supported and costs highest — and its cost drag is far worse than the generic rule's because
its stop is structural (the doji's low) rather than volatility-scaled, so it is systematically
tighter. At 0.20 % it surrenders 1.2R before the trade opens.

**And its kill zone rules out the timeframe the persistence gradient favours.** NSE serves
seven 60-minute bars a session; only four fall inside 09:15–12:45. A completed pattern needs
five, so with four the doji can only print on the window's last bar and the confirmation has
nowhere to go. The 60-minute trident is impossible not because the setup fails there but
because the transplanted window is too short to contain it — which is the single most
actionable thing in this document, given that 60 minutes is where the persistence gradient
first turns favourable.

---

## 5. So: which timeframe, and what should actually be done

**For a continuation or trend-following trade on NSE single stocks: 60 minutes or longer.**
Below the hour the returns tilt against you and the cost of a tight stop is decisive. That is
supported by four independent columns of the persistence table and by the sign of the excess.

**For the intraday index rather than single stocks:** the picture is less hostile at every
horizon, but still nothing significant.

**Do not read the daily/weekly net R as trend-following edge.** It is mostly drift, and drift
is available without any signal at all. The practical implication is not "run this rule on the
weekly" — it is that a long-only rule with a wide stop in a rising market will look good, and
the way to tell whether your rule is doing anything is to run the random-entry control against
it. That control now ships as part of `timeframe-study`.

**Three concrete changes this argues for on the trident**, none of which have been made:

1. Test a 60-minute track, which requires widening the kill zone past 12:45 first — the
   window sweep already suggests the full session is no worse than the transplant.
2. Treat the 5-minute track as a measurement exercise rather than a candidate for live use.
   It is running at the bar size where this study finds an entry of this family actively
   harmful, and where costs are worst.
3. Stop reading net R across timeframes as a ranking of the setup. It is a ranking of stop
   widths.

---

## What this study cannot tell you

- **Nothing here is significant except one negative result.** Every persistence z is inside
  ±1.96 and every excess except 5m is inside ±1.96. A gradient consistent across columns is
  suggestive; it is not proof, and this document should not be quoted as if it were.
- **The samples are not the same length.** Intraday is 59 sessions — one market period.
  Daily is 5 years, weekly 10. The intraday rows are "this window", not "this timeframe".
- **One regime, one direction.** Long only, and a period in which Indian equities rose.
  Persistence is regime-dependent; a study run over 2008 or 2020 would very likely disagree.
- **A timeframe where continuation persists is one where a trend rule is *possible*,** not one
  where any particular rule works. This tested one rule.
- **The 200-bar warm-up matters and is enforced.** An earlier draft let the 200 EMA "confirm"
  at bar two, which inflated trade counts with entries taken on an unwarmed indicator.

Nothing here has been traded. The system places no orders and holds no credential that could.
