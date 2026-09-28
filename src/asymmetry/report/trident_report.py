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
from ..engines.trident import GATE_NAMES, TridentSettings, TridentSignal

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
        ("Liquidity taken", liquidity_thesis(signal, cfg)),
    ]


def liquidity_thesis(signal: TridentSignal, cfg: TridentSettings) -> str:
    """Which pool of resting orders, if any, was taken before this gap formed.

    Three distinct states, and they must stay distinguishable. A pool was swept; no pool was
    swept; or the layer did not run. Collapsing the last two would make a missing measurement
    read as a measured absence — the fallback-with-no-marker trap.
    """
    if not cfg.tag_liquidity_sweep:
        return "not checked — the liquidity-sweep layer is switched off for this run."
    if not signal.sweep_checked:
        return (
            "**not checked** — the pool map could not be built for this name. This is not "
            "the same as no liquidity being taken, and must not be read as it."
        )
    if not signal.swept:
        return (
            "**none** — no sell-side pool was taken and reclaimed before the gap formed. "
            "The imbalance printed in isolation. Recorded, not penalised: whether that "
            "matters is exactly what the tagged-vs-untagged comparison is being collected "
            "to answer, and it has not been answered yet."
        )
    return (
        f"**{signal.swept_pool}** at {signal.swept_level:,.2f}, taken and reclaimed "
        f"{signal.sweep_bars_before_gap} candle(s) before the gap completed. The gap's 50% "
        f"sits {signal.sweep_to_midpoint_pct:+.2f}% from that level. The thesis is that "
        "sellers were flushed out of that pool and the imbalance is what the resulting "
        "buying left behind — **tagged, never gated**: it does not admit or refuse this "
        "setup, and it will not until tagged and untagged cohorts can be compared."
    )


def render_scan(signals: list[TridentSignal], as_of: str, cfg: TridentSettings) -> Group:
    parts: list = [
        Text.from_markup(
            f"[bold]Kill-zone trident — {as_of}[/]\n"
            f"[dim]long only · {cfg.reward_risk:.0f}R · "
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


def render_scan_full(scan, as_of: str, cfg: TridentSettings) -> Group:
    """The scan plus everything needed to argue with it: the universe gate, the funnel and
    every near miss with its numbers.

    `render_scan` still renders the setups alone, because the Markdown brief and the forward
    record want only those. This is the console surface, where the accounting belongs.
    """
    parts: list = [
        render_liquidity(scan.liquidity, cfg),
        render_scan(scan.signals, as_of, cfg),
        render_funnel(scan.funnel(), scan.universe_size),
    ]
    if scan.suppressed:
        parts.append(
            Text.from_markup(
                f"\n[dim]{scan.suppressed} further qualifying setup(s) fell below the "
                f"{cfg.max_alerts_per_session}-alert budget, ranked by net R after costs.[/]"
            )
        )
    late = [f for f in scan.failures if f.gate_reached >= 4]
    if late:
        detail = Table(title="\nNear misses — gate 4 and 5, with the numbers",
                       header_style="bold", box=None, padding=(0, 2))
        detail.add_column("Symbol")
        detail.add_column("Gate", justify="right")
        detail.add_column("Reason")
        for signal in late[:20]:
            detail.add_row(signal.symbol, str(signal.gate_reached), signal.gate_reason)
        parts.append(detail)
        if len(late) > 20:
            parts.append(Text.from_markup(f"[dim]…{len(late) - 20} more.[/]"))
    if scan.fetch is not None and scan.fetch.failed:
        parts.append(Text.from_markup(f"\n[red]Fetch failures:[/] {scan.fetch.summary()}"))
    return Group(*parts)


def build_markdown(signals: list[TridentSignal], as_of: str, cfg: TridentSettings) -> str:
    lines = [
        f"# Kill-zone trident — {as_of}",
        "",
        f"*Long only · {cfg.reward_risk:.0f}R · {cfg.anchor_interval} inside "
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

    cohorts = result.sweep_split()
    tagged, untagged = cohorts["swept"], cohorts["not swept"]
    if tagged or untagged:
        sweep_table = Table(
            title="\nSplit by whether sell-side liquidity was taken before the gap",
            header_style="bold", box=None, padding=(0, 2),
        )
        sweep_table.add_column("Cohort")
        sweep_table.add_column("n", justify="right")
        sweep_table.add_column("Net R", justify="right")
        for label, cohort in (
            ("liquidity swept", tagged),
            ("no sweep", untagged),
            ("not checked", cohorts["not checked"]),
        ):
            if not cohort:
                continue
            sweep_table.add_row(
                label, str(len(cohort)), f"{np.mean([t.net_r for t in cohort]):+.3f}"
            )
        parts.append(sweep_table)
        parts.append(
            Text.from_markup(
                "[dim]Tagged, never gated. With cohorts this size the difference between "
                "them is noise — the split is printed so it accumulates, not so it is acted "
                "on. Making it a filter today would shrink an already tiny sample on an "
                "untested assumption.[/]"
            )
        )

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


# ── Live monitoring surfaces ──────────────────────────────────────────────────

SPREAD_CAVEAT = (
    "Spread figures are Corwin-Schultz estimates from daily high/low, not quoted spreads. "
    "No bid/ask feed is reachable from this project at any price."
)


def render_liquidity(screen, cfg: TridentSettings) -> Group:
    """The universe gate, reported before anything downstream of it.

    Printed even when it admits everything: the count that makes an expansion to the NIFTY
    500 arguable is how much of the 500 was thrown away and why, and a gate that reports only
    its survivors cannot be audited.
    """
    if screen is None:
        return Group(
            Text.from_markup(
                "[dim]Liquidity gate not run — an explicit symbol list was supplied.[/]"
            )
        )
    if screen.days_used == 0:
        return Group(
            Text.from_markup(
                "[red]Liquidity gate could not run.[/] No bhavcopy was readable for any of "
                f"the last {screen.days_requested} sessions, so the universe is empty by "
                "**outage**, not by selection. Nothing below is a market observation.\n"
            )
        )

    table = Table(title="\nUniverse — liquidity floor", header_style="bold", box=None,
                  padding=(0, 2))
    table.add_column("Stage")
    table.add_column("Names", justify="right")
    table.add_row("Index constituents", f"{screen.considered:,}")
    for reason, count in sorted(screen.reason_counts().items(), key=lambda kv: -kv[1]):
        table.add_row(f"  [dim]refused — {reason}[/]", f"[dim]-{count:,}[/]")
    table.add_row("[bold]Cleared the floor[/]", f"[bold]{len(screen.passing):,}[/]")

    parts: list = [table]
    if screen.days_missing:
        parts.append(
            Text.from_markup(
                f"[yellow]{len(screen.days_missing)} session(s) missing from the archive: "
                + ", ".join(str(d) for d in screen.days_missing)
                + f". The screen ran on {screen.days_used}, not "
                f"{screen.days_requested}.[/]"
            )
        )
    if screen.missing:
        parts.append(
            Text.from_markup(
                f"[yellow]{len(screen.missing)} index member(s) had no EOD row at all: "
                + ", ".join(sorted(screen.missing)[:12])
                + (" …" if len(screen.missing) > 12 else "")
                + ". Named rather than counted — a shrinking denominator is how a moving "
                "sample looks like a stable one.[/]"
            )
        )
    # The footer must say what actually gated. Printing "estimated spread <= 25bp" while the
    # spread cap is disarmed would describe a filter that did not run — the same class of
    # error as a fallback with no marker.
    spread_line = (
        f", estimated spread ≤ {cfg.max_spread_bps:.0f}bp"
        if cfg.require_spread_estimate
        else ""
    )
    parts.append(
        Text.from_markup(
            f"[dim]Floor: ₹{cfg.min_median_turnover_inr / 1e7:.0f} cr/day median turnover "
            f"over {screen.days_used} sessions{spread_line}.\n"
            + (
                f"{SPREAD_CAVEAT}\n"
                if cfg.require_spread_estimate
                else "The estimated-spread cap is OFF and did not remove anything. It is "
                     "computed and reported only: on NSE data the Corwin-Schultz estimate "
                     "correlates -0.15 with turnover and +0.31 with daily range, and at "
                     "25bp refuses 40 of the NIFTY 50. --require-spread arms it.\n"
            )
            + "This runs FIRST, before any price action is examined: a bar with no prints "
            "has a low equal to its high, which the gap detector reads as a perfect "
            "imbalance. Those are data holes, not liquidity voids.[/]"
        )
    )
    return Group(*parts)


def render_funnel(rows: list[tuple[int, str, int]], universe_size: int) -> Table:
    """Distinct symbols reaching each of the five gates.

    The breakdown the owner asked to keep. Its value is negative as often as positive: a gate
    nothing ever reaches is not strict, it is unmeasurable, and this is the only surface that
    says which one that is.
    """
    table = Table(title="\nFunnel — symbols reaching each gate", header_style="bold",
                  box=None, padding=(0, 2))
    table.add_column("Gate")
    table.add_column("Condition")
    table.add_column("Reached", justify="right")
    table.add_column("Share", justify="right")
    table.add_row("0", "universe after the liquidity floor", f"{universe_size:,}", "100%")
    for gate, name, count in rows:
        share = f"{count / universe_size * 100:.1f}%" if universe_size else "—"
        table.add_row(str(gate), name, f"{count:,}", share)
    return table


def _alert_panel(alert, cfg: TridentSettings) -> Text:
    """One alert, with every field the owner asked to carry on it."""
    heading = "[yellow]GET READY[/]" if alert.gate == 4 else "[green]TRIGGER[/]"
    lines = [
        f"\n{heading} [bold]{alert.symbol}[/] "
        f"[dim]gate {alert.gate} · {alert.interval} · {alert.at}[/]",
        f"  [dim]Gap:[/] {alert.gap_low:,.2f} – {alert.gap_high:,.2f}, "
        f"50% at {alert.gap_midpoint:,.2f}",
        f"  [dim]Doji:[/] O {alert.doji_open:,.2f}  H {alert.doji_high:,.2f}  "
        f"L {alert.doji_low:,.2f}  C {alert.doji_close:,.2f}  "
        f"— body {alert.body_to_range_pct:.1f}% of range",
        f"  [dim]Entry:[/] {alert.entry:,.2f}"
        + ("  [yellow](PROVISIONAL)[/]" if alert.provisional else ""),
        f"  [dim]Stop:[/] {alert.stop:,.2f}   "
        f"[dim]Target:[/] {alert.target:,.2f}   "
        f"[dim]Risk:[/] {alert.risk_pct:.2f}% of price",
        f"  [dim]Net R at target:[/] {alert.net_r_at_target:+.2f}R "
        f"[dim]({alert.reward_risk:.0f}R gross less {alert.cost_r:.2f}R round-trip cost at "
        f"this stop distance)[/]",
        f"  [dim]Invalidated if:[/] {alert.invalidated_if}",
    ]
    if alert.swept_pool:
        lines.append(
            f"  [dim]Liquidity taken:[/] {alert.swept_pool}, "
            f"{alert.sweep_bars_before_gap} candle(s) before the gap, "
            f"{alert.sweep_to_midpoint_pct:+.2f}% from the 50%"
        )
    if alert.note:
        lines.append(f"  [dim]{alert.note}[/]")
    return Text.from_markup("\n".join(lines))


def render_live(scan, cfg: TridentSettings) -> Group:
    """One monitoring pass: what fired, what did not, and why not."""
    parts: list = [
        Text.from_markup(
            f"[bold]Kill-zone trident — live pass[/]\n"
            f"[dim]{scan.as_of} · {cfg.anchor_interval} candles · "
            f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M} · "
            f"{scan.universe_size:,} names · evaluated on closed candles only · "
            f"pass took {scan.elapsed_sec:.0f}s[/]"
        )
    ]

    if scan.elapsed_sec > cfg.interval_minutes * 60:
        parts.append(
            Text.from_markup(
                f"\n[red]This pass took {scan.elapsed_sec:.0f}s against a "
                f"{cfg.interval_minutes}-minute candle.[/] The monitor is behind the market: "
                "by the time it finished, the bar it evaluated was already stale. Cut the "
                "universe or move to a longer interval — a late alert on a 20R setup is not "
                "a small problem, because the entry is a single bar's close."
            )
        )

    triggers = [a for a in scan.alerts if a.gate == 5]
    ready = [a for a in scan.alerts if a.gate == 4]
    if not scan.alerts:
        parts.append(
            Text.from_markup(
                "\n[yellow]No alerts this pass.[/] [dim]The expected state — the source's "
                "own estimate is 8–10 setups a year per instrument.[/]"
            )
        )
    else:
        parts.append(
            Text.from_markup(
                f"\n[bold]{len(triggers)} trigger(s), {len(ready)} get-ready[/]"
                + (
                    f" [dim](+{scan.suppressed} below the "
                    f"{cfg.max_alerts_per_session}-alert budget, ranked by net R after "
                    "costs)[/]"
                    if scan.suppressed
                    else ""
                )
            )
        )
        for alert in scan.alerts:
            parts.append(_alert_panel(alert, cfg))

    parts.append(render_funnel(scan.funnel(), scan.universe_size))
    parts.append(_failure_table(scan.failures))

    if scan.fetch is not None and scan.fetch.failed:
        parts.append(
            Text.from_markup(
                f"\n[red]Fetch failures:[/] {scan.fetch.summary()}\n"
                "[dim]Named rather than counted, and retried before being given up on.[/]"
            )
        )
    parts.append(
        Text.from_markup(
            "\n[dim]Decision support only. Evaluated on closed candles: a forming bar's "
            "high and low only widen, so a body that looks like a doji mid-candle can be a "
            "full-bodied invalidation by its close. Nothing here places an order.[/]\n"
            f"[yellow]{UNPROVEN}[/]"
        )
    )
    return Group(*parts)


def _failure_table(failures, limit: int = 20) -> Group:
    """Every candidate that failed, grouped by gate, with the numbers that decided it.

    Deliberately given as much room as the winners. At this sample size the failure log is
    the more informative half of the output: it is what distinguishes a gate doing real work
    from a gate that simply never lets anything through.
    """
    if not failures:
        return Group(Text.from_markup("\n[dim]No failures recorded this pass.[/]"))

    by_gate: dict[int, list] = {}
    for failure in failures:
        by_gate.setdefault(failure.gate, []).append(failure)

    summary = Table(title="\nWhere candidates failed", header_style="bold", box=None,
                    padding=(0, 2))
    summary.add_column("Gate")
    summary.add_column("Condition")
    summary.add_column("n", justify="right")
    for gate in sorted(by_gate):
        rows = by_gate[gate]
        summary.add_row(str(gate), GATE_NAMES.get(gate, "unknown"), f"{len(rows):,}")

    parts: list = [summary]
    # The late-gate failures are the ones worth reading individually: a name that formed a
    # gap, tagged the 50%, printed a doji and then missed is telling you something about the
    # thresholds. A name with no gap is telling you about the day.
    late = [f for f in failures if f.gate >= 4]
    if late:
        detail = Table(title="\nNear misses — gate 4 and 5, with the numbers",
                       header_style="bold", box=None, padding=(0, 2))
        detail.add_column("Symbol")
        detail.add_column("Gate", justify="right")
        detail.add_column("Reason")
        for failure in late[:limit]:
            detail.add_row(failure.symbol, str(failure.gate), failure.reason)
        parts.append(detail)
        if len(late) > limit:
            parts.append(
                Text.from_markup(
                    f"[dim]…{len(late) - limit} more. Every one is in the JSONL log.[/]"
                )
            )
    return Group(*parts)


def render_sweep(sweep, cfg: TridentSettings) -> Group:
    """The kill-zone window sweep — is 09:15–12:45 contributing anything?

    Read the intervals, never the ranking. A grid searched over ~60 sessions will always have
    a best cell, and adopting it is fitting rather than measuring; the question this sweep can
    actually answer is whether any window is *distinguishable* from the others.
    """
    parts: list = [
        Text.from_markup(
            f"[bold]Kill-zone window sweep[/]\n"
            f"[dim]{sweep.interval} entry · {sweep.reward_risk:.0f}R · "
            f"{sweep.symbols_tested} symbols · {sweep.sessions_spanned} sessions · "
            f"every window replayed on identical frames[/]\n"
        )
    ]
    if not sweep.results:
        parts.append(Text.from_markup("[red]No windows produced any trades.[/]"))
        return Group(*parts)

    # Seven columns, not ten. A squashed table is worse than a smaller one — the same
    # judgement `render_scan` makes — and a wrapped confidence interval is unreadable, which
    # matters here because the interval is the only thing this sweep is powered to report.
    table = Table(title="\nBy window", header_style="bold", box=None, padding=(0, 1))
    for column, justify in (
        ("Window", "left"), ("n", "right"), ("Wins", "right"), ("Stop%", "right"),
        ("Gross R", "right"), ("Net R", "right"), ("95% CI on net", "right"),
    ):
        table.add_column(column, justify=justify)

    for row in sweep.results:
        thin = row.setups < sweep.MIN_CELL
        label = row.label
        if row.label == sweep.baseline:
            label = f"[cyan]{label} *[/]"
        if thin:
            label = f"[dim]{label}[/]"
        table.add_row(
            label,
            f"[dim]{row.setups}[/]" if thin else f"[bold]{row.setups}[/]",
            f"{row.wins}",
            f"{row.mean_risk_pct:.2f}" if row.mean_risk_pct == row.mean_risk_pct else "—",
            f"{row.gross_r:+.3f}" if row.gross_r == row.gross_r else "—",
            f"{row.net_r:+.3f}" if row.net_r == row.net_r else "—",
            f"{row.ci_low:+.2f}…{row.ci_high:+.2f}" if row.ci_low == row.ci_low else "—",
        )
    parts.append(table)
    parts.append(
        Text.from_markup(
            f"[dim]* the transplanted window. Rows in grey carry fewer than "
            f"{sweep.MIN_CELL} trades and take no part in any comparison.[/]"
        )
    )

    thin = sweep.thin_cells()
    if thin:
        parts.append(
            Text.from_markup(
                f"\n[yellow]{len(thin)} of {len(sweep.results)} windows produced fewer than "
                f"{sweep.MIN_CELL} trades[/] ("
                + ", ".join(f"{r.label}: {r.setups}" for r in thin)
                + "). They are shown above and excluded from every comparison below. A "
                "window with two trades has a narrow interval for the same reason a coin "
                "flipped twice looks decisive."
            )
        )
    best = sweep.best()
    if best is not None:
        parts.append(
            Text.from_markup(
                f"\n[dim]Best comparable cell: {best.label} at {best.net_r:+.3f}R on "
                f"{best.setups} setups. Reported, not recommended.[/]"
            )
        )
    if sweep.overlapping_intervals():
        parts.append(
            Text.from_markup(
                "\n[yellow]Every window's confidence interval overlaps every other's.[/] "
                "No block of the session is distinguishable from any other at this sample "
                "size — which means the transplanted 09:15–12:45 is neither vindicated nor "
                "convicted, and is not what is deciding the strategy's result. Do not adopt "
                "the best-looking cell: on this many sessions a grid search will always "
                "produce one, and adopting it is fitting."
            )
        )
    else:
        parts.append(
            Text.from_markup(
                "\n[green]At least one window separates from the others on this sample.[/] "
                "Treat that as a hypothesis for forward collection, not a setting to change: "
                "the grid was searched on the same data that would justify the change."
            )
        )
    parts.append(
        Text.from_markup(
            "\n[dim]Windows overlap by design — a disjoint grid can split a real effect "
            "across a boundary and report nothing on either side. Every window ran on the "
            "same fetched frames, so a symbol cannot be present in one cell and absent from "
            "another.[/]"
        )
    )
    return Group(*parts)


def render_failure_log(rows: list[dict], limit: int = 40) -> Group:
    """The accumulated JSONL failure log, bucketed. Read across days, not within one."""
    if not rows:
        return Group(Text.from_markup("[yellow]No failures recorded yet.[/]"))

    by_bucket: dict[str, int] = {}
    by_gate: dict[int, int] = {}
    for row in rows:
        by_bucket[row.get("bucket", "unknown")] = by_bucket.get(row.get("bucket", "unknown"), 0) + 1
        gate = int(row.get("gate", 0))
        by_gate[gate] = by_gate.get(gate, 0) + 1

    sessions = len({row.get("session", "") for row in rows})
    parts: list = [
        Text.from_markup(
            f"[bold]Trident failure log[/]\n"
            f"[dim]{len(rows):,} refusals across {sessions} session(s)[/]\n"
        )
    ]
    gates = Table(title="\nBy gate", header_style="bold", box=None, padding=(0, 2))
    gates.add_column("Gate")
    gates.add_column("Condition")
    gates.add_column("n", justify="right")
    gates.add_column("Share", justify="right")
    for gate in sorted(by_gate):
        gates.add_row(
            str(gate), GATE_NAMES.get(gate, "unknown"), f"{by_gate[gate]:,}",
            f"{by_gate[gate] / len(rows) * 100:.1f}%",
        )
    parts.append(gates)

    buckets = Table(title="\nBy condition", header_style="bold", box=None, padding=(0, 2))
    buckets.add_column("Condition")
    buckets.add_column("n", justify="right")
    for bucket, count in sorted(by_bucket.items(), key=lambda kv: -kv[1])[:limit]:
        buckets.add_row(bucket, f"{count:,}")
    parts.append(buckets)
    parts.append(
        Text.from_markup(
            "\n[dim]This log is currently worth more than the winners. With 22 measured "
            "setups, what tells you whether a gate is doing real work is the shape of what "
            "it refuses — a threshold that never binds is not strict, and one that refuses "
            "everything is not measurable.[/]"
        )
    )
    return Group(*parts)
