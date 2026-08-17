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

from datetime import date, datetime, timedelta

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..config import BRIEF_DIR, settings
from ..data.yahoo import SESSION_CLOSE
from ..engines.v3 import ENTRY_STOP_THROUGH, TRIGGER_TIMEFRAME_MINUTES
from ..engines.v3_scan import V3Candidate, V3Scan


def _direction_style(direction: str) -> str:
    return "green" if direction == "long" else "red"


def _time_stop(start: date, sessions: int) -> date:
    """The date ``sessions`` trading days after ``start``, counting weekdays only.

    NSE holidays are deliberately not applied. ``nse_archive.trading_days`` resolves a
    session by probing for its published bhavcopy, which by definition only exists for days
    that have already happened, and this project maintains no forward holiday calendar (the
    NSE site is Akamai-blocked from here — see the README). A holiday inside the window
    therefore pushes the real date out by a day, which the rendered wording says outright
    rather than implying a precision that is not available.
    """
    cursor, remaining = start, sessions
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def _next_session(after: date) -> date:
    """The next weekday after `after` — the session a post-close setup is acted on.

    Weekday-based for the same reason as the time stop: no forward NSE holiday calendar
    exists here, so a holiday pushes the real session out by a day.
    """
    cursor = after + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


def carry_lines(candidate: V3Candidate) -> list[tuple[str, str]]:
    """Why this name is judged able to carry 1-5 sessions, not merely interesting.

    Shared by all three V3 surfaces, like `execution_lines`. The checklist is reported even
    when it passes: a gate nobody can see the workings of is indistinguishable from no gate,
    which is how a description-only 60m read survived as long as it did.
    """
    carry = candidate.carry
    if not carry.checks and not carry.failed:
        return []

    from ..engines.carry import gate_applies

    gating = gate_applies(candidate.setup.kind)
    rows: list[tuple[str, str]] = []
    if carry.failed and gating:
        rows.append(("Carry", f"FAILED — {carry.failed}"))
    elif not gating:
        # Say so explicitly. A reclaim card that showed a carry score without this line
        # would look like it had cleared a gate it never faced.
        rows.append((
            "Carry",
            f"{carry.score:.0f}/100 — measured but not gating for "
            f"{candidate.setup_name}: the gate is a continuation-regime test, and it "
            "cut this setup's measured edge rather than protecting it"
            + (f". Would have failed on: {carry.failed}" if carry.failed else ""),
        ))
    else:
        rows.append((
            "Carry",
            f"{carry.score:.0f}/100 · every gating condition met on 60m and 120m",
        ))

    if carry.checks:
        from ..engines.carry import CORE_CONDITIONS

        met = [name for name, ok in carry.checks.items() if ok]
        rows.append(("Conditions", f"{len(met)}/{len(carry.checks)} met — " + "; ".join(
            f"{name}{'' if ok else ' (FAILED)'}"
            f"{'' if name in CORE_CONDITIONS else ' [scored, not gating]'}"
            for name, ok in carry.checks.items()
        )))
    if carry.components:
        rows.append((
            "Carry parts",
            " · ".join(f"{k} {v:.0f}" for k, v in carry.components.items()),
        ))
    rows.append((
        "Higher timeframe",
        f"60m {carry.trend_60m.value} · 120m {carry.trend_120m.value}, "
        f"{carry.setup_120m.value}" + (f" · {carry.note}" if carry.note else ""),
    ))
    return rows


def hard_filter_note(scan: V3Scan) -> str:
    """Which filters were armed on this run — shared by all three surfaces.

    The count is not fixed any more, so no surface may hard-code it. "0 rejected on
    catalyst" and "the catalyst filter never ran" are opposite facts, and a page that
    renders them identically is claiming a check it did not perform.
    """
    base = "4R feasibility, stop distance, liquidity and technical validity"
    if scan.catalyst_required:
        return (
            f"Five hard filters may reject: {base}, and a catalyst — §12 requires an answer "
            "to \"why now\", and a name without one is refused rather than scored down."
        )
    if scan.catalyst_status == "outage":
        return (
            f"Four hard filters may reject: {base}. The catalyst filter is armed in settings "
            "but did not run: the catalyst pass returned nothing for any candidate, which is "
            "an outage rather than an absence of news, and refusing the whole universe on "
            "missing data would dress a broken feed up as selectivity."
        )
    return f"Four hard filters may reject: {base}."


def _bar_span(start) -> str:
    """Describe the interval a trigger bar covers, without inventing one.

    Bars are stamped with their start, so the naive ``start + 15min`` reads correctly all
    session — until the last bar. The feed returns 26 bars for a 25-interval session, the
    extra one stamped at the 15:30 close: that is the closing print, and rendering it as
    "15:30–15:45" quotes a window in which the exchange is shut.
    """
    if start.time() >= SESSION_CLOSE:
        return f"closing print at {start:%H:%M} IST on {start:%a %d %b %Y}"
    end = start + timedelta(minutes=TRIGGER_TIMEFRAME_MINUTES)
    if end.time() > SESSION_CLOSE:
        end = end.replace(hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute)
    return (
        f"{TRIGGER_TIMEFRAME_MINUTES}m bar {start:%H:%M}–{end:%H:%M} IST "
        f"on {start:%a %d %b %Y}"
    )


def execution_lines(candidate: V3Candidate) -> list[tuple[str, str]]:
    """How to actually execute one setup: the order, its validity, and its timing.

    Shared by all three V3 surfaces — the console, the Markdown brief and the published
    page — so none of them can describe the same plan differently.
    """
    plan = candidate.plan
    if plan is None:
        return []

    long_side = candidate.direction == "long"
    rows: list[tuple[str, str]] = []

    # ── the order ─────────────────────────────────────────────────────────────
    if plan.entry_rule == ENTRY_STOP_THROUGH and plan.entry_level:
        through = abs(plan.entry / plan.entry_level - 1) * 100
        rows.append((
            "Entry",
            f"{'Buy' if long_side else 'Sell'}-stop at {plan.entry:,.2f} — "
            f"{through:.2f}% {'above' if long_side else 'below'} the "
            f"{TRIGGER_TIMEFRAME_MINUTES}m flag "
            f"{'high' if long_side else 'low'} at {plan.entry_level:,.2f}. "
            "Resting order: not a trade until price trades through it.",
        ))
    else:
        rows.append((
            "Entry",
            f"{'Buy' if long_side else 'Sell'} at market or limit near {plan.entry:,.2f} — "
            f"the {candidate.setup_name} confirmed on the trigger bar, so that bar's "
            "close is the entry. There is no further level to wait for.",
        ))

    # ── when the quoted plan stops being this trade ───────────────────────────
    # Why the band is lopsided, said outright. It is not a tolerance drawn around the entry
    # — it is solved backwards from the stop, which is why the entry does not sit in its
    # middle. Readers reasonably assumed a symmetric window and read the skew as a bug.
    rows.append((
        "Valid fill",
        f"{plan.entry_min:,.2f} – {plan.entry_max:,.2f}. This is not a tolerance around "
        f"{plan.entry:,.2f}: the stop at {plan.stop:,.2f} is a fixed structural level, and "
        f"the band is every fill that leaves it inside V3's "
        f"{settings.min_stop_pct:.1f}–{settings.v3_max_stop_pct:.1f}% rule — which is why "
        "it is not centred on the entry. A fill outside it is a different trade, not a "
        "worse one.",
    ))

    # ── the bar the geometry was measured on ──────────────────────────────────
    if plan.trigger_bar is not None:
        rows.append((
            "Trigger",
            f"{_bar_span(plan.trigger_bar)} — "
            + (
                "the newest bar in this scan's data."
                if plan.is_live
                else f"{plan.bars_ago} bars before the data ends."
            ),
        ))

    # ── when to act on it ─────────────────────────────────────────────────────
    #
    # A scan run after the close describes a setup for the *next* session, not a trade that
    # has already happened. Without saying so, a trigger stamped "Fri 14 Aug" reads as
    # history — and the reader has no prompt to re-check the price before acting on Monday.
    if plan.trigger_bar is not None:
        triggered = plan.trigger_bar
        # The session is over once its *final* bar has begun — that bar starts one interval
        # before the close, so 15:15 is as much "after hours" as the 15:30 closing print.
        # Testing only for the closing print told a reader to act "this session" on a setup
        # whose session had already ended.
        last_bar_start = (
            datetime.combine(triggered.date(), SESSION_CLOSE)
            - timedelta(minutes=TRIGGER_TIMEFRAME_MINUTES)
        ).time()
        if triggered.time() >= last_bar_start:
            act = _next_session(triggered.date())
            rows.append((
                "Act on",
                f"the next session, {act:%a %d %b %Y}. The quoted entry is "
                f"{triggered:%a %d %b}'s close, so check the opening price is inside the "
                "valid-fill band above before acting — outside it, re-run the scan rather "
                "than adjusting the levels.",
            ))
        else:
            rows.append((
                "Act on",
                "this session, while price is inside the valid-fill band above.",
            ))

    # ── how long the trade is allowed to take ─────────────────────────────────
    if plan.trigger_bar is not None:
        stop_date = _time_stop(plan.trigger_bar.date(), settings.max_holding_sessions)
        rows.append((
            "Exit by",
            f"hold {settings.min_holding_sessions}–{settings.max_holding_sessions} "
            f"sessions; time-stop at the close on {stop_date:%a %d %b %Y} "
            "(weekdays only — an NSE holiday in between pushes it out a day).",
        ))

    rows.append((
        "Target basis",
        f"4R is measured from the fill: {plan.target:,.2f} assumes entry at "
        f"{plan.entry:,.2f} with {plan.risk:,.2f} of risk.",
    ))
    return rows


def render_v3(scan: V3Scan) -> Group:
    parts: list = [
        Text.from_markup(
            f"[bold]Specification V3 — {scan.as_of}[/]\n"
            f"[dim]NIFTY 500 · long + short · 4R minimum · stop "
            f"{settings.min_stop_pct:.1f}–{settings.v3_max_stop_pct:.1f}% · 1–5 sessions[/]\n"
            f"[dim]{scan.regime_note} · data: {scan.tier}[/]\n"
            + (f"[dim]Regime inputs: {scan.regime_detail}[/]\n" if scan.regime_detail else "")
            + (
                f"[dim]Quality floor {scan.threshold:.0f}/100 — {scan.threshold_basis}[/]\n"
                if scan.threshold
                else ""
            )
        )
    ]

    funnel = Table(title="Funnel", box=None, header_style="bold", padding=(0, 2))
    funnel.add_column("Stage")
    funnel.add_column("Count", justify="right")
    funnel.add_row("NIFTY 500 liquid", str(scan.liquid))
    funnel.add_row("showing a V3 setup", str(scan.with_setup))
    funnel.add_row("intraday-evaluated", str(scan.evaluated))
    if scan.catalyst_required:
        funnel.add_row("has a catalyst", str(scan.evaluated - scan.catalyst_rejected))
    funnel.add_row(
        "proved a 60m/120m carry setup",
        str(scan.evaluated - scan.catalyst_rejected - scan.carry_rejected),
    )
    funnel.add_row("cleared the quality floor", str(scan.cleared_floor))
    funnel.add_row("[bold]shown today[/]", f"[bold]{len(scan.trades)}[/]")
    parts.append(funnel)

    parts.append(Text.from_markup(f"\n[dim]{hard_filter_note(scan)}[/]"))

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
                candidate.setup_name,
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
        + "".join(f"\n  [dim]{label}:[/] {value}" for label, value in carry_lines(candidate))
        + "".join(f"\n  [dim]{label}:[/] {value}" for label, value in execution_lines(candidate))
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
    ]
    if scan.regime_detail:
        lines += [f"*Regime inputs: {scan.regime_detail}*", ""]
    if scan.threshold:
        lines += [f"*Quality floor {scan.threshold:.0f}/100 — {scan.threshold_basis}*", ""]
    lines += [
        "> Decision support only. Every row is a setup for your own judgement, never an "
        "instruction to buy or sell. The system places no orders.",
        "",
        "## Funnel",
        "",
        f"*{scan.carry_rejected} of {scan.evaluated} evaluated names failed the 60m/120m "
        "carry test — no carry setup, no trade, whatever the 15m chart offered.*",
        "",
        "| Stage | Count |",
        "| --- | --: |",
        f"| NIFTY 500 liquid | {scan.liquid} |",
        f"| Showing a V3 setup | {scan.with_setup} |",
        f"| Intraday-evaluated | {scan.evaluated} |",
    ]
    if scan.catalyst_required:
        lines.append(f"| Has a catalyst | {scan.evaluated - scan.catalyst_rejected} |")
    lines += [
        f"| Proved a 60m/120m carry setup | "
        f"{scan.evaluated - scan.catalyst_rejected - scan.carry_rejected} |",
        f"| Cleared the quality floor | {scan.cleared_floor} |",
        f"| **Shown today** | **{len(scan.trades)}** |",
        "",
    ]

    # Unconditional: which filters were armed is a fact about the run, not about whether
    # any of them happened to bite. A day with no rejections still made these checks.
    lines += [hard_filter_note(scan), ""]

    if scan.reject_counts:
        lines += [
            "### Which filter was binding", "", "| Hard filter | n |", "| --- | --: |",
        ]
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
                f"{candidate.setup_name} | {candidate.score:.1f} | {plan.entry:,.2f} | "
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
                f"- **Setup:** {candidate.setup_name} — {candidate.setup.note}",
                f"- **Structure:** Weekly {candidate.weekly_note} → Daily "
                f"{candidate.daily_note}"
                + (f" → 60m {candidate.hourly_note}" if candidate.hourly_note else ""),
                f"- **Where I'm wrong:** {plan.invalidation}",
                "",
                "**Why it can carry**",
                "",
                *(f"- **{label}:** {value}" for label, value in carry_lines(candidate)),
                "",
                "**How to execute it**",
                "",
                *(f"- **{label}:** {value}" for label, value in execution_lines(candidate)),
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


def _subset(result, predicate):
    """A result carrying only the trades matching `predicate`, for cohort comparison."""
    clone = type(result)(
        trades=[t for t in result.trades if predicate(t)],
        symbols_tested=result.symbols_tested,
        sessions_spanned=result.sessions_spanned,
    )
    return clone


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

    # ── what the carry gate is worth ──────────────────────────────────────────
    #
    # The same trades, split by the gate's own verdict at entry. Filtering first and
    # measuring second would compare two differently-sampled runs; tagging measures the gate
    # against the trades it actually judged.
    gated = result.gated() if hasattr(result, "gated") else None
    if gated is not None and result.trades:
        held = len(gated.trades)
        table = Table(title="\nWhat the 60m/120m carry gate is worth", header_style="bold",
                      box=None, padding=(0, 2))
        table.add_column("Cohort")
        table.add_column("n", justify="right")
        table.add_column("Win rate", justify="right")
        table.add_column("Mean R", justify="right")
        table.add_column("Net of costs", justify="right")

        def row(name: str, res) -> None:
            if not res.trades:
                table.add_row(name, "0", "—", "—", "—")
                return
            style = "green" if res.net_expectancy_r > 0 else "red"
            rate = f"{res.win_rate:.0f}%" if res.win_rate == res.win_rate else "—"
            table.add_row(
                name, f"{len(res.trades):,}", rate, f"{res.expectancy_r:+.2f}",
                f"[{style}]{res.net_expectancy_r:+.2f}R[/]",
            )

        row("Every trigger (gate off)", result)
        row("Passed the carry gate", gated)

        # Per setup as well as blended. The blended figure is dominated by whichever setup
        # fires most — continuation supplies most triggers and loses on all of them — so a
        # gate can help the setups actually traded while the total barely moves.
        for setup in sorted({t.setup for t in result.trades}):
            subset = _subset(result, lambda t, s=setup: t.setup == s)
            passed = _subset(result, lambda t, s=setup: t.setup == s and t.admitted)
            row(f"  {setup} — all", subset)
            row(f"  {setup} — gated", passed)
        parts.append(table)

        verdict_line = (
            f"\n[dim]The gate kept {held:,} of {len(result.trades):,} triggers "
            f"({held / len(result.trades) * 100:.0f}%).[/]"
        )
        if gated.trades:
            delta = gated.net_expectancy_r - result.net_expectancy_r
            verdict_line += (
                f" [{'green' if delta > 0 else 'red'}]It moves net expectancy by "
                f"{delta:+.2f}R per trade.[/]"
            )
        else:
            verdict_line += (
                " [yellow]Nothing survived it, so the gate is unmeasured rather than "
                "proven — a filter that admits nothing cannot be shown to help.[/]"
            )
        if result.carry_unavailable:
            verdict_line += (
                f"\n[dim]{result.carry_unavailable:,} decisions had no 60m history that far "
                "back and were failed closed, the same as the live scan does.[/]"
            )
        parts.append(Text.from_markup(verdict_line))

    # ── the survivors, one by one ─────────────────────────────────────────────
    #
    # With a deliberately selective gate the useful question is not "what is the win rate
    # of 40 trades" — it is "are these the setups I wanted?". That is answered by reading
    # them, so every gated trade is listed while the list stays readable.
    survivors = [t for t in result.trades if getattr(t, "admitted", False)]
    if survivors:
        shown = sorted(survivors, key=lambda x: -x.realised_r)
        title = (
            "\nEvery trade the carry gate admitted" if len(shown) <= 40
            else f"\nThe 20 best and 20 worst of {len(shown)} gated trades"
        )
        if len(shown) > 40:
            shown = shown[:20] + shown[-20:]
        detail = Table(title=title, header_style="bold", box=None, padding=(0, 2))
        for column, justify in (
            ("Symbol", "left"), ("Dir", "left"), ("Setup", "left"), ("Entered", "left"),
            ("Carry", "right"), ("Stop%", "right"), ("Outcome", "left"),
            ("R", "right"), ("MAE", "right"), ("MFE", "right"), ("Bars", "right"),
        ):
            detail.add_column(column, justify=justify)
        for t in shown:
            style = "green" if t.realised_r > 0 else "red"
            detail.add_row(
                t.symbol, t.direction, t.setup, f"{t.entered_at:%d %b %H:%M}",
                f"{t.carry_score:.0f}", f"{t.stop_pct:.2f}%", t.outcome,
                f"[{style}]{t.realised_r:+.2f}[/]",
                f"{t.mae_r:+.2f}", f"{t.mfe_r:+.2f}", str(t.bars_held),
            )
        parts.append(detail)
        parts.append(Text.from_markup(
            "\n[dim]MAE is the worst excursion against the position before it resolved, MFE "
            "the best in favour — together they say whether a trade went cleanly to target "
            "or bled first. A gate meant to prove carry should produce high MFE and shallow "
            "MAE, not merely a better win rate.[/]"
        ))

    parts.append(
        Text.from_markup(
            "\n[dim]Entries and stops come from the live engine, resolved on 15-minute bars. "
            "A bar touching both stop and target counts as a loss, since intraday sequence "
            "is unknown. Intraday history is limited to roughly 60-80 days, so treat the "
            "buckets as indicative rather than calibrated.[/]"
        )
    )
    return Group(*parts)
