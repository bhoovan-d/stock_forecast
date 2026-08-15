"""The carry test — is this a continuation regime, or just an interesting chart?

V3's hierarchy is right market → right sector → right stock → right time → right
risk/reward, and the engine was implementing it across two timeframes: daily/weekly to pick
the stock, 15m to place the entry. Nothing in between asked the question that decides
whether a position survives 1–5 sessions: **is the stock in a regime that carries?**

The gap was not subtle. The 60m read existed but ran *after* a candidate had already
qualified, and was documented as "a description, not a filter" — so a name whose 60m fetch
failed outright still published, which is exactly what happened to JYOTICNC on 14 Aug 2026.

This module fills the middle rung:

    Daily / Weekly   why this stock, and the regime      (stage 1, no network)
    60m / 120m       is there actually a carry setup     (this module)
    30m / 15m        where exactly to enter              (v3.build_v3_plan)

It answers with a **checklist and a score**, and both must pass. Three conditions gate — a
structure to continue from, fuel to move it, and room to run — so a rejection can always
name what failed rather than pointing at an opaque number. Everything else is scored, where
a weak reading costs points instead of ending the evaluation. See ``CORE_CONDITIONS`` for
why that split is where it is; making all eight gating admitted 2 of 2,527 replayed
triggers, which is unmeasurable rather than strict.

Two deliberate choices:

* **Fail closed.** No 60m data means the carry regime is unproven, which is a rejection.
  Treating missing data as "fine" is what let an unverified name onto the site.

* **Direction-symmetric.** ``structure.analyse_timeframe`` answers "does this support a
  long?", so the primitives are used directly here instead: V3 trades both ways and a short
  needs the mirror of every test, not the negation of a long-biased summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import settings
from ..spec import SetupType, Trend
from .indicators import ema
from .setups import detect_setup
from .structure import (
    analyse_timeframe,
    classify_structure,
    nearest_resistance,
    nearest_support,
)

# Sub-weights for the carry score. Module constants rather than settings: these describe
# what "carry" means, whereas settings hold the numbers meant to be tuned against results.
W_SETUP = 0.30        # a real setup on the carry timeframe itself
W_ALIGNMENT = 0.20    # moving averages stacked with the trade
W_HEADROOM = 0.20     # room to the next opposing structure
W_VOLUME = 0.20       # contraction through the base, expansion on the move
W_LOCATION = 0.10     # where price sits in its own recent range

EMA_SPANS = (20, 50)

# Which conditions gate, and which only score.
#
# The first cut made all eight conditions gating, and measured across 2,527 replayed
# triggers it admitted **two**. A filter that admits 0.08% of candidates cannot be shown to
# help or hurt — it is unmeasurable, not strict. So the checklist keeps the three that state
# the requirement directly (a carry setup, fuel to move it, room to run) and the rest ride
# in the score, where a weak reading costs points instead of ending the evaluation.
#
# 60m EMA alignment in particular was binding on 51% of candidates on its own, and a sweep
# is by construction a stock that just took out a low — price is often under its 60m stack
# at the exact moment the reclaim triggers. Gating on it asked two contradictory things.
CORE_CONDITIONS = (
    "120m setup present",
    "volume contracted then expanded",
)
# Headroom is scored, not gated. Measured across 57 candidates it changed the pass rate by
# a single name — the conditions overlap, so weak candidates fail several at once — and it
# duplicates work `build_v3_plan` already does as a hard filter. It also repeated a mistake
# this codebase documents: `_plan_at_bar` lets only *weekly* structure reject because "daily
# swing highs are far denser — rejecting on those vetoes almost every setup", and 120m
# pivots are denser still.
# The shapes that count as "there is something to continue from" on the carry timeframe.
#
# This is `structure._base_quality`'s vocabulary, read through `analyse_timeframe`. The
# question is whether the higher timeframe sits in a structure that *can* continue — a base,
# a breakout, an orderly hold above the mean — which is far broader than a V3 entry signal.
#
# Testing this set against `detect_setup` was a bug: that function only ever returns
# RECLAIM, CONTINUATION, BASE_BREAKOUT or NONE, so four of the seven values were unreachable
# and the condition silently demanded a *second* independent V3 setup on the 120m. It bound
# 64% of candidates on its own, and was the main reason the gate admitted 2 of 2,527.
CARRY_STRUCTURES = {
    SetupType.BREAKOUT,
    SetupType.BREAKOUT_RETEST,
    SetupType.CONTINUATION,
    SetupType.FLAT_BASE,
    SetupType.ASCENDING_BASE,
}
MIN_BARS_60M = 60


@dataclass
class CarryState:
    """The verdict, and enough detail for the report to explain it."""

    passes: bool = False
    score: float = 0.0
    failed: str = ""                                  # first unmet checklist condition
    checks: dict[str, bool] = field(default_factory=dict)
    components: dict[str, float] = field(default_factory=dict)
    trend_60m: Trend = Trend.SIDEWAYS
    trend_120m: Trend = Trend.SIDEWAYS
    setup_120m: SetupType = SetupType.NONE
    note: str = ""

    @property
    def summary(self) -> str:
        if not self.passes:
            return self.failed or "no carry setup"
        return f"carry {self.score:.0f}/100 · {self.setup_120m.value} on 120m"


def gate_applies(setup: SetupType, gated_setups: str | None = None) -> bool:
    """May the carry gate reject this setup?

    The gate is a continuation-regime test, and not every V3 setup is a continuation trade.
    Measured over 2,527 triggers it improved base-breakout and continuation while cutting
    reclaim from +0.30R to -0.07R — see ``v3_carry_gated_setups`` for the numbers and the
    reason. Carry is still assessed and reported for ungated setups; it just cannot reject.
    """
    names = {
        part.strip().lower()
        for part in (
            gated_setups if gated_setups is not None else settings.v3_carry_gated_setups
        ).split(",")
        if part.strip()
    }
    return setup.value.lower() in names


def resample_120m(h60: pd.DataFrame) -> pd.DataFrame:
    """Fold 60m bars into 2-hour bars, anchored to each session's open.

    A clock resample is wrong here. NSE runs 09:15–15:30, so the feed returns seven 60m bars
    a session (09:15 … 15:15) and a ``120min`` rule anchored to midnight would cut buckets at
    10:00/12:00/14:00 — straddling the open and mixing two sessions into one bar. Folding by
    *position within the session* keeps every bucket inside the day that produced it, and
    leaves the odd last bar as its own short bucket rather than pairing it with tomorrow.
    """
    if h60 is None or h60.empty:
        return pd.DataFrame()

    frame = h60.copy()
    frame["_ts"] = frame.index
    sessions = frame.index.date
    pair = frame.groupby(sessions).cumcount() // 2

    agg = {"high": "max", "low": "min", "close": "last", "volume": "sum", "_ts": "first"}
    if "open" in frame:
        agg["open"] = "first"

    out = frame.groupby([sessions, pair], sort=True).agg(agg)
    out = out.set_index("_ts")
    out.index.name = h60.index.name
    return out[[c for c in ("open", "high", "low", "close", "volume") if c in out]]


def _aligned(frame: pd.DataFrame, direction: str, spans: tuple[int, ...] = EMA_SPANS) -> bool:
    """Is the EMA stack ordered with the trade, with price on the right side of it?

    ``structure._ema_state`` answers this for longs only; a short needs price *below* a
    stack that descends, which is not the same as "not aligned for a long".
    """
    close = frame["close"]
    usable = [s for s in spans if len(close) >= s]
    if not usable:
        return False
    values = [float(ema(close, s).iloc[-1]) for s in usable]
    price = float(close.iloc[-1])
    if direction == "long":
        stacked = all(values[i] > values[i + 1] for i in range(len(values) - 1))
        return price > max(values) and stacked
    stacked = all(values[i] < values[i + 1] for i in range(len(values) - 1))
    return price < min(values) and stacked


def _location(frame: pd.DataFrame, window: int = 60) -> float:
    """Where price sits inside its own recent range, 0 (low) to 100 (high)."""
    span = frame.tail(window)
    high, low = float(span["high"].max()), float(span["low"].min())
    if high <= low:
        return 50.0
    return float(np.clip((float(span["close"].iloc[-1]) - low) / (high - low) * 100, 0, 100))


def _volume_sequence(
    frame: pd.DataFrame, *, base_window: int = 12, expansion_window: int = 3
) -> tuple[float, str]:
    """Contraction through the base, then expansion on the move — scored 0-100.

    The sequence is the point, not either half alone. Volume that is merely high proves
    nothing; volume that dried up while price coiled and then multiplied on the break is the
    change of hands the setup is claiming.
    """
    if "volume" not in frame or len(frame) < base_window + expansion_window:
        return 0.0, "no volume data"

    volume = frame["volume"].astype(float)
    recent = volume.tail(expansion_window)
    base = volume.tail(base_window + expansion_window).head(base_window)
    base_mean = float(base.mean())
    if base_mean <= 0:
        return 0.0, "no volume baseline"

    expansion = float(recent.mean()) / base_mean
    half = max(base_window // 2, 1)
    early, late = float(base.head(half).mean()), float(base.tail(half).mean())
    contracted = late < early

    # Expansion carries most of the score; a genuine dry-up beforehand adds to it.
    score = float(np.clip(np.log10(max(expansion, 0.01)) * 120 + 40, 0, 100)) * 0.75
    if contracted:
        score += 25
    score = float(np.clip(score, 0, 100))
    return score, (
        f"{expansion:.1f}x recent vs base volume"
        + (", base dried up first" if contracted else ", no prior contraction")
    )


def _headroom(frame: pd.DataFrame, direction: str, required_pct: float) -> tuple[float, str]:
    """Distance to the next opposing structure, against the move the target needs."""
    price = float(frame["close"].iloc[-1])
    level = (
        nearest_resistance(frame, price)
        if direction == "long"
        else nearest_support(frame, price)
    )
    if level is None:
        return 100.0, "no opposing structure on the carry timeframe"
    room_pct = abs(level - price) / price * 100
    if required_pct <= 0:
        return 100.0, f"next structure {room_pct:.1f}% away"
    ratio = room_pct / required_pct
    return float(np.clip(ratio * 60, 0, 100)), (
        f"next structure {room_pct:.1f}% away against {required_pct:.1f}% needed"
    )


def assess_carry(
    h60: pd.DataFrame | None,
    *,
    direction: str = "long",
    required_pct: float = 0.0,
    floor: float = 60.0,
    min_volume_score: float = 40.0,
    min_headroom_score: float = 40.0,
) -> CarryState:
    """Does this name carry? Checklist first, then the score; both must pass.

    ``required_pct`` is the move the 4R target needs, used to judge headroom. It is optional
    so the carry test can run before a plan exists.

    Volume and headroom gate as well as score, on purpose. A first run of this gate passed
    PIIND short at 66/100 with volume 26 and headroom 24 — no expansion at all, and the
    nearest support 0.8% away against a target needing 2.0% — because alignment and range
    position were perfect. A weighted average will always let two strong components carry a
    fatal one, so the two that describe fuel and room are floors. See ``CORE_CONDITIONS``
    for why the other five only score.
    """
    if h60 is None or len(h60) < MIN_BARS_60M:
        # Fail closed. Unproven is not the same as fine.
        return CarryState(
            failed="no 60m data — carry regime unproven",
            note="the higher-timeframe read could not be fetched",
        )

    h120 = resample_120m(h60)
    if len(h120) < 25:
        return CarryState(failed="not enough 120m history to judge carry")

    _s60, trend60 = classify_structure(h60)
    _s120, trend120 = classify_structure(h120)
    against = Trend.DOWN if direction == "long" else Trend.UP

    # Two different questions, deliberately kept apart:
    #   structure_120 — is the 120m in a shape that can continue?   (gates)
    #   setup_120     — how clean is the best V3 setup there?       (scores)
    structure_120 = analyse_timeframe(h120, "120m", ema_spans=EMA_SPANS)
    setup_120 = detect_setup(h120, direction=direction)
    location = _location(h120)
    aligned_60, aligned_120 = _aligned(h60, direction), _aligned(h120, direction)
    volume_score, volume_note = _volume_sequence(h60)
    headroom_score, headroom_note = _headroom(h120, direction, required_pct)

    checks = {
        "60m EMA aligned": aligned_60,
        "120m EMA aligned": aligned_120,
        "60m trend not opposing": trend60 is not against,
        "120m trend not opposing": trend120 is not against,
        "120m setup present": structure_120.setup in CARRY_STRUCTURES,
        "in the right half of the 120m range": (
            location > 50 if direction == "long" else location < 50
        ),
        "volume contracted then expanded": volume_score >= min_volume_score,
        "room before the next opposing level": headroom_score >= min_headroom_score,
    }
    # Only the core conditions can reject. The rest are reported and scored.
    failed = next((name for name in CORE_CONDITIONS if not checks[name]), "")

    alignment_score = 100.0 * (aligned_60 + aligned_120) / 2
    location_score = location if direction == "long" else 100 - location

    components = {
        "120m setup": round(setup_120.quality, 1),
        "ma alignment": round(alignment_score, 1),
        "headroom": round(headroom_score, 1),
        "volume sequence": round(volume_score, 1),
        "range location": round(location_score, 1),
    }
    score = (
        W_SETUP * setup_120.quality
        + W_ALIGNMENT * alignment_score
        + W_HEADROOM * headroom_score
        + W_VOLUME * volume_score
        + W_LOCATION * location_score
    )

    if not failed and score < floor:
        failed = f"carry score {score:.0f} below {floor:.0f}"

    return CarryState(
        passes=not failed,
        score=round(score, 1),
        failed=failed,
        checks=checks,
        components=components,
        trend_60m=trend60,
        trend_120m=trend120,
        setup_120m=setup_120.kind,
        note=f"{volume_note}; {headroom_note}",
    )
