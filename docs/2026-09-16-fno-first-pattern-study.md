# FnO first-pattern study — 16 September 2026

## Decision

Propose one inactive research pair: **bearish prior-session-high liquidity sweep and reclaim
on completed 15-minute bars**, mapped to a long put only after the option layer is available.

Do not activate the bullish mirror. Its best development variant produced `+0.0533R` over
1,580 outcomes, then fell to `-0.0431R` in validation and `-0.2786R` in the later evaluation
window.

## Frozen underlying geometry

- Anchor: previous completed session high.
- Direction: bearish only.
- Sweep: high trades at least 30 bps above the anchor.
- Reclaim: a completed bar closes at least 20 bps below the anchor within two bars beginning
  with the sweep bar.
- Entry label: next completed 15-minute bar open in the same session.
- Stop: highest sweep extreme observed before confirmation.
- Eligibility: structural stop distance must already be 0.5–2.0%; it is not widened to fit.
- Target: fixed 2R.
- Expiry: close of the third entry-inclusive session.
- Same-bar conflict: stop first when no finer point-in-time evidence exists.
- Vehicle after later approval: long put; no option-writing path.

## Chronological evidence

The clean-room study rebuilt 15-minute bars from 840 immutable raw capture windows covering
210 FnO stocks from 15 June through 15 September 2026. It evaluated 128 predeclared
direction/threshold combinations. Selection used development expectancy only.

| Window | Dates | Outcomes | Targets | Stops | Expiries | Mean R |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development | 16 Jun–31 Jul | 866 | 275 | 530 | 61 | +0.0753 |
| Validation | 4 Aug–31 Aug | 428 | 127 | 241 | 60 | +0.0972 |
| Observed evaluation | 3 Sep–16 Sep | 159 | 67 | 64 | 28 | +0.6039 |

The observation-set hash is
`5e338b0d7ff7d8d1b59f367a677310fc40bbea08a14fc6968f85f7e6c1479d24` and the
reproducible report hash is
`94fe3fb2b70ba6a7e771e41da3b2731d486e904a84aea9a0cb6bf16c663a602a`.

## Limitations and promotion status

These are underlying geometry labels, not option profits. They exclude bid/ask spreads,
slippage, fees, taxes, missed fills, and premium behavior. The threshold search creates
selection bias; the period is short; and the strongest later result overlaps a bearish
market regime. Because the evaluation results have now been observed, this window cannot be
presented as untouched evidence in a future promotion decision.

The proposal therefore remains **inactive research only**. It requires a new untouched
historical cohort, provider-timestamped option BBO replay with costs, and 20 immutable
forward paper alerts before promotion can be considered.
