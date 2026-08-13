"""The dated Markdown daily brief — the system's primary output.

Ordered the way the decision is actually made: regime first (should I be aggressive at
all?), then the shortlist with the reason each name is there, then the trade plans with
their invalidation. A name only reaches the plan table if it cleared the R:R gate.
"""

from __future__ import annotations

from datetime import date

from loguru import logger

from ..config import BRIEF_DIR
from ..data import MarketData
from ..models import ScanResult


def _regime_section(result: ScanResult) -> list[str]:
    regime = result.regime
    lines = [
        "## 1. Market regime",
        "",
        f"### {regime.verdict.emoji} {regime.verdict.headline}",
        "",
        f"Composite score **{regime.total:+d}** (range ±5).",
        "",
        "| Input | Score | Detail |",
        "| --- | :---: | --- |",
    ]
    for component in regime.components:
        lines.append(f"| {component.name} | {component.score:+d} | {component.detail} |")

    if regime.net_gex is not None:
        lines += [
            "",
            f"Net dealer gamma **{regime.net_gex / 1e9:,.1f}bn**"
            + (f", flip level **{regime.gamma_flip:,.0f}**" if regime.gamma_flip else "")
            + (f", spot {regime.spot:,.0f}" if regime.spot else "")
            + ".",
            "",
            "> Gamma is regime *context*, never an entry signal.",
        ]
    return lines


def _shortlist_section(result: ScanResult) -> list[str]:
    if not result.candidates:
        return [
            "## 2. Shortlist",
            "",
            "**No candidate cleared the R:R gate today.**",
            "",
            "This is a valid outcome. The gate exists to say no — a day with no 2R setup is "
            "a day to not trade.",
        ]

    lines = [
        "## 2. Shortlist",
        "",
        "Factor scores are cross-sectional percentiles (0–100) across the liquid universe.",
        "",
        "| # | Symbol | Sector | Score | RS | Vol | Struct | Catalyst | Liq |",
        "| --: | --- | --- | --: | --: | --: | --: | --: | --: |",
    ]
    for i, cand in enumerate(result.candidates, 1):
        f = cand.factors
        lines.append(
            f"| {i} | **{cand.symbol}** | {cand.sector} | {cand.total_score:.1f} | "
            f"{f.relative_strength:.0f} | {f.volume:.0f} | {f.price_structure:.0f} | "
            f"{f.catalyst:.0f} | {f.liquidity:.0f} |"
        )
    return lines


def _why_section(result: ScanResult) -> list[str]:
    rows = [
        c for c in result.candidates if c.catalyst_note or (c.macro.reliable and c.macro.gap_pct)
    ]
    if not rows:
        return []

    lines = ["## 3. Why — catalyst and macro", ""]
    for cand in rows:
        lines.append(f"**{cand.symbol}** — {cand.company}")
        if cand.catalyst_note:
            lines.append(f"- Catalyst: {cand.catalyst_note}")
        if cand.macro.reliable and cand.macro.gap_pct is not None:
            direction = "above" if cand.macro.gap_pct > 0 else "below"
            drivers = ", ".join(f"{n} {b:+.3f}" for n, b in cand.macro.top_drivers)
            lines.append(
                f"- Macro: trading {abs(cand.macro.gap_pct):.1f}% {direction} macro-implied "
                f"fair value ₹{cand.macro.fair_value:,.2f} "
                f"(R²={cand.macro.r_squared:.2f}; drivers: {drivers})"
            )
        lines.append("")
    return lines


def _plan_section(result: ScanResult) -> list[str]:
    planned = [c for c in result.candidates if c.plan]
    if not planned:
        return []

    lines = [
        "## 4. Trade plans",
        "",
        "Every plan below clears the minimum R:R gate. Position size assumes the configured "
        "risk budget per trade.",
        "",
        "| Symbol | Entry | Stop | Target | Risk | Reward | R:R | Qty | Invalidation |",
        "| --- | --: | --: | --: | --: | --: | --: | --: | --- |",
    ]
    for cand in planned:
        plan = cand.plan
        risk = plan.entry - plan.stop
        reward = plan.target - plan.entry
        lines.append(
            f"| **{cand.symbol}** | {plan.entry:,.2f} | {plan.stop:,.2f} | {plan.target:,.2f} | "
            f"₹{risk:,.2f} | ₹{reward:,.2f} | **1:{plan.reward_risk:.1f}** | {plan.quantity} | "
            f"{plan.invalidation} |"
        )

    lines += ["", "### Setup detail", ""]
    for cand in planned:
        lines.append(f"- **{cand.symbol}**: {cand.plan.setup}")

    flagged = [c for c in planned if c.earnings_flag]
    if flagged:
        lines += ["", "### Earnings proximity", ""]
        for cand in flagged:
            lines.append(f"- **{cand.symbol}**: {cand.earnings_flag}")
    return lines


def _watchlist_section(result: ScanResult) -> list[str]:
    if not result.watchlist:
        return []
    lines = [
        "## 5. Watchlist — strong stock, insufficient asymmetry",
        "",
        "These ranked well but their setup does not currently offer enough reward for the "
        "risk. They are not trades today.",
        "",
        "| Symbol | Score | Why rejected |",
        "| --- | --: | --- |",
    ]
    for cand in result.watchlist:
        lines.append(f"| {cand.symbol} | {cand.total_score:.1f} | {cand.rejected_reason} |")
    return lines


def build_markdown(result: ScanResult) -> str:
    lines = [
        f"# Asymmetry brief — {result.as_of:%A, %d %B %Y}",
        "",
        f"*Data tier: {result.tier} · universe: {result.liquid_size}/{result.universe_size} "
        f"passed the liquidity gate*",
        "",
        "> Decision support only. This system never places orders — you place every order "
        "yourself, and every level below is a starting point for your own judgement.",
        "",
        "---",
        "",
    ]
    lines += _regime_section(result)
    lines += ["", "---", ""]
    lines += _shortlist_section(result)

    why = _why_section(result)
    if why:
        lines += ["", "---", ""] + why

    plans = _plan_section(result)
    if plans:
        lines += ["", "---", ""] + plans

    watch = _watchlist_section(result)
    if watch:
        lines += ["", "---", ""] + watch

    lines += [
        "",
        "---",
        "",
        "### How to read this",
        "",
        "1. **Regime** decides how aggressive to be at all. A great setup in a red regime is "
        "still a marginal trade.",
        "2. **Catalyst** answers why the stock should move — whether the news changes forward "
        "earnings expectations, not whether it sounds positive.",
        "3. **Score** ranks likelihood of movement; it is not a prediction of direction on its own.",
        "4. **Macro gap** only counts when the model is reliable (R² above the configured floor).",
        "5. **R:R** is the gate. No plan below the minimum reaches this brief.",
        "",
    ]
    return "\n".join(lines)


def run_pipeline(
    as_of: date | None = None, *, top_n: int = 10, refresh_catalysts: bool = True
) -> ScanResult:
    """All five engines, in order. Shared by the Markdown and HTML outputs."""
    from ..engines.selection import (
        attach_earnings,
        attach_intraday,
        attach_macro,
        run_selection,
    )

    data = MarketData()
    result = run_selection(
        as_of, top_n=top_n, refresh_catalysts=refresh_catalysts, data=data
    )
    result = attach_macro(result)
    result = attach_earnings(result)
    result = attach_intraday(result, data)
    result.tier = data.session_tier.label
    return result


def generate_brief(
    as_of: date | None = None,
    *,
    top_n: int = 10,
    html: bool = False,
    refresh_catalysts: bool = True,
) -> list[str]:
    """Write the brief. Returns every path written."""
    from .website import export_html

    result = run_pipeline(as_of, top_n=top_n, refresh_catalysts=refresh_catalysts)

    # Record every call as it is made, so the journal exists whether or not you act on it.
    # Recording only what you traded would bias the record toward decisions you already
    # liked, which is exactly the comparison the journal is meant to make possible.
    from ..journal import record_brief

    record_brief(result)

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    markdown_path = BRIEF_DIR / f"{result.as_of:%Y-%m-%d}.md"
    markdown_path.write_text(build_markdown(result), encoding="utf-8")
    logger.info(f"[brief] wrote {markdown_path}")
    written = [str(markdown_path)]

    if html:
        written.append(export_html(result))
        logger.info(f"[brief] wrote {written[-1]}")
    return written
