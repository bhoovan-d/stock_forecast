# Last week's positive 1:3 / 1:4 example — KEI

## Bottom line

The point-in-time replay found one clean stock example in the 1–4 September 2026 trading
week: **KEI, short, base breakdown**, triggered at 12:45 IST on 1 September.

- Entry: **₹5,384.81**
- Structural stop: **₹5,460.00**
- Risk: **₹75.19 per share**, or **1.396%**
- 3R target: **₹5,159.24**, reached 2 September at 14:00
- 4R target: **₹5,084.03**, reached 4 September at 09:15
- Round-trip cost estimate: **0.122R**
- Net result at 3R: approximately **+2.88R**
- Net result at 4R: approximately **+3.88R**

The stop was never touched first. The worst adverse excursion before resolution was about
0.47R; the highest traded price after entry and before the 4R exit was ₹5,420, still below
the ₹5,460 stop.

This is a valid worked winner, but it was **not a published V3 recommendation**. Its
point-in-time quality score was 47.78 against the selective-regime publication floor of 76.
The distinction matters: the geometry and outcome worked; the complete live selection model
would have refused it.

## How it was found without looking into the future

KEI was in the 150-name uniform sample drawn with seed 1 from the universe frozen on
13 July 2026. It was not selected because it subsequently fell. At each replay decision the
engine received only daily, hourly and 15-minute bars timestamped at or before that moment.

At 12:45 on 1 September, the shared live setup detector identified a short base breakdown:

> Cleared an 8-bar base, 5.8% deep, at ₹5,442 on 2.3× volume, closing 85% of the bar's range.

The short entry was a stop-through order at ₹5,384.81, derived from the ₹5,387.50 trigger
level. The 12:45 bar traded down to ₹5,375.50, so the entry was actually crossed; this was
not a resting order that never filled. The valid-fill band was ₹5,379.31–₹5,432.84.

The nearest valid 15-minute structural high put the stop at ₹5,460. This left a 1.396% stop,
inside V3's required 0.5–1.5% band. Four times the ₹75.19 per-share risk placed the target at
₹5,084.03, a 5.59% move. The stock's point-in-time ADR was 2.80% and ATR was 3.31%, so the
move-feasibility check accepted the target.

## Higher-timeframe confirmation

The carry score was 62.2, above the 60 floor. It passed seven important continuation checks:

- 60-minute and 120-minute EMA alignment;
- neither hourly trend opposed the short;
- a 120-minute setup was present;
- price occupied the correct half of the 120-minute range;
- volume showed contraction followed by expansion.

It failed the headroom check—there was not a clean opposing-level-free route all the way to
the target—but the aggregate carry score still passed under the rule used in this replay.

## Bar-by-bar outcome

| Session | Open | High | Low | Close | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 Sep, after 12:45 | ₹5,395.50 | ₹5,420.00 | ₹5,310.00 | ₹5,348.00 | Entry crossed; stop untouched |
| 2 Sep | ₹5,309.50 | ₹5,332.00 | ₹5,132.50 | ₹5,170.00 | 3R reached at 14:00 |
| 3 Sep | ₹5,223.50 | ₹5,378.00 | ₹5,173.50 | ₹5,327.00 | Pullback, still below stop |
| 4 Sep, 09:15 bar | ₹4,995.00 | ₹5,025.00 | ₹4,863.00 | ₹4,975.00 | Gapped through the 4R target |

The replay books exactly +4R at the target even though the favourable 4 September gap could
have produced a better fill. That keeps this example comparable with every other replayed
trade and avoids claiming price improvement after the fact. A 15-minute bar touching both
stop and target would have been booked as a loss; no such ambiguity occurred here.

## Why V3 did not publish it

The 1 September regime was selective, so publication required 76/100. KEI scored 47.78:

| Module | Score |
| --- | ---: |
| Relative strength vs NIFTY | 58.7 |
| Relative strength vs sector | 67.9 |
| Sector leadership | 5.3 |
| Weekly/daily structure | 22.0 |
| Setup quality | 47.4 |
| Entry quality | 60.4 |
| Catalyst | 50.0 (neutral; none found) |
| Volatility feasibility | 44.1 |
| Carry | 62.2 |

Weak sector leadership and higher-timeframe structure outweighed the valid breakdown and
carry confirmation. The generated 1 September V3 brief consequently published zero trades.

This is useful evidence about the model, but it is not proof that the quality threshold
should be lowered. Selecting a known winner and then changing the threshold to include it
would be hindsight tuning. The correct next test is whether low-scoring but geometrically
valid base breakdowns win as a full cohort out of sample—not whether this one winner can be
made to qualify retrospectively.

## Capital warning

Under the legacy ₹5,000 risk budget, the plan would size about 66 shares: roughly ₹3.55 lakh
of short notional for approximately ₹4,962 of initial stop risk. Account equity was not
configured at the time, so that quantity is an arithmetic illustration, not an approved or
fundable position size.

## Conclusion

Yes, the engine's geometry can identify a real 1:3 and 1:4 winner: KEI reached both levels
without first touching its stop. What this example does **not** establish is that the full
ranking model can select such winners prospectively. It demonstrates that the entry, stop,
target and settlement machinery worked on this case, while also exposing that the current
quality layer rejected it.
