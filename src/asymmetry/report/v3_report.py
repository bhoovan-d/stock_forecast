"""Reporting for Specification V3 (§18).

V3 asks for a dashboard of only the highest-quality setups, each answering its "why now?"
chain (§12). Two reporting choices follow directly from the spec:

* **Rejections are shown as counts, not rows.** With ~180 candidates screened and a handful
  qualifying, the useful signal is *which filter is binding* — not 170 individual failures.

* **Near-misses on stop distance are surfaced separately.** A setup whose invalidation sits
  at 1.7% is not a bad stock; it is a good stock whose entry has not arrived. Those are worth
  watching tomorrow, which is different from a rejection.
"""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..config import BRIEF_DIR, settings
from ..engines.v3_scan import V3Candidate, V3Scan


def _direction_style(direction: str) -> str:
    return "green" if direction == "long" else "red"


def render_v3(scan: V3Scan) -> Group:
    parts: list = [
        Text.from_markup(
            f"[bold]Specification V3 — {scan.as_of}[/]\n"
            f"[dim]NIFTY 500 · long + short · 4R minimum · stop "
            f"{settings.min_stop_pct:.1f}–{settings.v3_max_stop_pct:.1f}% · 1–5 sessions[/]\n"
            f"[dim]{scan.regime_note} · data: {scan.tier}[/]\n"
        )
    ]

    funnel = Table(title="Funnel", box=None, header_style="bold", padding=(0, 2))
    funnel.add_column("Stage")
    funnel.add_column("Count", justify="right")
    funnel.add_row("NIFTY 500 liquid", str(scan.liquid))
    funnel.add_row("showing a V3 setup", str(scan.with_setup))
    funnel.add_row("intraday-evaluated", str(scan.evaluated))
    funnel.add_row("cleared the quality floor", str(scan.cleared_floor))
    funnel.add_row("[bold]shown today[/]", f"[bold]{len(scan.trades)}[/]")
    parts.append(funnel)

    if scan.reject_counts:
        rejects = Table(title="\nWhich filter was binding", box=None, header_style="bold",
                        padding=(0, 2))
        rejects.add_column("Hard filter")
        rejects.add_column("n", justify="right")
        for name, count in sorted(scan.reject_counts.items(), key=lambda kv: -kv[1]):
            rejects.add_row(name, str(count))
        parts.append(rejects)

    if scan.trades:
        table = Table(title=f"\nQualified setups — {len(scan.trades)}", header_style="bold")
        table.add_column("Symbol", no_wrap=True)
        table.add_column("Dir", no_wrap=True)
        table.add_column("Setup", no_wrap=True)
        table.add_column("Score", justify="right")
        table.add_column("Entry", justify="right", no_wrap=True)
        table.add_column("Stop", justify="right", no_wrap=True)
        table.add_column("Stop%", justify="right")
        table.add_column("4R target", justify="right", no_wrap=True)
        table.add_column("Move", justify="right")
        table.add_column("Qty", justify="right")

        for candidate in scan.trades:
            plan = candidate.plan
            style = _direction_style(candidate.direction)
            table.add_row(
                f"[bold]{candidate.symbol}[/]",
                f"[{style}]{candidate.direction.upper()}[/]",
                candidate.setup.kind.value,
                f"{candidate.score:.1f}",
                f"{plan.entry:,.2f}",
                f"{plan.stop:,.2f}",
                f"{plan.stop_pct:.2f}%",
                f"{plan.target:,.2f}",
                f"{plan.target_pct:+.1f}%",
                str(plan.quantity),
            )
        parts.append(table)

        for candidate in scan.trades:
            parts.append(_detail(candidate))
    else:
        parts.append(
            Text.from_markup(
                "\n[yellow]Nothing qualified.[/]\n"
                f"[dim]V3 targets ~{settings.target_setups_per_month} setups a month, not a "
                "daily list. Most days produce none, and the specification is explicit that "
                "the 4R and stop requirements never loosen to fill a quota.[/]"
            )
        )

    if scan.near_miss:
        watch = Table(title="\nWatch — good stock, entry not there yet", box=None,
                      header_style="bold", padding=(0, 2))
        watch.add_column("Symbol")
        watch.add_column("Dir")
        watch.add_column("Why not")
        for candidate in scan.near_miss[:10]:
            watch.add_row(
                candidate.symbol,
                candidate.direction,
                candidate.reject_detail or candidate.rejected_by,
            )
        parts.append(watch)

    parts.append(
        Text.from_markup(
            "\n[dim]Decision support only — these are setups for your judgement, never "
            "instructions. The system places no orders.[/]"
        )
    )
    return Group(*parts)


def _detail(candidate: V3Candidate) -> Text:
    """The §12 'why now?' chain for one candidate."""
    plan = candidate.plan
    style = _direction_style(candidate.direction)
    modules = candidate.modules
    return Text.from_markup(
        f"\n[bold]{candidate.symbol}[/] [{style}]{candidate.direction.upper()}[/] — "
        f"{candidate.company}\n"
        f"  [dim]Why this stock:[/] RS vs NIFTY {candidate.rs_nifty_pct:.0f}th pct · "
        f"vs sector {candidate.rs_sector_pct:.0f}th · accel {candidate.rs_accel_pct:.0f}th\n"
        f"  [dim]Why this sector:[/] {candidate.sector} — {candidate.sector_state} "
        f"({candidate.sector_percentile:.0f}th pct)\n"
        f"  [dim]Why now:[/] {candidate.why_now}\n"
        f"  [dim]Why it can move:[/] ADR {candidate.adr_pct:.1f}% · ATR {candidate.atr_pct:.1f}% "
        f"· needs {plan.target_pct:.1f}%\n"
        f"  [dim]Structure:[/] W {candidate.weekly_note} → D {candidate.daily_note}"
        + (f" → 60m {candidate.hourly_note}" if candidate.hourly_note else "")
        + f"\n  [dim]Where I'm wrong:[/] {plan.invalidation}"
        + (
            f"\n  [dim]Nearest opposing structure:[/] {plan.nearest_barrier:,.2f}"
            if plan.nearest_barrier
            else ""
        )
        + f"\n  [dim]Score parts:[/] "
        + " · ".join(f"{k.replace('_', ' ')} {v:.0f}" for k, v in modules.items())
    )


def build_v3_markdown(scan: V3Scan) -> str:
    lines = [
        f"# Specification V3 — {scan.as_of}",
        "",
        f"*NIFTY 500 · long + short · 4R minimum · stop {settings.min_stop_pct:.1f}–"
        f"{settings.v3_max_stop_pct:.1f}% · 1–5 sessions*",
        "",
        f"*{scan.regime_note} · data: {scan.tier}*",
        "",
        "> Decision support only. Every row is a setup for your own judgement, never an "
        "instruction to buy or sell. The system places no orders.",
        "",
        "## Funnel",
        "",
        "| Stage | Count |",
        "| --- | --: |",
        f"| NIFTY 500 liquid | {scan.liquid} |",
        f"| Showing a V3 setup | {scan.with_setup} |",
        f"| Intraday-evaluated | {scan.evaluated} |",
        f"| Cleared the quality floor | {scan.cleared_floor} |",
        f"| **Shown today** | **{len(scan.trades)}** |",
        "",
    ]

    if scan.reject_counts:
        lines += ["### Which filter was binding", "", "| Hard filter | n |", "| --- | --: |"]
        for name, count in sorted(scan.reject_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {name} | {count} |")
        lines.append("")

    lines += [
        "---", "", f"## Today's setups — {len(scan.trades)}", "",
        f"*{scan.cleared_floor} cleared the quality floor; the best "
        f"{len(scan.trades)} are shown. V3 targets ~10–15 a month, so the cap demotes the "
        "rest to the watch list rather than widening the output.*", "",
    ]
    if not scan.trades:
        lines += [
            "**Nothing qualified today.**",
            "",
            f"V3 targets roughly {settings.target_setups_per_month} setups a month, not a "
            "daily list. Most days produce none, and the specification is explicit that the "
            "4R and stop-distance requirements never loosen to fill a quota.",
            "",
        ]
    else:
        lines += [
            "| # | Symbol | Dir | Setup | Score | Entry | Stop | Stop% | 4R target | Move | Qty |",
            "| --: | --- | --- | --- | --: | --: | --: | --: | --: | --: | --: |",
        ]
        for i, candidate in enumerate(scan.trades, 1):
            plan = candidate.plan
            lines.append(
                f"| {i} | **{candidate.symbol}** | {candidate.direction.upper()} | "
                f"{candidate.setup.kind.value} | {candidate.score:.1f} | {plan.entry:,.2f} | "
                f"{plan.stop:,.2f} | {plan.stop_pct:.2f}% | {plan.target:,.2f} | "
                f"{plan.target_pct:+.1f}% | {plan.quantity} |"
            )
        lines.append("")

        for candidate in scan.trades:
            plan = candidate.plan
            lines += [
                f"### {candidate.symbol} — {candidate.direction.upper()}",
                "",
                f"*{candidate.company} · {candidate.sector}*",
                "",
                f"- **Why this stock:** RS vs NIFTY {candidate.rs_nifty_pct:.0f}th pct, "
                f"vs sector {candidate.rs_sector_pct:.0f}th, acceleration "
                f"{candidate.rs_accel_pct:.0f}th",
                f"- **Why this sector:** {candidate.sector_state} "
                f"({candidate.sector_percentile:.0f}th percentile)",
                f"- **Why now:** {candidate.why_now}",
                f"- **Why it can move:** ADR {candidate.adr_pct:.1f}%, ATR "
                f"{candidate.atr_pct:.1f}%, target needs {plan.target_pct:.1f}%",
                f"- **Setup:** {candidate.setup.kind.value} — {candidate.setup.note}",
                f"- **Structure:** Weekly {candidate.weekly_note} → Daily "
                f"{candidate.daily_note}"
                + (f" → 60m {candidate.hourly_note}" if candidate.hourly_note else ""),
                f"- **Where I'm wrong:** {plan.invalidation}",
                "",
            ]

    if scan.near_miss:
        lines += [
            "---", "", "## Watch — good stock, entry not there yet", "",
            "| Symbol | Dir | Why not |", "| --- | --- | --- |",
        ]
        for candidate in scan.near_miss[:15]:
            lines.append(
                f"| {candidate.symbol} | {candidate.direction} | "
                f"{candidate.reject_detail or candidate.rejected_by} |"
            )
        lines.append("")

    return "\n".join(lines)


def write_v3_brief(scan: V3Scan) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{scan.as_of}-v3.md"
    path.write_text(build_v3_markdown(scan), encoding="utf-8")
    return str(path)


def render_backtest(result) -> Group:
    """Walk-forward result for the V3 engine, measured on real 15m triggers.

    Reported bluntly: the win rate is compared against the break-even a 4R payoff requires,
    because that single comparison decides whether the specification is tradeable. Costs are
    shown separately rather than folded in, so a marginal result cannot look profitable.
    """
    if not result.trades:
        return Group(
            Text.from_markup(
                "[red]No trades generated.[/] Either no setups fired in the available "
                "intraday history, or every one failed a hard filter."
            )
        )

    breakeven = result.break_even_win_rate
    win = result.win_rate
    verdict = (
        "[green]above break-even — the setup pays at this sample[/]"
        if win > breakeven + 3
        else "[yellow]around break-even — not distinguishable from a coin flip here[/]"
        if win > breakeven - 3
        else "[red]below break-even — this geometry loses money at the measured hit rate[/]"
    )

    summary = Table(title="V3 intraday-triggered backtest", header_style="bold", box=None,
                    padding=(0, 2))
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Trades", f"{len(result.trades):,}")
    summary.add_row("Symbols / sessions", f"{result.symbols_tested} / {result.sessions_spanned}")
    summary.add_row("Resolved (hit stop or target)", f"{len(result.resolved):,}")
    summary.add_row("[bold]Win rate[/]", f"[bold]{win:.1f}%[/]")
    summary.add_row("Break-even at 4R", f"{breakeven:.1f}%")
    summary.add_row("Expectancy per trade", f"{result.expectancy_r:+.3f}R")
    summary.add_row("Costs", f"-{result.cost_r:.3f}R")
    summary.add_row("[bold]Net expectancy[/]", f"[bold]{result.net_expectancy_r:+.3f}R[/]")
    summary.add_row("Total", f"{result.total_r:+.1f}R")

    parts: list = [summary, Text.from_markup(f"\n{verdict}\n")]

    for label, key in (
        ("By setup", lambda t: t.setup),
        ("By direction", lambda t: t.direction),
        ("By stop distance", lambda t: f"{round(t.stop_pct * 2) / 2:.1f}%"),
    ):
        frame = result.by(key)
        if frame.empty or len(frame) < 2:
            continue
        table = Table(title=f"\n{label}", header_style="bold", box=None, padding=(0, 2))
        table.add_column("Bucket")
        table.add_column("n", justify="right")
        table.add_column("Win rate", justify="right")
        table.add_column("Mean R", justify="right")
        for row in frame.itertuples():
            style = "green" if row.mean_r > 0 else "red"
            rate = f"{row.win_rate:.0f}%" if row.win_rate == row.win_rate else "—"
            table.add_row(str(row.bucket), str(int(row.n)), rate,
                          f"[{style}]{row.mean_r:+.2f}[/]")
        parts.append(table)

    parts.append(
        Text.from_markup(
            "\n[dim]Entries and stops come from the live engine, resolved on 15-minute bars. "
            "A bar touching both stop and target counts as a loss, since intraday sequence "
            "is unknown. Intraday history is limited to roughly 60-80 days, so treat the "
            "buckets as indicative rather than calibrated.[/]"
        )
    )
    return Group(*parts)
