"""Console and Markdown surfaces for the intraday pullback scanner.

Kept separate from `v3_report` for the same reason the engine is separate: the two
strategies have different geometry (3R vs 4R, 0.7% vs 0.5-1.5%, intraday vs 1-5 sessions)
and sharing a renderer would eventually mean sharing a constant.

The line builders are shared *within* this strategy, so the console and the Markdown brief
cannot describe one trade two ways — the same rule V3 follows.
"""

from __future__ import annotations

import numpy as np
from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..config import BRIEF_DIR, settings
from ..engines.hma_pullback import PullbackSettings, PullbackSignal


def execution_lines(signal: PullbackSignal, cfg: PullbackSettings) -> list[tuple[str, str]]:
    """How to act on one signal, including what invalidates it."""
    reward = abs(signal.target - signal.entry)
    cost_r = (settings.cost_roundtrip_pct + settings.slippage_pct) / max(signal.risk_pct, 1e-9)
    return [
        (
            "Entry",
            f"Buy at {signal.entry:,.2f} — the close of the {cfg.entry_interval} candle that "
            "pulled back into the middle band and held it.",
        ),
        (
            "Stop",
            f"{signal.stop:,.2f}, the low of that same candle — {signal.risk_pct:.2f}% away, "
            f"inside the {cfg.max_risk_pct:.1f}% cap.",
        ),
        (
            "Target",
            f"{signal.target:,.2f} — {cfg.reward_risk:.0f}R, {reward:,.2f} against "
            f"{signal.risk:,.2f} risked.",
        ),
        (
            "Cost drag",
            f"~{cost_r:.2f}R of this trade's risk goes on round-trip costs, because "
            f"{settings.cost_roundtrip_pct + settings.slippage_pct:.2f}% of friction against a "
            f"{signal.risk_pct:.2f}% stop is proportionally large. Solving "
            f"p(R-c) = (1-p)(1+c) for the break-even hit rate, that moves it from "
            f"{100 / (cfg.reward_risk + 1):.0f}% to "
            f"{100 * (1 + cost_r) / (cfg.reward_risk + 1):.0f}% on this trade.",
        ),
        (
            "Exit by",
            "the close — this is an intraday setup anchored to a pre-13:00 candle and the "
            "spec carries no overnight rule, so an unresolved position is squared off."
            if cfg.square_off_at_close
            else "stop or target only; no time exit configured.",
        ),
    ]


def render_scan(signals: list[PullbackSignal], as_of: str, cfg: PullbackSettings) -> Group:
    parts: list = [
        Text.from_markup(
            f"[bold]Intraday HMA pullback — {as_of}[/]\n"
            f"[dim]NIFTY 200 · long only · {cfg.reward_risk:.0f}R · risk cap "
            f"{cfg.max_risk_pct:.1f}% · {cfg.anchor_interval} anchor before "
            f"{cfg.latest_anchor:%H:%M} → {cfg.entry_interval} entry[/]\n"
        )
    ]

    if not signals:
        parts.append(
            Text.from_markup(
                "\n[yellow]Nothing qualified.[/]\n"
                "[dim]The anchor requires a green 30m candle with a "
                f"{cfg.min_body_pct:.0f}% body before {cfg.latest_anchor:%H:%M}, with HMA"
                f"{cfg.hma_period} rising into the middle band. Most sessions produce none "
                "for most names.[/]"
            )
        )
        return Group(*parts)

    table = Table(title=f"\nQualifying setups — {len(signals)}", header_style="bold")
    for column, justify in (
        ("Symbol", "left"), ("Anchor", "left"), ("Body", "right"), ("HMA vs band", "right"),
        ("Entry time", "left"), ("Entry", "right"), ("Stop", "right"), ("Risk%", "right"),
        ("Target", "right"),
    ):
        table.add_column(column, justify=justify)

    for signal in signals:
        table.add_row(
            f"[bold]{signal.symbol}[/]",
            f"{signal.anchor_at:%H:%M}" if signal.anchor_at is not None else "—",
            f"{signal.anchor_body_pct:.0f}%",
            f"{signal.anchor_hma_gap_pct:+.2f}%",
            f"{signal.entry_at:%H:%M}" if signal.entry_at is not None else "—",
            f"{signal.entry:,.2f}",
            f"{signal.stop:,.2f}",
            f"{signal.risk_pct:.2f}%",
            f"{signal.target:,.2f}",
        )
    parts.append(table)

    for signal in signals:
        parts.append(
            Text.from_markup(f"\n[bold]{signal.symbol}[/]\n  [dim]Why:[/] {signal.note}")
            .append_text(
                Text.from_markup(
                    "".join(
                        f"\n  [dim]{label}:[/] {value}"
                        for label, value in execution_lines(signal, cfg)
                    )
                )
            )
        )

    parts.append(
        Text.from_markup(
            "\n[dim]Decision support only — setups for your judgement, never instructions. "
            "The system places no orders.[/]\n"
            "[yellow]This strategy is separately measured and its edge is not established. "
            "See docs/spec-hma-pullback.md before trading it.[/]"
        )
    )
    return Group(*parts)


def build_markdown(signals: list[PullbackSignal], as_of: str, cfg: PullbackSettings) -> str:
    lines = [
        f"# Intraday HMA pullback — {as_of}",
        "",
        f"*NIFTY 200 · long only · {cfg.reward_risk:.0f}R · risk cap {cfg.max_risk_pct:.1f}% · "
        f"{cfg.anchor_interval} anchor before {cfg.latest_anchor:%H:%M} → "
        f"{cfg.entry_interval} entry*",
        "",
        "> Decision support only. Every row is a setup for your own judgement, never an "
        "instruction to buy or sell. The system places no orders.",
        "",
    ]
    if not signals:
        lines += ["**Nothing qualified today.**", ""]
        return "\n".join(lines)

    lines += [
        "| Symbol | Anchor | Body | HMA vs band | Entry time | Entry | Stop | Risk% | Target |",
        "| --- | --- | --: | --: | --- | --: | --: | --: | --: |",
    ]
    for s in signals:
        lines.append(
            f"| **{s.symbol}** | {s.anchor_at:%H:%M} | {s.anchor_body_pct:.0f}% | "
            f"{s.anchor_hma_gap_pct:+.2f}% | {s.entry_at:%H:%M} | {s.entry:,.2f} | "
            f"{s.stop:,.2f} | {s.risk_pct:.2f}% | {s.target:,.2f} |"
        )
    lines.append("")

    for s in signals:
        lines += [f"### {s.symbol}", "", f"- **Why:** {s.note}"]
        lines += [f"- **{label}:** {value}" for label, value in execution_lines(s, cfg)]
        lines.append("")
    return "\n".join(lines)


def write_brief(signals: list[PullbackSignal], as_of: str, cfg: PullbackSettings) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{as_of}-pullback.md"
    path.write_text(build_markdown(signals, as_of, cfg), encoding="utf-8")
    return str(path)


def render_backtest(result, cfg: PullbackSettings) -> Group:
    """Measured performance, reported with costs separated rather than folded in."""
    if not result.trades:
        return Group(
            Text.from_markup(
                "[red]No trades generated.[/] Either the anchor never fired in the available "
                "history, or no pullback held the band inside the risk cap."
            )
        )

    breakeven = 100.0 / (cfg.reward_risk + 1.0)
    win = result.win_rate
    verdict = (
        "[green]above break-even on this sample[/]"
        if win > breakeven + 3
        else "[yellow]around break-even — not distinguishable from chance here[/]"
        if win > breakeven - 3
        else "[red]below break-even — this geometry loses at the measured hit rate[/]"
    )

    summary = Table(title="Intraday HMA pullback — backtest", header_style="bold", box=None,
                    padding=(0, 2))
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Trades", f"{len(result.trades):,}")
    summary.add_row("Symbols / sessions", f"{result.symbols_tested} / {result.sessions_spanned}")
    summary.add_row("Anchors found", f"{result.anchors_found:,}")
    summary.add_row("Anchors with no valid entry", f"{result.entries_missed:,}")
    summary.add_row("Resolved (stop or target)", f"{len(result.resolved):,}")
    summary.add_row("[bold]Win rate[/]", f"[bold]{win:.1f}%[/]")
    summary.add_row(f"Break-even at {cfg.reward_risk:.0f}R (before costs)", f"{breakeven:.1f}%")
    summary.add_row("Expectancy per trade", f"{result.expectancy_r:+.3f}R")
    summary.add_row("Mean cost per trade", f"-{np.mean([t.cost_r for t in result.trades]):.3f}R")
    summary.add_row("[bold]Net expectancy[/]", f"[bold]{result.net_expectancy_r:+.3f}R[/]")
    summary.add_row("Total", f"{result.total_r:+.1f}R")

    parts: list = [summary, Text.from_markup(f"\n{verdict}\n")]

    outcomes = Table(title="\nBy outcome", header_style="bold", box=None, padding=(0, 2))
    outcomes.add_column("Outcome")
    outcomes.add_column("n", justify="right")
    outcomes.add_column("Mean R", justify="right")
    for name in ("target", "stop", "square-off"):
        subset = [t for t in result.trades if t.outcome == name]
        if not subset:
            continue
        outcomes.add_row(
            name, str(len(subset)), f"{np.mean([t.realised_r for t in subset]):+.2f}"
        )
    parts.append(outcomes)

    parts.append(
        Text.from_markup(
            "\n[dim]A bar touching both stop and target books a loss, since the order of "
            "events inside a bar is unknown. Costs are charged per trade from its own stop "
            "distance — at a 0.7% stop, 0.17% of friction is roughly a quarter of 1R. "
            "Intraday history reaches ~58 sessions, so this is one market period.[/]"
        )
    )
    return Group(*parts)
