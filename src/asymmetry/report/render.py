"""Terminal rendering for the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..models import RegimeReport, ScanResult

if TYPE_CHECKING:
    from ..backtest import BacktestReport

_SCORE_STYLE = {1: "green", 0: "yellow", -1: "red"}
_SCORE_MARK = {1: "+1", 0: " 0", -1: "-1"}


def render_regime(report: RegimeReport) -> Panel:
    table = Table(box=None, padding=(0, 2))
    table.add_column("Input")
    table.add_column("Score", justify="center")
    table.add_column("Detail")

    for component in report.components:
        table.add_row(
            component.name,
            f"[{_SCORE_STYLE[component.score]}]{_SCORE_MARK[component.score]}[/]",
            component.detail,
        )

    header = Text.from_markup(
        f"{report.verdict.emoji}  [bold]{report.verdict.headline}[/]   "
        f"(score {report.total:+d} of ±5)"
    )
    footer = Text.from_markup(
        f"\n[dim]as of {report.as_of} · data: {report.tier}[/]\n"
        "[dim]Gamma is regime context, never an entry signal.[/]"
    )
    return Panel(Group(header, Text(""), table, footer), title="Engine 1 — Market regime")


def render_scan(result: ScanResult) -> Group:
    parts: list = [render_regime(result.regime), Text("")]

    if not result.candidates:
        parts.append(
            Text.from_markup(
                "[yellow]No candidate cleared the R:R gate today.[/]\n"
                "[dim]That is a valid outcome — the gate exists to say no.[/]"
            )
        )
    else:
        # Factor sub-scores are collapsed into one RS/Vol/St/Cat column: at terminal width
        # the wide form truncated every price to "1,…" and became unreadable.
        table = Table(title=f"Shortlist — top {len(result.candidates)}", header_style="bold")
        table.add_column("#", justify="right", width=2)
        table.add_column("Symbol", no_wrap=True)
        table.add_column("Score", justify="right")
        table.add_column("RS/Vol/St/Cat", justify="center", no_wrap=True)
        table.add_column("Entry", justify="right", no_wrap=True)
        table.add_column("Stop", justify="right", no_wrap=True)
        table.add_column("Target", justify="right", no_wrap=True)
        table.add_column("R:R", justify="right", no_wrap=True)
        table.add_column("Qty", justify="right")

        for i, cand in enumerate(result.candidates, 1):
            plan = cand.plan
            f = cand.factors
            rr_style = "green" if plan and plan.reward_risk >= 3 else "cyan"
            table.add_row(
                str(i),
                f"[bold]{cand.symbol}[/]",
                f"{cand.total_score:.1f}",
                f"{f.relative_strength:.0f}/{f.volume:.0f}/"
                f"{f.price_structure:.0f}/{f.catalyst:.0f}",
                f"{plan.entry:,.2f}" if plan else "-",
                f"{plan.stop:,.2f}" if plan else "-",
                f"{plan.target:,.2f}" if plan else "-",
                f"[{rr_style}]1:{plan.reward_risk:.1f}[/]" if plan else "-",
                str(plan.quantity) if plan else "-",
            )
        parts.append(table)

        notes = [c for c in result.candidates if c.catalyst_note or c.macro.reliable]
        if notes:
            detail = Table(title="Why", box=None, header_style="bold", padding=(0, 2))
            detail.add_column("Symbol")
            detail.add_column("Catalyst / macro")
            for cand in notes:
                bits = []
                if cand.catalyst_note:
                    bits.append(cand.catalyst_note)
                if cand.macro.reliable and cand.macro.gap_pct is not None:
                    bits.append(
                        f"macro gap {cand.macro.gap_pct:+.1f}% (R²={cand.macro.r_squared:.2f})"
                    )
                detail.add_row(cand.symbol, " · ".join(bits))
            parts.extend([Text(""), detail])

    if result.watchlist:
        watch = Table(title="Watch — setup valid but below the R:R gate", box=None, padding=(0, 2))
        watch.add_column("Symbol")
        watch.add_column("Score", justify="right")
        watch.add_column("Reason")
        for cand in result.watchlist:
            watch.add_row(cand.symbol, f"{cand.total_score:.1f}", cand.rejected_reason)
        parts.extend([Text(""), watch])

    parts.append(
        Text.from_markup(
            f"\n[dim]{result.liquid_size}/{result.universe_size} passed the liquidity gate "
            f"· data: {result.tier}[/]\n"
            "[dim]Decision support only — you place every order yourself.[/]"
        )
    )
    return Group(*parts)


def render_backtest(report: "BacktestReport", *, full: bool = True) -> Group:
    """Render walk-forward results.

    Deliberately blunt: the spread between the best and worst score bucket is printed as a
    single number, because that is the whole question. A flat spread means the ranking is
    decoration and should be reported as such rather than buried under favourable-looking
    detail.
    """
    parts: list = []

    if not report.days:
        return Group(
            Text.from_markup(
                "[red]No days evaluated.[/] Widen the window, or backfill more history."
            )
        )

    scope = "full universe" if full else "shortlist only"
    parts.append(
        Text.from_markup(
            f"[bold]Walk-forward result[/] · {len(report.days)} sample days · "
            f"{report.horizon}-day forward horizon · {scope}\n"
            f"[dim]{report.days[0].as_of} → {report.days[-1].as_of}. "
            "Returns are excess of the same-day universe mean, so a rising market is not "
            "mistaken for skill.[/]\n"
        )
    )

    table_data = report.rank_table()
    if not table_data.empty:
        table = Table(title="Forward excess return by score bucket", header_style="bold")
        table.add_column("Bucket")
        table.add_column("Mean excess", justify="right")
        table.add_column("Median", justify="right")
        table.add_column("Hit rate", justify="right")
        table.add_column("n", justify="right")

        for row in table_data.itertuples():
            style = "green" if row.mean_excess > 0 else "red"
            table.add_row(
                str(row.bucket),
                f"[{style}]{row.mean_excess:+.2f}%[/]",
                f"{row.median_excess:+.2f}%",
                f"{row.hit_rate:.0f}%",
                f"{int(row.n):,}",
            )
        parts.append(table)

        top, bottom = table_data.iloc[-1], table_data.iloc[0]
        mean_spread = top["mean_excess"] - bottom["mean_excess"]
        median_spread = top["median_excess"] - bottom["median_excess"]
        hit_spread = top["hit_rate"] - bottom["hit_rate"]

        # Equity returns are fat-tailed, so a handful of outliers dominate the mean and it
        # is the noisiest of the three. Median and hit rate describe what happens to the
        # typical name, which is what a stock picker actually experiences.
        monotonic_median = list(table_data["median_excess"]) == sorted(
            table_data["median_excess"]
        )
        monotonic_hit = list(table_data["hit_rate"]) == sorted(table_data["hit_rate"])

        if median_spread > 0.4 and hit_spread > 3 and (monotonic_median or monotonic_hit):
            verdict = (
                "[green]consistent signal — the ranking orders the typical stock "
                "correctly[/]"
            )
        elif median_spread > 0 and hit_spread > 0:
            verdict = "[yellow]weak but positive — a screen with mild edge, not alpha[/]"
        else:
            verdict = "[red]no signal: the ranking is not predictive over this window[/]"

        parts.append(
            Text.from_markup(
                f"\n[bold]Top-minus-bottom over {report.horizon} days[/] — "
                f"mean {mean_spread:+.2f}%, median [bold]{median_spread:+.2f}%[/], "
                f"hit rate [bold]{hit_spread:+.0f}pp[/]\n"
                f"{verdict}"
            )
        )
        if monotonic_median or monotonic_hit:
            which = " and ".join(
                x for x, ok in (("median", monotonic_median), ("hit rate", monotonic_hit)) if ok
            )
            parts.append(
                Text.from_markup(
                    f"[dim]Bucket {which} rises monotonically across all "
                    f"{len(table_data)} buckets — that ordering is harder to get by chance "
                    "than any single bucket's number.[/]"
                )
            )

    correlation = report.rank_correlation()
    if correlation == correlation:  # not NaN
        parts.append(
            Text.from_markup(
                f"\nMean daily rank correlation (score vs forward return): "
                f"[bold]{correlation:+.3f}[/]\n"
                "[dim]Above roughly +0.05 sustained is meaningful for a cross-sectional "
                "equity signal; near zero means no relationship.[/]"
            )
        )

    regime_table = report.by_regime()
    if not regime_table.empty and len(regime_table) > 1:
        table = Table(title="\nDoes the regime verdict condition outcomes?", header_style="bold")
        table.add_column("Regime")
        table.add_column("Days", justify="right")
        table.add_column("Top-10 excess", justify="right")
        table.add_column("Universe return", justify="right")
        for row in regime_table.itertuples():
            table.add_row(
                str(row.regime),
                str(int(row.days)),
                f"{row.mean_top10_excess:+.2f}%",
                f"{row.mean_universe_return:+.2f}%",
            )
        parts.append(table)
        parts.append(
            Text.from_markup(
                "[dim]If these rows look alike, Engine 1 is costing you selectivity "
                "without buying anything.[/]"
            )
        )

    stats = report.plan_stats()
    if stats:
        table = Table(title="\nEmitted trade plans", header_style="bold")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Plans emitted", f"{stats['emitted']:,}")
        table.add_row("Entry triggered", f"{stats['triggered']:,}")
        table.add_row("Resolved (hit stop or target)", f"{stats['resolved']:,}")
        table.add_row("Wins / losses", f"{stats['wins']} / {stats['losses']}")
        if stats["win_rate"] == stats["win_rate"]:
            table.add_row("Win rate", f"{stats['win_rate']:.0f}%")
            table.add_row("Expectancy per trade", f"{stats['expectancy_r']:+.2f}R")
            table.add_row("Total", f"{stats['total_r']:+.1f}R")
        parts.append(table)
        parts.append(
            Text.from_markup(
                "[dim]A bar touching both stop and target is scored a loss — intraday "
                "sequence is unknown, and assuming the win is how backtests flatter "
                "themselves.[/]"
            )
        )

    return Group(*parts)


def render_journal(stats: dict, days: int) -> Group:
    """System calls versus your own decisions.

    The comparison between 'taken' and 'skipped' is the point: if the calls you skipped
    outperformed the ones you took, your filtering is costing you, and that is worth
    knowing early rather than after a year of it.
    """
    table = Table(title=f"Decision journal — last {days} days", header_style="bold")
    table.add_column("Set")
    table.add_column("n", justify="right")
    table.add_column("Win rate", justify="right")
    table.add_column("Mean R", justify="right")
    table.add_column("Total R", justify="right")

    labels = {
        "all_calls": "All system calls",
        "taken": "You took",
        "skipped": "You skipped",
    }
    for key, label in labels.items():
        row = stats.get(key, {})
        if not row or row.get("n", 0) == 0:
            table.add_row(label, "0", "—", "—", "—")
            continue
        style = "green" if row["mean_r"] > 0 else "red"
        table.add_row(
            label,
            str(row["n"]),
            f"{row['win_rate']:.0f}%",
            f"[{style}]{row['mean_r']:+.2f}R[/]",
            f"[{style}]{row['total_r']:+.1f}R[/]",
        )

    parts: list = [table]

    taken, skipped = stats.get("taken", {}), stats.get("skipped", {})
    if taken.get("n") and skipped.get("n"):
        delta = taken["mean_r"] - skipped["mean_r"]
        if delta > 0.1:
            verdict = "[green]your filtering is adding value — you take the better calls[/]"
        elif delta < -0.1:
            verdict = (
                "[red]the calls you skipped did better than the ones you took — "
                "your filtering is subtracting value[/]"
            )
        else:
            verdict = "[yellow]no measurable difference between what you take and skip[/]"
        parts.append(Text.from_markup(f"\n{verdict}"))

    logged, total = stats.get("logged", 0), stats.get("total_calls", 0)
    if total:
        parts.append(
            Text.from_markup(
                f"\n[dim]{logged}/{total} calls have a logged action. "
                "Unlogged calls still count in 'all system calls', so the comparison only "
                "gets sharper as you log more.[/]"
            )
        )
    return Group(*parts)
