"""The price-action setups V3 permits (§9).

V3 is deliberately narrow. Generic "breakout" detection is what produced the mediocre
candidates — it fires on any stock making a new high, most of which have no asymmetry left.
Every setup here describes a specific imbalance rather than mere strength:

* **Liquidity sweep** — price takes out an obvious prior low, sellers fail to follow
  through, and the level is reclaimed. Whoever sold the breakdown is now trapped, and the
  reclaim is the tell. This is the setup that caught ZEEL: it swept a 92.00/92.57/93.36
  cluster on 11 Aug at 90.00, then reclaimed +6.5% and +5.7%.

* **High-tight flag** — a strong impulse, then a tight, controlled pause near the extreme.
  Compression before expansion, with the prior impulse proving the stock can travel.

* **Base breakout on volume** — a tight base, then a range expansion out of it on a volume
  surge. This is *not* the generic breakout the paragraph above rejects: the volume
  requirement is what separates a real change of hands from a drift to a new high, and it is
  the difference that makes it selective. Measured over the stored NIFTY 500 on 14 Aug 2026
  it fired on 6 of 473 liquid names. It exists because LGEINDIA — a 9.6% expansion out of a
  3.7% base on 36.8x volume, with a genuine earnings catalyst — was invisible to the other
  two: its pre-move impulse measured 7.3% against the flag's 8.0% flagpole minimum.

All three are implemented symmetrically for long and short, since V3 allows either
direction.

**None of them may be labelled by the move they are trying to catch.** Each measures its
setup strictly on the bars *preceding* the confirming bar, so a signal that appears today
would have appeared on today's data alone. `fetch_chart(as_of=...)` enforces the same
property upstream by truncating the frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import settings
from ..spec import SetupType
from .indicators import atr
from .structure import find_pivots


@dataclass
class SetupSignal:
    """A detected setup, or an explicit absence."""

    kind: SetupType = SetupType.NONE
    direction: str = "long"          # "long" | "short"
    found: bool = False
    quality: float = 0.0             # 0-100
    level: float | None = None       # the swept or flagged level
    note: str = ""

    @property
    def is_short(self) -> bool:
        return self.direction == "short"


# ── Setup A: liquidity sweep ──────────────────────────────────────────────────


def detect_liquidity_sweep(
    frame: pd.DataFrame, *, direction: str = "long", lookback: int = 40, cluster_pct: float = 1.5
) -> SetupSignal:
    """Sweep of a prior extreme followed by a failure and reclaim.

    The sequence V3 specifies, in order:
      1. price approaches a meaningful prior low (or high, for shorts)
      2. price sweeps beyond it
      3. the follow-through fails
      4. the level is reclaimed

    Requiring a *cluster* of prior pivots rather than a single one matters: one stray low is
    noise, whereas several lows within ~1.5% of each other is where stops actually sit, and
    that is what makes the sweep meaningful.
    """
    if frame is None or len(frame) < lookback + 10:
        return SetupSignal(direction=direction, note="insufficient history")

    long_side = direction == "long"
    window = frame.tail(lookback)

    # The shelf must be located from structure that existed *before* the sweep. Searching
    # the whole window lets the sweep's own low become the anchor, after which nothing has
    # been swept by definition and every real setup is missed.
    recent_bars = 8
    prior = window.iloc[:-recent_bars]
    if len(prior) < 10:
        return SetupSignal(direction=direction, note="insufficient prior structure")

    highs, lows = find_pivots(prior, window=2)
    pivots = lows if long_side else highs
    if not pivots:
        return SetupSignal(direction=direction, note="no prior pivot to sweep")

    # The level is anchored on the extreme pivot, but its *significance* is measured by how
    # many bars traded into that shelf — not by how many strict fractal pivots sit there.
    # A descending sequence like 92.00 / 92.57 / 93.36 contains only one fractal low, yet it
    # is plainly a support shelf where stops accumulate, and counting pivots alone scored
    # exactly that shape as weak.
    anchor = min(pivots) if long_side else max(pivots)
    level = float(anchor)

    band = level * (1 + cluster_pct / 100) if long_side else level * (1 - cluster_pct / 100)
    touches = int(
        ((prior["low"] >= level) & (prior["low"] <= band)).sum()
        if long_side
        else ((prior["high"] <= level) & (prior["high"] >= band)).sum()
    )
    touches = max(touches, 1)

    # Look for the sweep in the recent past, then a reclaim by the latest bar.
    recent = frame.tail(recent_bars)
    close = float(frame["close"].iloc[-1])

    if long_side:
        swept = float(recent["low"].min()) < level
        reclaimed = close > level
        sweep_depth = (level - float(recent["low"].min())) / level * 100
    else:
        swept = float(recent["high"].max()) > level
        reclaimed = close < level
        sweep_depth = (float(recent["high"].max()) - level) / level * 100

    if not swept:
        return SetupSignal(direction=direction, level=level, note="level not swept")
    if not reclaimed:
        return SetupSignal(
            direction=direction, level=level,
            note=f"swept {level:,.2f} but not reclaimed — still below" if long_side
            else f"swept {level:,.2f} but not rejected — still above",
        )

    # Quality: a shallow sweep that reclaims decisively is the cleanest version. A very deep
    # sweep is a genuine breakdown that happens to have bounced, which is a weaker thesis.
    depth_score = float(np.clip(100 - sweep_depth * 25, 20, 100))
    # A single clean touch is already a valid level; more touches make it a better one. The
    # floor stays high because a well-defined single pivot that gets swept and reclaimed is
    # exactly the setup V3 describes.
    touch_score = float(np.clip(50 + touches * 18, 50, 100))
    reclaim_pct = abs(close - level) / level * 100
    reclaim_score = float(np.clip(reclaim_pct * 40, 10, 100))
    quality = 0.45 * depth_score + 0.25 * touch_score + 0.30 * reclaim_score

    return SetupSignal(
        kind=SetupType.RECLAIM,
        direction=direction,
        found=True,
        quality=round(quality, 1),
        level=round(level, 2),
        note=(
            f"swept {level:,.2f} ({touches} prior touch{'es' if touches > 1 else ''}) "
            f"by {sweep_depth:.1f}%, "
            f"{'reclaimed' if long_side else 'rejected'} by {reclaim_pct:.1f}%"
        ),
    )


# ── Setup B: high-tight flag ──────────────────────────────────────────────────


def detect_high_tight_flag(
    frame: pd.DataFrame,
    *,
    direction: str = "long",
    impulse_window: int = 20,
    flag_window: int = 8,
    min_impulse_pct: float = 8.0,
) -> SetupSignal:
    """Strong impulse, then a tight pause holding near the extreme of the move.

    The three conditions that make it "high and tight": the impulse must be large enough to
    prove the stock travels, the pause must be genuinely narrow relative to that impulse,
    and price must hold near the extreme rather than giving most of it back.
    """
    if frame is None or len(frame) < impulse_window + flag_window + 5:
        return SetupSignal(direction=direction, note="insufficient history")

    long_side = direction == "long"
    flag = frame.tail(flag_window)
    impulse = frame.tail(impulse_window + flag_window).head(impulse_window)

    if long_side:
        impulse_low = float(impulse["low"].min())
        impulse_high = float(impulse["high"].max())
        impulse_pct = (impulse_high - impulse_low) / impulse_low * 100
        extreme = impulse_high
    else:
        impulse_high = float(impulse["high"].max())
        impulse_low = float(impulse["low"].min())
        impulse_pct = (impulse_high - impulse_low) / impulse_high * 100
        extreme = impulse_low

    if impulse_pct < min_impulse_pct:
        return SetupSignal(
            direction=direction, note=f"impulse only {impulse_pct:.1f}% — not a flagpole"
        )

    flag_high = float(flag["high"].max())
    flag_low = float(flag["low"].min())
    flag_depth = (flag_high - flag_low) / flag_high * 100

    # The pause must be tight relative to the impulse that preceded it.
    if flag_depth > impulse_pct * 0.5:
        return SetupSignal(
            direction=direction,
            note=f"consolidation {flag_depth:.1f}% too loose against a {impulse_pct:.1f}% impulse",
        )

    close = float(frame["close"].iloc[-1])
    retention = (
        (close - impulse_low) / (impulse_high - impulse_low)
        if long_side else
        (impulse_high - close) / (impulse_high - impulse_low)
    ) * 100
    if retention < 60:
        return SetupSignal(
            direction=direction,
            note=f"gave back too much — holding only {retention:.0f}% of the impulse",
        )

    # Volatility contraction confirms the pause is orderly rather than a stall.
    day_atr = atr(frame["high"], frame["low"], frame["close"], 14)
    contracting = bool(
        len(day_atr.dropna()) > flag_window
        and float(day_atr.iloc[-1]) < float(day_atr.iloc[-flag_window])
    )

    tightness = float(np.clip(100 - (flag_depth / max(impulse_pct, 1e-9)) * 200, 20, 100))
    quality = (
        0.4 * tightness
        + 0.35 * float(np.clip(retention, 0, 100))
        + 0.25 * (100 if contracting else 40)
    )

    return SetupSignal(
        kind=SetupType.CONTINUATION,
        direction=direction,
        found=True,
        quality=round(quality, 1),
        level=round(flag_high if long_side else flag_low, 2),
        note=(
            f"{impulse_pct:.1f}% impulse, {flag_depth:.1f}% flag, holding {retention:.0f}% "
            f"of the move{', ATR contracting' if contracting else ''}"
        ),
    )


def detect_base_breakout(
    frame: pd.DataFrame,
    *,
    direction: str = "long",
    base_window: int | None = None,
    max_depth_pct: float | None = None,
    min_volume_mult: float | None = None,
) -> SetupSignal:
    """A tight base, then expansion out of it on a volume surge.

    The base is measured on the ``base_window`` bars *ending one bar before the last*, and
    the breakout is judged on the final bar alone. That ordering is the whole integrity of
    the setup: the base has to have existed before the move, so the signal cannot be a
    relabelling of an explosion after the fact. LGEINDIA is the worked example — it fires on
    14 Aug 2026 and is silent on the 13th, from the same code and the same frame.

    Volume is the discriminator, not the breakout. Price clearing a base is common and
    mostly worthless; price clearing it while volume multiplies is a change of hands.
    """
    # Resolved here rather than in the signature. A default argument binds once at import,
    # and `settings` is a module-level singleton — so `base_window: int = settings.x` would
    # freeze the value at import time and silently ignore any later change, which is the
    # same trap that made `upstox_auth` verify against an expired token. Until 19 Aug 2026
    # these three were hardcoded and the config keys were dead: tuning them did nothing.
    base_window = settings.base_breakout_window if base_window is None else base_window
    max_depth_pct = (
        settings.base_breakout_max_depth_pct if max_depth_pct is None else max_depth_pct
    )
    min_volume_mult = (
        settings.base_breakout_min_volume_mult if min_volume_mult is None else min_volume_mult
    )

    long_side = direction == "long"
    if frame is None or len(frame) < base_window + 25:
        return SetupSignal(direction=direction, note="insufficient history")
    if "volume" not in frame:
        return SetupSignal(direction=direction, note="no volume data")

    base = frame.iloc[-(base_window + 1) : -1]
    bar = frame.iloc[-1]
    base_high, base_low = float(base["high"].max()), float(base["low"].min())
    if base_high <= 0:
        return SetupSignal(direction=direction, note="unusable base")

    depth_pct = (base_high - base_low) / base_high * 100
    if depth_pct > max_depth_pct:
        return SetupSignal(
            direction=direction,
            note=f"base {depth_pct:.1f}% deep — not a base, just a range",
        )

    close = float(bar["close"])
    level = base_high if long_side else base_low
    if (long_side and close <= level) or (not long_side and close >= level):
        return SetupSignal(
            direction=direction,
            level=round(level, 2),
            note=f"coiled in a {depth_pct:.1f}% base, {level:,.2f} not yet cleared",
        )

    baseline_volume = float(base["volume"].mean())
    if baseline_volume <= 0:
        return SetupSignal(direction=direction, note="no volume baseline")
    relative_volume = float(bar["volume"]) / baseline_volume
    if relative_volume < min_volume_mult:
        return SetupSignal(
            direction=direction,
            level=round(level, 2),
            note=(
                f"cleared {level:,.2f} on only {relative_volume:.1f}x volume — "
                "a drift, not a change of hands"
            ),
        )

    # Quality: how tight the base was, how heavy the surge, and whether the bar closed at
    # its extreme rather than giving the move back into the close.
    tightness = float(np.clip(100 - (depth_pct / max_depth_pct) * 100, 0, 100))
    surge = float(np.clip(np.log10(relative_volume) * 100, 0, 100))
    span = float(bar["high"]) - float(bar["low"])
    if span > 0:
        closing = (close - float(bar["low"])) / span * 100
        if not long_side:
            closing = 100 - closing
    else:
        closing = 50.0
    quality = 0.35 * tightness + 0.35 * surge + 0.30 * closing

    return SetupSignal(
        kind=SetupType.BASE_BREAKOUT,
        direction=direction,
        found=True,
        quality=round(quality, 1),
        level=round(level, 2),
        note=(
            f"cleared a {base_window}-bar base ({depth_pct:.1f}% deep) at {level:,.2f} "
            f"on {relative_volume:.1f}x volume, closing {closing:.0f}% of the bar's range"
        ),
    )


# ── How a setup is named to a reader ──────────────────────────────────────────


# `SetupType` values are the engine's identifiers: they key the carry exemption
# (`v3_carry_gated_setups`), the `--setup` CLI filter and every backtest cohort, so they are
# direction-neutral and must stay that way. What a *reader* needs is the mechanic, and the
# mechanic is mirrored, not shared: a short "reclaim" is price sweeping a prior high and
# being rejected back below it — a failed breakout. PIIND published on 14 Aug 2026 tagged
# "reclaim" on a SHORT, with its own note underneath correctly reading "rejected by 10.2%".
# The trade was right and the label contradicted it, which is the cheapest possible way to
# lose a reader's trust in the rest of the card.
_DIRECTIONAL_LABELS: dict[SetupType, tuple[str, str]] = {
    #                     long                short
    SetupType.RECLAIM: ("reclaim", "failed breakout"),
    SetupType.BASE_BREAKOUT: ("base breakout", "base breakdown"),
}


def setup_label(kind: SetupType, direction: str) -> str:
    """The reader-facing name of a setup, in the direction it is actually being traded.

    Display only — never parse this back, and never gate on it. `CONTINUATION` is absent
    from the table on purpose: a flag reads the same either way, so inventing a second name
    for it would add a word without adding meaning.
    """
    labels = _DIRECTIONAL_LABELS.get(kind)
    if labels is None:
        return kind.value
    return labels[0] if direction == "long" else labels[1]


def detect_setup(frame: pd.DataFrame, direction: str = "long") -> SetupSignal:
    """Best of the permitted setups, or an explicit none.

    V3 allows nothing else, so a stock making a new high with no sweep, no flag and no
    volume-backed base breakout simply does not qualify — which is the point of the narrow
    taxonomy.
    """
    sweep = detect_liquidity_sweep(frame, direction=direction)
    flag = detect_high_tight_flag(frame, direction=direction)
    base = detect_base_breakout(frame, direction=direction)

    found = [s for s in (sweep, flag, base) if s.found]
    if not found:
        # Surface the most informative explanation available.
        for candidate in (sweep, base, flag):
            if candidate.level is not None:
                return candidate
        return flag
    return max(found, key=lambda s: s.quality)
