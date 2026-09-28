"""Console surface for the timeframe study.

Its own module for the same reason every other strategy has one: this study derives its own
cost constants, and a renderer shared with a strategy report is one refactor away from a
shared constant.

The ordering of the output is deliberate and is the argument. Persistence comes first,
because it is a property of the market that no rule of mine can flatter. The rule replay
comes second, because it is the persistence figure after it has been made to pay for itself.
Where the two disagree — a timeframe that persists but loses money — the disagreement *is*
the finding, and it is always the same finding: cost in R is (cost% / stop%).
"""

from __future__ import annotations

import numpy as np
from rich.console import Group
from rich.table import Table
from rich.text import Text

CAVEAT = (
    "Intraday rows come from ~60 sessions — one market period — while the daily and weekly "
    "rows span years. A ranking across them compares a noisy estimate against a stable one, "
    "so read the intraday rows as 'this window' rather than 'this timeframe'."
)


def _fmt(value: float, places: int = 3, signed: bool = False) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:+.{places}f}" if signed else f"{value:.{places}f}"


def render_persistence(rows, title: str) -> Table:
    table = Table(title=title, header_style="bold", box=None, padding=(0, 2))
    for column, justify in (
        ("Timeframe", "left"), ("Bars", "right"), ("Median bar range", "right"),
        ("VR(2)", "right"), ("robust z", "right"), ("VR(4)", "right"),
        ("AC(1)", "right"), ("% VR>1", "right"), ("Verdict", "left"),
    ):
        table.add_column(column, justify=justify)

    for row in rows:
        colour = (
            "green" if row.verdict.startswith("persistent")
            else "red" if row.verdict.startswith("mean-reverting")
            else "yellow"
        )
        table.add_row(
            row.timeframe,
            f"{row.bars:,}",
            f"{row.median_bar_range_pct:.2f}%"
            if np.isfinite(row.median_bar_range_pct) else "—",
            _fmt(row.variance_ratio),
            _fmt(row.vr_z, 2, signed=True),
            _fmt(row.vr_4),
            _fmt(row.autocorr_1, 4, signed=True),
            f"{row.share_trending:.0f}%" if np.isfinite(row.share_trending) else "—",
            f"[{colour}]{row.verdict}[/]",
        )
    return table


def render_rule(rows, study) -> Table:
    table = Table(
        title=f"\nOne continuation rule at every bar size — "
              f"EMA 5/9/13/21 stack above the 200, "
              f"{study.atr_stop_mult:.0f}xATR stop, {study.reward_risk:.0f}R target",
        header_style="bold", box=None, padding=(0, 2),
    )
    for column, justify in (
        ("Timeframe", "left"), ("Trades", "right"), ("Win%", "right"),
        ("Median stop", "right"), ("Cost in R", "right"), ("Gross R", "right"),
        ("Net R", "right"), ("Random entry", "right"), ("Excess", "right"),
        ("t", "right"), ("What the signal adds", "left"),
    ):
        table.add_column(column, justify=justify)

    for row in rows:
        colour = "green" if np.isfinite(row.net_r) and row.net_r > 0 else "red"
        verdict = row.signal_verdict
        vcolour = (
            "green" if verdict.startswith("beats")
            else "red" if verdict.startswith("WORSE")
            else "yellow"
        )
        table.add_row(
            row.timeframe,
            f"{row.trades:,}",
            f"{row.win_rate:.1f}%" if np.isfinite(row.win_rate) else "—",
            f"{row.median_stop_pct:.2f}%" if np.isfinite(row.median_stop_pct) else "—",
            f"-{row.cost_r:.3f}" if np.isfinite(row.cost_r) else "—",
            _fmt(row.gross_r, 3, signed=True),
            f"[{colour}]{_fmt(row.net_r, 3, signed=True)}[/]",
            _fmt(row.control_gross_r, 3, signed=True),
            _fmt(row.excess_r, 3, signed=True),
            _fmt(row.excess_t, 2, signed=True),
            f"[{vcolour}]{verdict}[/]",
        )
    return table


def render_study(study) -> Group:
    parts: list = [
        Text.from_markup(
            "[bold]Timeframe study — where continuation actually persists on NSE[/]\n"
            f"[dim]{len(study.symbols)} liquid NIFTY 500 names · variance ratios, "
            "autocorrelation, and one unchanged rule replayed at every bar size[/]\n"
        )
    ]
    parts.append(
        render_persistence(study.persistence, "\nWhat the market does — cross-section of stocks")
    )
    parts.append(
        Text.from_markup(
            "[dim]VR(2) above 1 means a two-bar move is larger than two one-bar moves — "
            "returns extend, and a continuation entry has something to hold. Below 1 they "
            "revert. The z is Lo-MacKinlay heteroskedasticity-robust, which matters "
            "enormously intraday: NSE returns cluster violently around the open, and the "
            "homoskedastic statistic reads that clustering as trend. |z| < 1.96 means the "
            "data cannot tell that bar size apart from a random walk — the most common and "
            "most honest answer.[/]"
        )
    )

    if study.index_persistence:
        parts.append(render_persistence(study.index_persistence, "\nNIFTY itself (^NSEI)"))
        parts.append(
            Text.from_markup(
                "[dim]Shown separately because the index and its constituents can disagree: "
                "idiosyncratic mean reversion in single names coexists with persistence in "
                "the aggregate, and anyone trading the index needs the second number.[/]"
            )
        )

    parts.append(render_rule(study.rule, study))
    parts.append(
        Text.from_markup(
            "[dim]The same rule everywhere, with no per-timeframe tuning — a rule optimised "
            "separately at each bar size would measure the optimiser. Intraday rows pay "
            "0.07% round trip (0.025% STT, sell side only, plus slippage); daily and weekly "
            "pay 0.17% delivery. Charging the delivery figure to an intraday strategy "
            "overstates cost 3.4x and is the error this codebase had to retract a published "
            "number over.[/]"
        )
    )
    parts.append(
        Text.from_markup(
            "\n[bold]Read the Excess column, not the Net R column.[/]\n"
            "[dim]'Random entry' is the identical geometry — same stop, same target, same "
            "holding cap — entered on randomly chosen bars of the same frames. Whatever it "
            "earns is drift: a long with a wide stop, held through a market that rose, makes "
            "money for reasons that have nothing to do with the signal, and the longer the "
            "bar the more of that it collects. Only the excess is about the rule.[/]"
        )
    )

    beats = [r for r in study.rule if r.signal_verdict.startswith("beats")]
    worse = [r for r in study.rule if r.signal_verdict.startswith("WORSE")]
    if not beats:
        parts.append(
            Text.from_markup(
                "\n[yellow]No timeframe's signal beats a random entry at the 95% level.[/] "
                "Whatever gradient the Net R column shows across bar sizes is drift capture "
                "and cost, not a sharper signal at longer horizons. That is a real finding "
                "about this rule, and it is the one that should govern how the Net R column "
                "is quoted."
            )
        )
    for row in beats:
        parts.append(
            Text.from_markup(
                f"\n[green]{row.timeframe}[/] beats a random entry by "
                f"{row.excess_r:+.3f}R (t = {row.excess_t:+.2f}) over {row.trades:,} trades."
            )
        )
    for row in worse:
        parts.append(
            Text.from_markup(
                f"\n[red]{row.timeframe} is worse than entering at random[/] by "
                f"{row.excess_r:+.3f}R (t = {row.excess_t:+.2f}). A continuation entry at "
                "this bar size is actively harmful, which is what a variance ratio below 1 "
                "means in practice."
            )
        )

    decisive = [r for r in study.rule if r.decisive]
    for row in decisive:
        direction = "positive" if row.net_r > 0 else "negative"
        parts.append(
            Text.from_markup(
                f"[dim]{row.timeframe} net expectancy is decisively {direction} "
                f"({row.net_r:+.3f}R, CI {row.ci_low:+.2f} to {row.ci_high:+.2f}, "
                f"{row.trades:,} trades) — before subtracting the random-entry control.[/]"
            )
        )

    if study.fetch_failed:
        parts.append(
            Text.from_markup(
                f"\n[yellow]{len(study.fetch_failed)} symbol/timeframe fetches failed:[/] "
                + ", ".join(study.fetch_failed[:12])
                + (" …" if len(study.fetch_failed) > 12 else "")
            )
        )
    parts.append(Text.from_markup(f"\n[yellow]{CAVEAT}[/]"))
    parts.append(
        Text.from_markup(
            "[dim]A bar touching both stop and target books a loss. Unresolved trades are "
            "marked out at the last bar seen rather than dropped — dropping them keeps only "
            "the ones that moved, which is a survivorship filter on outcomes. Decision "
            "support only; nothing here places an order.[/]"
        )
    )
    return Group(*parts)
