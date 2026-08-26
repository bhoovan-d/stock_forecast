"""Console and Markdown surfaces for the kill-zone trident scanner.

Separate from `v3_report` and `pullback_report` for the same reason the engine is separate:
three strategies with different geometry (4R / 3R / 20R, three different stop rules, three
different holding periods) and a shared renderer eventually means a shared constant.

The line builders are shared *within* this strategy, so the console and the Markdown brief
cannot describe one trade two ways — the rule V3 follows.
"""

from __future__ import annotations

import numpy as np
from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..config import BRIEF_DIR
from ..engines.trident import TridentSettings, TridentSignal

# Every surface repeats this. The strategy is transcribed from a YouTube interview whose
# headline claim is a 90% win rate at 1:20, and nothing in this repository has tested that
# claim at a sample size capable of confirming or refuting it.
UNPROVEN = (
    "Transcribed from a source claiming a 90% win rate at 1:20. That claim is untested "
    "here and cannot be tested on the ~60 sessions of 30-minute history available. Read "
    "docs/spec-trident.md before risking anything on it."
)


def execution_lines(signal: TridentSignal, cfg: TridentSettings) -> list[tuple[str, str]]:
    """How to act on one signal, including what invalidates it and what it needs to work."""
    cost_r = (cfg.cost_roundtrip_pct + cfg.slippage_pct) / max(signal.risk_pct, 1e-9)
    breakeven = 100 / (cfg.reward_risk + 1)
    breakeven_net = 100 * (1 + cost_r) / (cfg.reward_risk + 1)
    return [
        (
            "Entry",
            f"Buy at {signal.entry:,.2f} — the close of the {signal.entry_at:%H:%M} candle, "
            f"which confirmed below the doji high at {signal.doji_high:,.2f}.",
        ),
        (
            "Stop",
            f"{signal.stop:,.2f}, the low of the {signal.doji_at:%H:%M} doji — "
            f"{signal.risk_pct:.2f}% away. Never widened: the 1:{cfg.reward_risk:.0f} "
            "geometry exists only while entry sits on top of its own invalidation, and a "
            "stop moved to survive a wobble turns a bounded loss into an unbounded one.",
        ),
        (
            "Target",
            f"{signal.target:,.2f} — {cfg.reward_risk:.0f}R, which is a "
            f"{signal.required_move_pct:.1f}% move from here.",
        ),
        (
            "Is that reachable",
            (
                f"ATR says roughly {signal.capacity_pct:.1f}% of travel is available over "
                f"{cfg.max_hold_sessions} sessions, so {signal.required_move_pct:.1f}% is "
                + ("**within** reach." if signal.feasible else "**beyond** it.")
                + " This is reported, not enforced — the source specifies no feasibility "
                "test, and adding one would measure a rule he does not have."
            )
            if signal.capacity_pct
            else "not computable — insufficient daily history for an ATR.",
        ),
        (
            "Cost drag",
            f"~{cost_r:.2f}R of this trade's risk goes on delivery-rate round-trip costs "
            f"({cfg.cost_roundtrip_pct + cfg.slippage_pct:.2f}% against a "
            f"{signal.risk_pct:.2f}% stop). Break-even moves from {breakeven:.1f}% to "
            f"{breakeven_net:.1f}%. Delivery rates, not intraday: a "
            f"{signal.required_move_pct:.1f}% move is not a one-session trade.",
        ),
        (
            "Time",
            f"No exit rule in the source beyond the target — he rides the trend until the "
            f"EMAs cross. Here the position is marked out after {cfg.max_hold_sessions} "
            "sessions if neither level is reached, and that outcome is counted separately "
            "from wins and losses.",
        ),
    ]


def render_scan(signals: list[TridentSignal], as_of: str, cfg: TridentSettings) -> Group:
    parts: list = [
        Text.from_markup(
            f"[bold]Kill-zone trident — {as_of}[/]\n"
            f"[dim]NIFTY 200 · long only · {cfg.reward_risk:.0f}R · "
            f"{cfg.anchor_interval} inside {cfg.killzone_start:%H:%M}–"
            f"{cfg.killzone_end:%H:%M} · daily {cfg.daily_bias_ema} EMA bias[/]\n"
        )
    ]

    if not signals:
        parts.append(
            Text.from_markup(
                "\n[yellow]Nothing qualified.[/]\n"
                "[dim]That is the expected state. The source's own estimate is 8–10 setups "
                "a year per instrument; whole weeks producing none is the design, not a "
                "failure.[/]"
            )
        )
        parts.append(Text.from_markup(f"\n[yellow]{UNPROVEN}[/]"))
        return Group(*parts)

    table = Table(title=f"\nQualifying setups — {len(signals)}", header_style="bold")
    for column, justify in (
        # Eight columns, not twelve. A squashed table is worse than a smaller one, and the
        # gap, doji and body detail already appears verbatim in each row's "Why" line
        # below. The Markdown brief, read in a browser, keeps the full set.
        ("Symbol", "left"), ("Entry at", "left"), ("Entry", "right"), ("Stop", "right"),
        ("Risk%", "right"), ("Target", "right"), ("Needs", "right"), ("Reachable", "left"),
    ):
        table.add_column(column, justify=justify)

    for s in signals:
        table.add_row(
            f"[bold]{s.symbol}[/]",
            f"{s.entry_at:%H:%M}" + ("*" if s.prime_time else ""),
            f"{s.entry:,.2f}",
            f"{s.stop:,.2f}",
            f"{s.risk_pct:.2f}%",
            f"{s.target:,.2f}",
            f"{s.required_move_pct:.1f}%",
            "[green]yes[/]" if s.feasible else "[red]no[/]",
        )
    parts.append(table)
    parts.append(
        Text.from_markup(
            "[dim]* the gap behind this setup printed in the first hour of the window — "
            "the source's highest-probability slot. Recorded, never gated. Gap, doji and "
            "body detail for each row is in its Why line below.[/]"
        )
    )

    for s in signals:
        parts.append(
            Text.from_markup(f"\n[bold]{s.symbol}[/]\n  [dim]Why:[/] {s.note}").append_text(
                Text.from_markup(
                    "".join(
                        f"\n  [dim]{label}:[/] {value}"
                        for label, value in execution_lines(s, cfg)
                    )
                )
            )
        )

    parts.append(
        Text.from_markup(
            "\n[dim]Decision support only — setups for your judgement, never instructions. "
            "The system places no orders.[/]\n"
            f"[yellow]{UNPROVEN}[/]"
        )
    )
    return Group(*parts)


def build_markdown(signals: list[TridentSignal], as_of: str, cfg: TridentSettings) -> str:
    lines = [
        f"# Kill-zone trident — {as_of}",
        "",
        f"*NIFTY 200 · long only · {cfg.reward_risk:.0f}R · {cfg.anchor_interval} inside "
        f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M} · daily "
        f"{cfg.daily_bias_ema} EMA bias*",
        "",
        "> Decision support only. Every row is a setup for your own judgement, never an "
        "instruction to buy or sell. The system places no orders.",
        "",
        f"> {UNPROVEN}",
        "",
    ]
    if not signals:
        lines += [
            "**Nothing qualified today.** The source's own estimate is 8–10 setups a year "
            "per instrument, so an empty day is the expected output.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "| Symbol | Gap | Gap% | Doji | Body | Entry | Price | Stop | Risk% | Target | "
        "Needs | Reachable |",
        "| --- | --- | --: | --- | --: | --- | --: | --: | --: | --: | --: | --- |",
    ]
    for s in signals:
        lines.append(
            f"| **{s.symbol}** | {s.gap_at:%H:%M}{'*' if s.prime_time else ''} | "
            f"{s.gap_pct:.2f}% | {s.doji_at:%H:%M} | {s.doji_body_pct:.0f}% | "
            f"{s.entry_at:%H:%M} | {s.entry:,.2f} | {s.stop:,.2f} | {s.risk_pct:.2f}% | "
            f"{s.target:,.2f} | {s.required_move_pct:.1f}% | "
            f"{'yes' if s.feasible else 'no'} |"
        )
    lines.append("")

    for s in signals:
        lines += [f"### {s.symbol}", "", f"- **Why:** {s.note}"]
        lines += [f"- **{label}:** {value}" for label, value in execution_lines(s, cfg)]
        lines.append("")
    return "\n".join(lines)


def write_brief(signals: list[TridentSignal], as_of: str, cfg: TridentSettings) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{as_of}-trident.md"
    path.write_text(build_markdown(signals, as_of, cfg), encoding="utf-8")
    return str(path)


def render_backtest(result, cfg: TridentSettings) -> Group:
    """Measured performance, with the sample-size problem stated before the numbers.

    The headline this strategy invites is a win rate, so the win rate is never printed
    without its interval. At 20R the break-even hit rate is 4.8%, which means a handful of
    trades can look spectacular or catastrophic on noise alone.
    """
    if not result.trades:
        return Group(
            Text.from_markup(
                "[red]No trades generated.[/] Either no fair value gap printed inside the "
                "kill zone with the EMAs stacked, or nothing retraced to the 50% as a doji.\n"
            ),
            _rejection_table(result),
        )

    breakeven = result.break_even_win_rate(cfg.reward_risk)
    win = result.win_rate
    lo_w, hi_w = result.win_rate_interval()
    lo, hi = result.confidence_interval()
    decided = len(result.resolved)

    summary = Table(
        title="Kill-zone trident — backtest", header_style="bold", box=None, padding=(0, 2)
    )
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Trades taken", f"{len(result.trades):,}")
    summary.add_row("Symbols / sessions", f"{result.symbols_tested} / {result.sessions_spanned}")
    summary.add_row("Fair value gaps seen", f"{result.gaps_found:,}")
    if result.fetch_failures:
        # Surfaced rather than buried: a dropped symbol shrinks the sample, so two runs of
        # the same command can legitimately disagree and the reader needs to know by how much.
        summary.add_row(
            "[yellow]Symbols dropped (fetch failed)[/]",
            f"[yellow]{result.fetch_failures}[/]",
        )
    summary.add_row("Resolved (stop or target)", f"{decided:,}")
    summary.add_row(
        "Unresolved (time-stopped or still open)", f"{len(result.censored):,}"
    )
    summary.add_row("[bold]Win rate[/]", f"[bold]{win:.1f}%[/]" if decided else "—")
    summary.add_row(
        "95% interval on the win rate",
        f"[{lo_w:.1f}%, {hi_w:.1f}%]" if decided else "—",
    )
    summary.add_row(f"Break-even at {cfg.reward_risk:.0f}R (before costs)", f"{breakeven:.1f}%")
    summary.add_row("Expectancy per trade", f"{result.expectancy_r:+.3f}R")
    summary.add_row(
        "Mean cost per trade",
        f"-{np.mean([t.cost_r for t in result.finished]):.3f}R" if result.finished else "—",
    )
    summary.add_row("[bold]Net expectancy[/]", f"[bold]{result.net_expectancy_r:+.3f}R[/]")
    summary.add_row("95% CI on net", f"[{lo:+.3f}, {hi:+.3f}]")
    summary.add_row("Total", f"{result.total_r:+.1f}R")

    parts: list = [summary]

    # The verdict is read off the *interval*, never the point estimate. Observing zero wins
    # in twenty trades feels decisive and is not: against a 4.8% break-even rate, a system
    # with exactly no edge produces a run of twenty losses better than a third of the time.
    if decided:
        if lo_w > breakeven:
            verdict = "[green]above break-even — the interval clears it[/]"
        elif hi_w < breakeven:
            verdict = "[red]below break-even — the interval excludes it[/]"
        else:
            verdict = (
                # Rich reads square brackets as markup, so the interval is written as a
                # plain range rather than escaped — it is read aloud more often than parsed.
                f"[yellow]not distinguishable from break-even: the interval "
                f"{lo_w:.1f}%–{hi_w:.1f}% contains {breakeven:.1f}%[/]"
            )
        parts.append(Text.from_markup(f"\n{verdict}\n"))
        if hi_w - lo_w > 30 or decided < 30:
            parts.append(
                Text.from_markup(
                    f"[yellow]{decided} resolved trades is too few to say more. This sample "
                    "cannot settle the source's claim in either direction — only "
                    "forward-collected data can.[/]\n"
                )
            )
        if len({t.outcome for t in result.resolved}) == 1:
            parts.append(
                Text.from_markup(
                    "[yellow]Every resolved trade shared one outcome, so the CI on net "
                    "expectancy above is an artefact: with no observed upside there is "
                    "barely any variance to widen it. Read the win-rate interval, not "
                    "that one.[/]\n"
                )
            )

    outcomes = Table(title="\nBy outcome", header_style="bold", box=None, padding=(0, 2))
    outcomes.add_column("Outcome")
    outcomes.add_column("n", justify="right")
    outcomes.add_column("Mean R", justify="right")
    outcomes.add_column("Through a gap", justify="right")
    for name in ("target", "stop", "time-stop", "open"):
        subset = [t for t in result.trades if t.outcome == name]
        if not subset:
            continue
        outcomes.add_row(
            name,
            str(len(subset)),
            f"{np.mean([t.realised_r for t in subset]):+.2f}",
            str(sum(t.gapped for t in subset)),
        )
    parts.append(outcomes)

    reachable = [t for t in result.trades if t.feasible]
    if reachable and len(reachable) != len(result.trades):
        unreachable = [t for t in result.trades if not t.feasible]
        split = Table(
            title="\nSplit by whether 20R was arithmetically reachable",
            header_style="bold", box=None, padding=(0, 2),
        )
        split.add_column("Cohort")
        split.add_column("n", justify="right")
        split.add_column("Mean stop%", justify="right")
        split.add_column("Needs", justify="right")
        split.add_column("Net R", justify="right")
        for label, cohort in (("reachable", reachable), ("not reachable", unreachable)):
            split.add_row(
                label,
                str(len(cohort)),
                f"{np.mean([t.risk_pct for t in cohort]):.2f}%",
                f"{np.mean([t.required_move_pct for t in cohort]):.1f}%",
                f"{np.mean([t.net_r for t in cohort]):+.3f}",
            )
        parts.append(split)

    parts.append(_rejection_table(result))
    parts.append(
        Text.from_markup(
            "\n[dim]A bar touching both stop and target books a loss. A session opening "
            "beyond a level resolves at the open, so a gap through the stop costs more than "
            "1R — without that, losses would be capped at 1R while the 20R upside stayed "
            "intact. Costs are delivery-rate and charged per trade from its own stop "
            "distance. 30-minute history reaches ~60 sessions, so this is one market "
            "period, and trades entered near its end are right-censored rather than "
            "counted.[/]"
        )
    )
    return Group(*parts)


def _rejection_table(result) -> Table:
    """Why candidates were refused — the accounting that makes a filter arguable.

    A scanner that reports only what it admitted cannot be audited: the interesting number
    is almost always which condition did the rejecting.
    """
    table = Table(title="\nWhy setups were refused", header_style="bold", box=None,
                  padding=(0, 2))
    table.add_column("Condition")
    table.add_column("Sessions", justify="right")
    for reason, count in sorted(result.rejections.items(), key=lambda kv: -kv[1]):
        table.add_row(reason, f"{count:,}")
    if not result.rejections:
        table.add_row("—", "0")
    return table


def render_watch(records, result, cfg: TridentSettings) -> Group:
    """The forward record: what has been flagged so far, and what it has done.

    This is the surface that matters most, because it is the only one reporting data the
    strategy has not already been fitted to. It leads with how many sessions have been
    collected, so a promising-looking early number is read next to the sample it came from.
    """
    sessions = len({r.as_of for r in records})
    open_rows = [r for r in records if r.outcome == "open"]
    done = [r for r in records if r.outcome != "open"]

    parts: list = [
        Text.from_markup(
            f"[bold]Trident forward record[/]\n"
            f"[dim]{len(records)} setups across {sessions} collected session(s) · "
            f"{len(open_rows)} open · {len(done)} resolved[/]\n"
        )
    ]

    if not records:
        parts.append(
            Text.from_markup(
                "\n[yellow]Nothing collected yet.[/]\n"
                "[dim]Run this after the close on each trading day. The pattern fires "
                "between 09:15 and 12:45, so a day is only collectable once it has "
                "happened — and most days produce nothing.[/]"
            )
        )
        return Group(*parts)

    table = Table(title="\nEvery setup recorded", header_style="bold")
    for column, justify in (
        ("Date", "left"), ("Symbol", "left"), ("Entry", "right"), ("Stop", "right"),
        ("Risk%", "right"), ("Target", "right"), ("R:R", "right"), ("Outcome", "left"),
        ("Held", "right"), ("Net R", "right"),
    ):
        table.add_column(column, justify=justify)

    for r in sorted(records, key=lambda r: r.as_of):
        colour = {"target": "green", "stop": "red", "time-stop": "yellow"}.get(
            r.outcome, "dim"
        )
        table.add_row(
            r.as_of, f"[bold]{r.symbol}[/]", f"{r.entry:,.2f}", f"{r.stop:,.2f}",
            f"{r.risk_pct:.2f}%", f"{r.target:,.2f}", f"{r.reward_risk:.0f}R",
            f"[{colour}]{r.outcome}[/]" + (" (gap)" if r.gapped else ""),
            str(r.sessions_held) if r.outcome != "open" else "—",
            f"[{colour}]{r.net_r:+.2f}[/]" if r.outcome != "open" else "—",
        )
    parts.append(table)

    if done:
        decided = result.resolved
        lo, hi = result.win_rate_interval()
        summary = Table(title="\nSo far", header_style="bold", box=None, padding=(0, 2))
        summary.add_column("Metric")
        summary.add_column("Value", justify="right")
        summary.add_row("Resolved by stop or target", f"{len(decided)}")
        if decided:
            summary.add_row("Wins", f"{sum(t.outcome == 'target' for t in decided)}")
            summary.add_row("Win rate", f"{result.win_rate:.1f}%")
            summary.add_row("95% interval", f"{lo:.1f}%–{hi:.1f}%")
        summary.add_row("Net expectancy", f"{result.net_expectancy_r:+.3f}R")
        summary.add_row("Total", f"{result.total_r:+.2f}R")
        parts.append(summary)

    # The number of sessions is the honest headline, not the win rate. A week of collection
    # is about five sessions, and at this strategy's rate that is a handful of setups —
    # nowhere near enough to conclude anything, however the early ones land.
    if sessions < 40:
        parts.append(
            Text.from_markup(
                f"\n[yellow]{sessions} session(s) collected. This is far too early to read "
                "as a win rate — at roughly a third of a setup per session, a month of "
                "collection is still single-digit trades. Let it run.[/]"
            )
        )
    parts.append(
        Text.from_markup(
            "\n[dim]Paper record only. Nothing here was traded and the system places no "
            "orders. Outcomes are settled by the same resolver the backtest uses: a bar "
            "touching both levels books a loss, and a gap through a level resolves at the "
            "open.[/]"
        )
    )
    return Group(*parts)
