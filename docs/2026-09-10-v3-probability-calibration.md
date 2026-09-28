# V3 probability calibration — 10 September 2026

## Bottom line

The system can now estimate the chance of reaching 3R and 4R, but it will not call that
estimate a trade unless the evidence is strong enough. On the present evidence, **no V3
setup is yet allowed to produce a funded alert**.

This is not because the model failed to measure the past. Its broad forecasts stayed close
to what happened in the later period. The problem is that the reliable setup either has too
little safety margin (reclaim) or too few genuinely comparable examples (base breakout).

## What was tested

- The stock list was frozen on 13 July 2026, before any trade used in this measurement.
- A fixed random sample of 150 symbols was chosen from that list.
- The engine replayed its real 15-minute entry, stop and target rules.
- 5,478 trades were found; 4,210 remained after all trades before the stock-list freeze
  were removed.
- Trades from 14 July through 18 August taught the probabilities.
- Trades from 19 August through 3 September were kept untouched and used only to check
  whether those probabilities held up.
- If the stop and target appeared in the same 15-minute candle, the trade was counted as a
  loss. Costs and slippage were included.

## What happened later

| Group | Earlier chance | Later result | What it means |
| --- | ---: | ---: | --- |
| All setups reaching 3R | 11.8% | 11.8% | The broad forecast was accurate, but the chance was low. |
| All setups reaching 4R | 9.6% | 9.5% | Same: accurately low, not fundable. |
| Reclaims reaching 3R | 32.3% | 30.7% | Stable, but the conservative estimate does not safely clear costs and losses. |
| Reclaims reaching 4R | 26.6% | 24.4% | Close to break-even, but not enough safety margin to risk money. |
| Base breakouts reaching 4R | 57.8% | 51.7% | Very promising, but only 45 earlier and 29 later examples. |
| Carry-approved base breakouts reaching 4R | 80.0% | 71.4% | Only 5 earlier and 7 later examples: far too small to trust. |

The base-breakout percentages are not a forecast the system may use. The live engine
requires the carry filter to pass, and that exact group has only 12 examples. A striking
percentage from 12 trades can disappear quickly.

## What the system requires before it predicts a funded trade

For each candidate, all of these must be true:

1. Its exact setup and carry condition has enough earlier examples and enough later checks.
2. The later result stayed close to the earlier probability.
3. The low, conservative end of the probability range still clears break-even after costs.
4. Expected profit after costs is at least +0.15R per trade.
5. The 15-minute candle is complete and comes from authenticated live Upstox data.
6. The NSE cash session is open and the model is no more than 30 days old.
7. Account equity is configured, so the position can be sized without exceeding the risk
   and portfolio limits.

If one condition fails, the candidate is shown as rejected and no funded alert is issued.

## Commands

Build and check the evidence:

```text
uv run asymmetry v3-calibrate --symbols-file data/universe-2026-07-13.json
```

Use the already captured bars for a reproducible rerun:

```text
uv run asymmetry v3-calibrate --symbols-file data/universe-2026-07-13.json --cache-only
```

Once account equity and live Upstox access are configured, check once during market hours:

```text
uv run asymmetry v3-monitor --once
```

Or keep checking shortly after every completed 15-minute candle:

```text
uv run asymmetry v3-monitor --loop
```

The monitor is decision support only and contains no order-placement code.
