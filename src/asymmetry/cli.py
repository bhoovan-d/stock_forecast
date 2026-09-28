"""Command line interface."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from .config import settings

app = typer.Typer(
    add_completion=False,
    help="Asymmetry Engine — Indian equities catalyst + regime scanner (decision support only).",
)

# Windows consoles default to cp1252, which cannot encode the regime emoji or the ₹/² we
# use throughout. Force UTF-8 rather than degrading the output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

console = Console()

logger.remove()
logger.add(sys.stderr, level="INFO", format="<dim>{time:HH:mm:ss}</dim> {message}")


@app.command()
def doctor() -> None:
    """Probe every data source and report reachability and the active tier."""
    from .data import DataTier, MarketData
    from .data import nse_archive, yahoo

    table = Table(title="Data source health", header_style="bold")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Detail")

    def row(name: str, ok: bool, detail: str) -> None:
        table.add_row(name, "[green]OK[/]" if ok else "[red]FAIL[/]", detail)

    # NSE archives — the backbone.
    universe = nse_archive.fetch_index_constituents(settings.universe_index)
    row(
        "NSE universe",
        universe is not None,
        f"{len(universe)} constituents, {universe['sector'].nunique()} sectors"
        if universe is not None
        else "unreachable",
    )

    day = nse_archive.last_trading_day()
    row("NSE trading day", day is not None, str(day) if day else "no bhavcopy in 10 days")

    if day:
        cm = nse_archive.fetch_cm_bhavcopy(day)
        row("NSE cash bhavcopy", cm is not None, f"{len(cm)} EQ rows" if cm is not None else "-")

        chain = nse_archive.fetch_option_chain(day, "NIFTY")
        detail = "-"
        if chain is not None:
            near = chain[chain["expiry"] == chain["expiry"].min()]
            detail = f"{len(chain)} rows, {chain['expiry'].nunique()} expiries, near={len(near)}"
        row("NSE option chain (gamma)", chain is not None, detail)

        deliv = nse_archive.fetch_delivery(day)
        row("NSE delivery (MTO)", deliv is not None, f"{len(deliv)} rows" if deliv is not None else "-")

    # Yahoo — intraday and macro.
    nifty = yahoo.fetch_chart("^NSEI", range_="5d", interval="5m")
    row(
        "Yahoo intraday",
        nifty is not None,
        f"{len(nifty)} 5m bars" if nifty is not None else "throttled/unreachable",
    )

    macro = yahoo.fetch_macro(range_="1mo")
    row(
        "Yahoo macro panel",
        len(macro) > 0,
        f"{len(macro)}/{len(yahoo.MACRO_SYMBOLS)} factors: {', '.join(sorted(macro))}",
    )

    # Live tier.
    data = MarketData()
    row(
        "Upstox (live)",
        data.live_available,
        "authenticated"
        if data.live_available
        else "no/expired token — degrading to archive+delayed",
    )

    console.print(table)

    tier = DataTier.LIVE if data.live_available else DataTier.ARCHIVE
    style = "green" if tier == DataTier.LIVE else "yellow"
    console.print(f"\nActive tier: [{style}]{tier.label}[/]")
    if tier != DataTier.LIVE:
        console.print("[dim]Run `asymmetry auth` for token refresh instructions.[/]")


@app.command()
def auth(
    manual: bool = typer.Option(
        False, "--manual", help="Print the steps instead of running the browser flow."
    ),
) -> None:
    """Log in to Upstox and store today's access token.

    The whole flow runs locally: your browser, a loopback redirect, and a direct call from
    this machine to Upstox. No credential is sent anywhere else.
    """
    from .config import settings as cfg
    from .data import MarketData, upstox
    from .data.upstox_auth import login

    console.print("[bold]Upstox access token[/]")
    console.print(
        "[dim]Tokens expire daily around 03:30 IST - this is a routine refresh.[/]\n"
    )

    if not cfg.upstox_api_key or not cfg.upstox_api_secret:
        console.print("[yellow]No app credentials yet. One-time setup:[/]\n")
        console.print("  1. Open [cyan]https://account.upstox.com/developer/apps[/]")
        console.print("  2. Create an app. Set its Redirect URL to exactly:")
        console.print(f"     [cyan]{cfg.upstox_redirect_uri}[/]")
        console.print("  3. Copy the API key and secret into [bold].env[/]:\n")
        console.print("     UPSTOX_API_KEY=your_key")
        console.print("     UPSTOX_API_SECRET=your_secret\n")
        console.print("  4. Run [bold]asymmetry auth[/] again.")
        return

    if manual:
        console.print("1. Open this URL and log in:\n")
        console.print(f"   [cyan]{upstox.auth_url()}[/]\n")
        console.print("2. After the redirect, copy the `code` query parameter.")
        console.print("3. POST it to /v2/login/authorization/token with your key and secret.")
        console.print("4. Put the returned token in .env as UPSTOX_ACCESS_TOKEN.\n")
        console.print(
            "[dim]Running `asymmetry auth` without --manual does all of this for you.[/]"
        )
        return

    if login() is None:
        console.print("\n[red]Login failed.[/] See the messages above.")
        raise typer.Exit(1)

    console.print("\n[green]Token stored in .env.[/] Verifying...\n")
    if MarketData().live_available:
        console.print("[green]Live tier active.[/] Briefs will now use real-time data.")
    else:
        console.print(
            "[yellow]Token stored, but the API did not accept it.[/] "
            "Check the app's Redirect URL matches .env exactly."
        )

@app.command()
def backfill(
    days: int = typer.Option(400, help="Calendar days of history to pull."),
) -> None:
    """Download and store EOD history (bhavcopy + delivery) into SQLite."""
    from .storage import backfill_history

    end = date.today()
    start = end - timedelta(days=days)
    stored = backfill_history(start, end)
    console.print(f"[green]Stored {stored:,} daily bars.[/]")


@app.command("freeze-universe")
def freeze_universe(
    on: str = typer.Option(..., "--date", help="Snapshot date (YYYY-MM-DD)."),
    out: Path = typer.Option(..., "--out", help="JSON snapshot path."),
    lookback: int = typer.Option(20, help="Trading sessions used by the liquidity gate."),
) -> None:
    """Freeze a point-in-time universe from stored bhavcopy history."""
    from .universe_snapshot import freeze, save

    target = date.fromisoformat(on)
    symbols = freeze(target, lookback=lookback)
    save(symbols, out)
    console.print(f"[green]Frozen:[/] {len(symbols)} symbols as of {target} → {out}")


@app.command()
def regime(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
) -> None:
    """Engine 1 — is today a day to be aggressive?"""
    from .engines.regime import assess_regime
    from .report.render import render_regime

    target = date.fromisoformat(on) if on else None
    console.print(render_regime(assess_regime(target)))


@app.command()
def scan(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    top: int = typer.Option(10, help="Shortlist size."),
    no_catalyst: bool = typer.Option(False, "--no-catalyst", help="Skip the LLM catalyst pass."),
) -> None:
    """Engines 3+5 — rank the universe and build trade plans."""
    from .engines.selection import run_selection
    from .report.render import render_scan

    target = date.fromisoformat(on) if on else None
    result = run_selection(target, top_n=top, use_catalyst=not no_catalyst)
    console.print(render_scan(result))


journal_app = typer.Typer(help="Decision journal — what the system said vs what you did.")
app.add_typer(journal_app, name="journal")


@journal_app.command("log")
def journal_log(
    symbol: str = typer.Argument(..., help="Ticker, e.g. RELIANCE"),
    action: str = typer.Argument(..., help="taken | skipped"),
    price: float = typer.Option(None, "--price", help="Your actual fill price."),
    qty: int = typer.Option(None, "--qty", help="Your actual quantity."),
    note: str = typer.Option("", "--note", help="Why you did or didn't."),
) -> None:
    """Record what you actually did about a call."""
    from .journal import log_action

    if action not in ("taken", "skipped"):
        console.print("[red]action must be 'taken' or 'skipped'[/]")
        raise typer.Exit(1)
    if log_action(symbol.upper(), action, actual_entry=price, actual_quantity=qty, note=note):
        console.print(f"[green]Logged:[/] {symbol.upper()} {action}")
    else:
        console.print(f"[yellow]No recent call found for {symbol.upper()}.[/]")


@journal_app.command("settle")
def journal_settle(
    horizon: int = typer.Option(10, help="Trading days to allow before marking expired."),
) -> None:
    """Mark open calls against stored prices."""
    from .journal import settle

    console.print(f"[green]Settled {settle(horizon)} entries.[/]")


@journal_app.command("review")
def journal_review(days: int = typer.Option(90, help="Look-back window.")) -> None:
    """System calls versus your actual decisions."""
    from .journal import performance
    from .report.render import render_journal

    stats = performance(days)
    if not stats:
        console.print("[yellow]Nothing recorded yet. Generate a brief first.[/]")
        return
    console.print(render_journal(stats, days))


@app.command()
def spec(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    evaluate: int = typer.Option(
        30, help="How many pre-ranked names get the full multi-timeframe pass."
    ),
    refresh: bool = typer.Option(
        False, "--refresh/--no-refresh", help="Re-score news and filings via LLM."
    ),
    refresh_rates: bool = typer.Option(
        False, "--refresh-rates", help="Recompute historical base rates (~45s)."
    ),
) -> None:
    """Engineer Brief scan: 4R minimum, 1.4% max stop, 1-5 session horizon."""
    from .engines.spec_engine import run_spec_scan
    from .report.spec_report import write_spec_brief

    target = date.fromisoformat(on) if on else None
    scan = run_spec_scan(
        target, max_evaluate=evaluate, refresh_catalysts=refresh,
        refresh_rates=refresh_rates,
    )

    table = Table(title=f"Specification scan — {scan.as_of}", header_style="bold")
    table.add_column("Verdict")
    table.add_column("Count", justify="right")
    table.add_row("[green]TRADE[/]", str(len(scan.trades)))
    table.add_row("[yellow]WATCH[/]", str(len(scan.watch)))
    table.add_row("[red]REJECT[/]", str(scan.total_rejected))
    console.print(table)

    if scan.reject_counts:
        reasons = Table(title="Why candidates failed", header_style="bold", box=None)
        reasons.add_column("Reason")
        reasons.add_column("n", justify="right")
        for reason, count in sorted(scan.reject_counts.items(), key=lambda kv: -kv[1]):
            reasons.add_row(reason, str(count))
        console.print(reasons)

    for candidate in scan.trades:
        plan = candidate.plan
        console.print(
            f"\n[bold green]TRADE[/] [bold]{candidate.symbol}[/] — {candidate.why_now}\n"
            f"  entry {plan.entry:,.2f} · stop {plan.stop:,.2f} ({plan.stop_pct:.2f}%) · "
            f"target {plan.target_4r:,.2f} (+{plan.target_pct:.1f}%)\n"
            f"  P(5d)={candidate.probability.p_5d:.0%} · "
            f"EV={candidate.expected_value.ev_r:+.2f}R · qty {plan.quantity}"
        )

    if not scan.trades:
        console.print(
            "\n[yellow]Nothing qualified.[/] "
            f"[dim]A {settings.min_reward_risk:.0f}R target with a "
            f"{settings.max_stop_pct:.1f}% stop needs a "
            f"{settings.min_reward_risk * settings.max_stop_pct:.1f}% move in five "
            "sessions — most days produce nothing, by design.[/]"
        )

    console.print(f"\n[green]Written:[/] {write_spec_brief(scan)}")


@app.command()
def v3(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    limit: int = typer.Option(
        0, help="Cap the intraday pass. 0 = evaluate every candidate (recommended)."
    ),
    refresh: bool = typer.Option(
        False, "--refresh/--no-refresh", help="Re-score news and filings via LLM."
    ),
    min_score: float = typer.Option(
        0.0, help="Legacy research input; composite scores cannot qualify or reject."
    ),
    per_day: int = typer.Option(
        1, help="Maximum geometry-qualified plans shown; frequency is never a target."
    ),
    setup: list[str] = typer.Option(
        # Defaults to the two setups measured above break-even, which is what CI publishes.
        # It used to default to *all* of them, so `asymmetry v3` followed by `asymmetry
        # site` could put a high-tight flag on the public page — the one setup measured to
        # lose money even after gating, and the one README says must not reach the site.
        # CI was the only thing enforcing that, via flags a local run had no reason to pass.
        # Ask for the flag explicitly (`--setup continuation`) to look at it.
        ["reclaim"], "--setup",
        help="Production research is restricted to the long reclaim setup.",
    ),
    require_catalyst: bool = typer.Option(
        None, "--require-catalyst/--no-require-catalyst",
        help="Research comparison only; catalysts cannot qualify or reject the fixed rule.",
    ),
) -> None:
    """Reliability scan: long NSE cash reclaims, fixed 2R, paper evidence only."""
    from .engines.v3_scan import run_v3_scan
    from .report.v3_report import render_v3, write_v3_brief
    from .report.v3_website import write_v3_html

    target = date.fromisoformat(on) if on else None
    scan = run_v3_scan(
        target, max_intraday=limit, refresh_catalysts=refresh, min_score=min_score,
        max_per_day=per_day, setups=tuple(setup) if setup else None,
        require_catalyst=require_catalyst,
    )
    console.print(render_v3(scan))
    # Both artefacts, every run. `site` only copies the HTML it finds in the brief
    # directory, so skipping this leaves the published V3 page frozen at whatever scan
    # last wrote one while the Markdown moves on.
    console.print(f"\n[green]Written:[/] {write_v3_brief(scan)}")
    console.print(f"[green]Written:[/] {write_v3_html(scan)}")
    console.print("[dim]Research scan only; v3-monitor creates immutable paper evidence.[/]")


def _last_session():
    from .data import nse_archive

    return nse_archive.last_trading_day() or date.today()


@app.command("pullback")
def pullback(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    limit: int = typer.Option(0, help="Cap the universe. 0 = all NIFTY 200 names."),
    entry_tf: str = typer.Option("5m", "--entry-tf", help="Entry timeframe. 3m is unavailable."),
    max_risk: float = typer.Option(0.7, "--max-risk", help="Max stop distance, percent."),
    min_risk: float = typer.Option(
        0.0, "--min-risk",
        help="Min stop distance, percent. 0 = as specified; the measurement suggests a floor.",
    ),
    rr: float = typer.Option(3.0, "--rr", help="Reward-to-risk target."),
) -> None:
    """Intraday HMA/Bollinger pullback scan across the NIFTY 200.

    Separate from the V3 engine: different universe, timeframe, geometry and risk cap.
    Its edge is not established — see docs/spec-hma-pullback.md.
    """
    from .engines.hma_pullback import PullbackSettings, scan_pullback
    from .report.pullback_report import render_scan, write_brief

    cfg = PullbackSettings(
        entry_interval=entry_tf, max_risk_pct=max_risk, min_risk_pct=min_risk,
        reward_risk=rr,
    )
    target = date.fromisoformat(on) if on else None
    signals = scan_pullback(target, max_symbols=limit, cfg=cfg)
    as_of = str(target) if target else str(_last_session())
    console.print(render_scan(signals, as_of, cfg))
    console.print(f"[green]Written:[/] {write_brief(signals, as_of, cfg)}")


@app.command("pullback-backtest")
def pullback_backtest(
    symbols: int = typer.Option(40, help="How many NIFTY 200 names to replay. 0 = all."),
    entry_tf: str = typer.Option("5m", "--entry-tf", help="Entry timeframe. 3m is unavailable."),
    max_risk: float = typer.Option(0.7, "--max-risk", help="Max stop distance, percent."),
    min_risk: float = typer.Option(
        0.0, "--min-risk",
        help="Min stop distance, percent. 0 = as specified; the measurement suggests a floor.",
    ),
    rr: float = typer.Option(3.0, "--rr", help="Reward-to-risk target."),
) -> None:
    """Replay the intraday pullback strategy over the available 30m/5m history."""
    from .engines.hma_pullback import PullbackSettings, backtest_pullback
    from .report.pullback_report import render_backtest

    cfg = PullbackSettings(
        entry_interval=entry_tf, max_risk_pct=max_risk, min_risk_pct=min_risk,
        reward_risk=rr,
    )
    result = backtest_pullback(max_symbols=symbols, cfg=cfg)
    console.print(render_backtest(result, cfg))


# ── The kill-zone trident ─────────────────────────────────────────────────────
#
# Five commands, one strategy. They share `_trident_cfg` so a threshold cannot mean one
# thing in the scan and another in the backtest — the same reason the report surfaces share
# their line builders.


def _trident_cfg(
    *,
    interval: str = "30m",
    killzone_start: str = "09:15",
    killzone_end: str = "12:45",
    rr: float = 20.0,
    hold: int = 60,
    strong_daily: bool = True,
    feasible_only: bool = False,
    min_turnover_cr: float = 25.0,
    max_spread_bps: float = 25.0,
    require_spread: bool = False,
    max_alerts: int = 5,
    sweep_candles: int = 3,
    tag_sweeps: bool = True,
):
    """Build a `TridentSettings` from CLI options.

    Every trident command routes through here. The alternative — each command constructing
    its own — is how two surfaces end up disagreeing about what the kill zone is, and this
    strategy already has three cost constants and three stop rules in the codebase that must
    not be confused with each other.
    """
    from datetime import time as _time

    from .engines.trident import TridentSettings

    def _clock(text: str) -> _time:
        hour, minute = (int(part) for part in text.split(":"))
        return _time(hour, minute)

    return TridentSettings(
        anchor_interval=interval,
        killzone_start=_clock(killzone_start),
        killzone_end=_clock(killzone_end),
        reward_risk=rr,
        max_hold_sessions=hold,
        require_strong_daily=strong_daily,
        require_feasible_target=feasible_only,
        min_median_turnover_inr=min_turnover_cr * 1e7,
        max_spread_bps=max_spread_bps,
        require_spread_estimate=require_spread,
        max_alerts_per_session=max_alerts,
        sweep_max_candles=sweep_candles,
        tag_liquidity_sweep=tag_sweeps,
    )


@app.command("trident")
def trident(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    limit: int = typer.Option(0, help="Cap the universe AFTER the liquidity floor. 0 = all."),
    universe: str = typer.Option(
        "nifty500", "--universe",
        help="nifty50 | nifty100 | nifty200 | nifty500. The 500 is the default now; the "
             "liquidity floor is what makes its tail safe to scan.",
    ),
    interval: str = typer.Option(
        "30m", "--interval",
        help="Entry timeframe. 30m is the source's. 5m is a parallel track, not a "
             "replacement — run both and compare.",
    ),
    killzone_start: str = typer.Option("09:15", "--killzone-start", help="Window start, HH:MM."),
    killzone_end: str = typer.Option(
        "12:45", "--killzone-end",
        help="End of the entry window, HH:MM. The NSE mapping of London 06:30 NY is a "
             "structural analogy, not a measured choice — see `trident-sweep`.",
    ),
    rr: float = typer.Option(20.0, "--rr", help="Reward-to-risk target."),
    min_turnover: float = typer.Option(
        25.0, "--min-turnover-cr",
        help="Median 20-day traded value floor, in ₹ crore. Runs before anything else.",
    ),
    max_spread: float = typer.Option(
        25.0, "--max-spread-bps",
        help="Estimated effective spread cap, basis points. Inert unless --require-spread.",
    ),
    require_spread: bool = typer.Option(
        False, "--require-spread/--no-require-spread",
        help="Arm the estimated-spread cap. OFF by default for a measured reason: the "
             "Corwin-Schultz estimate correlates -0.15 with turnover and +0.31 with daily "
             "range on NSE data, and at 25bp refuses 40 of the NIFTY 50.",
    ),
    max_alerts: int = typer.Option(5, "--max-alerts", help="Alert budget, ranked by net R."),
    liquidity: bool = typer.Option(
        True, "--liquidity/--no-liquidity",
        help="--no-liquidity scans the raw index. Expect data-hole gaps from the tail.",
    ),
    strong_daily: bool = typer.Option(
        True, "--strong-daily/--any-daily",
        help="Require the daily candle to be printing 'green'. That indicator is a "
             "reconstruction of an unnamed third-party script, so it can be switched off.",
    ),
    feasible_only: bool = typer.Option(
        False, "--feasible-only",
        help="Refuse setups whose 20R target exceeds ATR-implied travel. Not in the source.",
    ),
) -> None:
    """Kill-zone fair-value-gap reclaim ("trident") scan, with the full funnel.

    Separate from V3 and from the HMA pullback: different universe, geometry, holding period
    and cost constant. Its edge is untested — see docs/spec-trident.md.
    """
    from .engines.trident import scan_trident_full
    from .report.trident_report import render_scan_full, write_brief

    cfg = _trident_cfg(
        interval=interval, killzone_start=killzone_start, killzone_end=killzone_end,
        rr=rr, strong_daily=strong_daily, feasible_only=feasible_only,
        min_turnover_cr=min_turnover, max_spread_bps=max_spread, max_alerts=max_alerts,
        require_spread=require_spread,
    )
    target = date.fromisoformat(on) if on else None
    scan = scan_trident_full(
        target, max_symbols=limit, universe=universe, cfg=cfg, apply_liquidity=liquidity
    )
    as_of = str(scan.as_of)
    console.print(render_scan_full(scan, as_of, cfg))
    console.print(f"[green]Written:[/] {write_brief(scan.signals, as_of, cfg)}")


@app.command("trident-monitor")
def trident_monitor(
    limit: int = typer.Option(
        60, help="Cap the universe AFTER the liquidity floor. A pass costs ~1.2s a symbol."
    ),
    universe: str = typer.Option("nifty500", "--universe"),
    interval: str = typer.Option("30m", "--interval", help="Entry timeframe: 30m or 5m."),
    killzone_start: str = typer.Option("09:15", "--killzone-start"),
    killzone_end: str = typer.Option("12:45", "--killzone-end"),
    rr: float = typer.Option(20.0, "--rr"),
    max_alerts: int = typer.Option(5, "--max-alerts"),
    once: bool = typer.Option(
        False, "--once",
        help="Run a single pass against the latest closed candle and exit. This is the form "
             "to use from cron or from the UI; the loop form is for watching a session live.",
    ),
    strong_daily: bool = typer.Option(True, "--strong-daily/--any-daily"),
) -> None:
    """Monitor the trident intraday, alerting at each gate on closed candles only.

    Gate 4 is the get-ready — a doji has closed back above the gap's 50% — and gate 5 is the
    trigger, one full candle later. They are alerted separately because that gap is the only
    operational reason to run this live instead of after the close.

    Every candidate that fails is logged with the gate it died at and the numbers that killed
    it. At this sample size that log is worth more than the winners.
    """
    from .engines.trident_live import monitor, persist, scan_once, session_window_note
    from .report.trident_report import render_live

    cfg = _trident_cfg(
        interval=interval, killzone_start=killzone_start, killzone_end=killzone_end,
        rr=rr, strong_daily=strong_daily, max_alerts=max_alerts,
    )
    console.print(f"[dim]{session_window_note(cfg)}[/]")

    if once:
        scan = scan_once(cfg, universe=universe, max_symbols=limit)
        alerts, failures = persist(scan)
        console.print(render_live(scan, cfg))
        console.print(
            f"[dim]Logged {alerts} new alert(s) and {failures} failure(s).[/]"
        )
        return

    monitor(
        cfg, universe=universe, max_symbols=limit,
        on_scan=lambda scan: console.print(render_live(scan, cfg)),
    )


@app.command("trident-backtest")
def trident_backtest(
    symbols: int = typer.Option(40, help="How many names to replay, after the floor. 0 = all."),
    universe: str = typer.Option("nifty500", "--universe"),
    interval: str = typer.Option("30m", "--interval", help="Entry timeframe: 30m or 5m."),
    killzone_start: str = typer.Option("09:15", "--killzone-start"),
    killzone_end: str = typer.Option("12:45", "--killzone-end", help="Entry window end, HH:MM."),
    rr: float = typer.Option(20.0, "--rr", help="Reward-to-risk target."),
    hold: int = typer.Option(
        60, "--hold",
        help="Sessions before an unresolved position is marked out. The source has no time "
             "stop; a mechanical replay needs one.",
    ),
    min_turnover: float = typer.Option(25.0, "--min-turnover-cr"),
    max_spread: float = typer.Option(25.0, "--max-spread-bps"),
    require_spread: bool = typer.Option(
        False, "--require-spread/--no-require-spread",
        help="Arm the estimated-spread cap. Off by default — see `trident --help`.",
    ),
    liquidity: bool = typer.Option(True, "--liquidity/--no-liquidity"),
    strong_daily: bool = typer.Option(True, "--strong-daily/--any-daily"),
) -> None:
    """Replay the trident over the available intraday history — roughly 60 sessions.

    That cap is the whole problem with measuring this strategy: at the source's own rate of
    8-10 setups a year per instrument, no sample this window can produce will separate a 90%
    win rate from a coin flip.
    """
    from .engines.trident import backtest_trident
    from .report.trident_report import render_backtest, render_liquidity

    cfg = _trident_cfg(
        interval=interval, killzone_start=killzone_start, killzone_end=killzone_end,
        rr=rr, hold=hold, strong_daily=strong_daily,
        min_turnover_cr=min_turnover, max_spread_bps=max_spread,
        require_spread=require_spread,
    )
    result = backtest_trident(
        max_symbols=symbols, universe=universe, cfg=cfg, apply_liquidity=liquidity
    )
    console.print(render_liquidity(result.liquidity, cfg))
    console.print(render_backtest(result, cfg))
    if result.fetch is not None and result.fetch.failed:
        console.print(f"[red]Fetch failures:[/] {result.fetch.summary()}")


@app.command("trident-sweep")
def trident_sweep(
    symbols: int = typer.Option(40, help="How many names to replay, after the floor. 0 = all."),
    universe: str = typer.Option("nifty500", "--universe"),
    interval: str = typer.Option("30m", "--interval", help="Entry timeframe: 30m or 5m."),
    rr: float = typer.Option(
        4.0, "--rr",
        help="Reward-to-risk. Defaults to 4R rather than 20R because at 20R almost nothing "
             "resolves inside the available history, and a grid of unresolved cells compares "
             "nothing.",
    ),
    hold: int = typer.Option(60, "--hold"),
    block: int = typer.Option(
        90, "--block-minutes", help="Width of each swept window, in minutes."
    ),
    liquidity: bool = typer.Option(True, "--liquidity/--no-liquidity"),
    strong_daily: bool = typer.Option(True, "--strong-daily/--any-daily"),
) -> None:
    """Sweep the kill zone across the session — is 09:15-12:45 contributing anything?

    The window is a straight transplant of a forex London-session block onto Indian
    equities and has never been tested. This replays every window on identical frames so the
    comparison is like for like.

    Read the intervals, not the ranking. A grid searched over ~60 sessions always has a best
    cell; adopting it is fitting rather than measuring.
    """
    from .engines.trident import default_windows, sweep_killzone
    from .report.trident_report import render_sweep

    cfg = _trident_cfg(interval=interval, rr=rr, hold=hold, strong_daily=strong_daily)
    windows = default_windows(cfg, block_minutes=block)
    console.print(
        f"[dim]Sweeping {len(windows)} windows on {interval} bars at {rr:.0f}R.[/]"
    )
    result = sweep_killzone(
        max_symbols=symbols, universe=universe, cfg=cfg, windows=windows,
        apply_liquidity=liquidity,
    )
    console.print(render_sweep(result, cfg))


@app.command("trident-failures")
def trident_failures() -> None:
    """The accumulated failure log — which gate refuses what, across every recorded pass."""
    from .engines.trident_live import load_failures
    from .report.trident_report import render_failure_log

    console.print(render_failure_log(load_failures()))


@app.command("trident-watch")
def trident_watch(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    limit: int = typer.Option(0, help="Cap the universe after the liquidity floor. 0 = all."),
    universe: str = typer.Option("nifty500", "--universe"),
    interval: str = typer.Option("30m", "--interval", help="Entry timeframe: 30m or 5m."),
    rr: float = typer.Option(
        4.0, "--rr",
        help="Reward-to-risk for setups recorded by THIS run. Existing records keep the "
             "ratio they were flagged with — a target you can move afterwards is not a "
             "target.",
    ),
    killzone_start: str = typer.Option("09:15", "--killzone-start"),
    killzone_end: str = typer.Option("12:45", "--killzone-end", help="Entry window end, HH:MM."),
    scan: bool = typer.Option(
        True, "--scan/--settle-only",
        help="--settle-only skips the scan and just marks open records against fresh bars.",
    ),
) -> None:
    """Collect the trident forward — scan the session, record setups, settle open ones.

    This is the only honest way left to test this strategy. The replay in
    docs/spec-trident.md is capped at ~60 sessions of intraday history against a pattern
    firing roughly a third of a time per session, which cannot separate a real hit rate from
    noise. Run this after the close on each trading day and let the record accumulate.
    """
    from . import trident_journal
    from .engines.trident import scan_trident
    from .report.trident_report import render_watch

    cfg = _trident_cfg(
        interval=interval, killzone_start=killzone_start, killzone_end=killzone_end, rr=rr
    )

    if scan:
        target = date.fromisoformat(on) if on else None
        signals = scan_trident(target, max_symbols=limit, universe=universe, cfg=cfg)
        added = trident_journal.record(signals, cfg)
        console.print(f"[green]Scanned:[/] {len(signals)} qualifying, {added} newly recorded")

    changed = trident_journal.settle(cfg)
    if changed:
        console.print(f"[green]Settled:[/] {changed} record(s) reached a stop or target")

    records = trident_journal.load()
    console.print(render_watch(records, trident_journal.as_result(records), cfg))
    console.print(f"[dim]Record: {trident_journal.WATCH_PATH}[/]")


@app.command("v3-watch")
def v3_watch() -> None:
    """Inspect and settle the immutable reliability-first forward event log."""
    from . import v3_events

    changed = v3_events.settle()
    events = v3_events.load()
    open_count = len(v3_events.open_records())
    rollout = v3_events.rollout_status()
    console.print(
        f"[green]Appended settlements:[/] {changed} · [yellow]Open:[/] {open_count} · "
        f"[dim]Events: {len(events)}[/]"
    )
    console.print(f"[dim]{rollout.phase}: {rollout.progress}; {rollout.reason}[/]")
    console.print(f"[dim]Immutable record: {v3_events.EVENT_PATH}[/]")


@app.command("timeframe-study")
def timeframe_study(
    symbols: int = typer.Option(
        60, help="How many liquid NIFTY 500 names to measure. 0 = all that clear the floor."
    ),
    universe: str = typer.Option("nifty500", "--universe"),
    rr: float = typer.Option(3.0, "--rr", help="Reward-to-risk for the continuation rule."),
    atr_stop: float = typer.Option(
        1.0, "--atr-stop", help="Stop distance in ATR(14) multiples."
    ),
    timeframes: list[str] = typer.Option(
        None, "--tf",
        help="Restrict to specific bar sizes (repeatable): 5m 15m 30m 60m daily weekly.",
    ),
) -> None:
    """Where does continuation actually persist on NSE — and what does it cost to trade it?

    Three instruments. Variance ratios and autocorrelation say what the market does, with no
    rule and no cost assumption in the way. One unchanged continuation rule, replayed at every
    bar size with that bar size's own costs, says what is left after paying for it.

    The gap between the two is the point: cost in R is (cost% / stop%), so halving the bar
    size roughly halves the stop and doubles the cost in R. A timeframe can persist and still
    be unprofitable, and on this data most of them are.
    """
    from .engines.timeframe_study import run_study
    from .report.timeframe_report import render_study

    study = run_study(
        max_symbols=symbols, universe=universe, reward_risk=rr, atr_stop_mult=atr_stop,
        timeframes=list(timeframes) if timeframes else None,
    )
    console.print(render_study(study))


@app.command("catalyst-backfill")
def catalyst_backfill(
    days: int = typer.Option(90, help="How many days back to collect."),
    llm: bool = typer.Option(True, "--llm/--no-llm", help="Score non-procedural filings."),
    budget: int = typer.Option(40, help="Max LLM calls per day of backfill."),
) -> None:
    """Collect historical BSE filings so the catalyst filter can be measured.

    Filings are the only catalyst source with an archive — the news RSS feeds serve about
    48 hours. A backfilled window is therefore filings-only and understates what the live
    filter sees, which any measurement built on it must say.
    """
    from datetime import timedelta

    from .engines.catalyst import backfill_filings

    end = date.today()
    start = end - timedelta(days=days)
    console.print(f"[dim]Collecting filings {start} → {end}…[/]")
    saved = backfill_filings(start, end, llm=llm, llm_budget_per_day=budget)
    console.print(f"[green]Saved {saved} catalyst records.[/]")


@app.command("v3-backtest")
def v3_backtest(
    symbols: int = typer.Option(
        60, help="How many current-setup symbols to replay (hindsight-ranked path)."
    ),
    horizon: int = typer.Option(5, help="Holding horizon in sessions."),
    symbols_file: Path | None = typer.Option(
        None, "--symbols-file", help="Point-in-time universe snapshot JSON."
    ),
    sample_size: int = typer.Option(
        0, "--sample", help="Uniform sample size after loading the snapshot. 0 = all."
    ),
    seed: int = typer.Option(1, help="Random seed used by --sample."),
    score_modules: bool = typer.Option(
        False, "--score-modules/--no-score-modules",
        help="Reconstruct quality-score modules at every historical decision.",
    ),
    cache_only: bool = typer.Option(
        False, "--cache-only/--no-cache-only",
        help="Use only previously fetched bars; make no data requests.",
    ),
) -> None:
    """Replay real entries for the fixed long-reclaim 2R research rule."""
    from .report.v3_report import render_backtest
    from .v3_backtest import run_v3_backtest

    frozen = None
    if symbols_file is not None:
        from .universe_snapshot import load, sample

        frozen = load(symbols_file)
        frozen = sample(frozen, sample_size, seed)
    elif sample_size:
        console.print("[red]--sample requires --symbols-file.[/]")
        raise typer.Exit(2)
    result = run_v3_backtest(
        symbols=frozen, max_symbols=symbols, horizon_sessions=horizon,
        score_modules=score_modules, cache_only=cache_only,
    )
    console.print(render_backtest(result))


@app.command("v3-calibrate")
def v3_calibrate(
    symbols_file: Path = typer.Option(
        ..., "--symbols-file", help="Point-in-time universe snapshot JSON (required)."
    ),
    sample_size: int = typer.Option(
        150, "--sample", help="Uniform snapshot sample. 0 = every symbol."
    ),
    seed: int = typer.Option(1, help="Random seed used by --sample."),
    horizon: int = typer.Option(5, help="Holding horizon in sessions."),
    output: Path = typer.Option(
        Path("data/v3_probability.json"), "--output", help="Where to save the model."
    ),
    cache_only: bool = typer.Option(
        False, "--cache-only", help="Use only previously fetched bars; make no data requests."
    ),
    intraday_store_path: Path = typer.Option(
        Path("data/intraday_15m.sqlite"), "--intraday-store",
        help="Validated immutable 15-minute research store.",
    ),
) -> None:
    """Test the fixed long-reclaim 2R rule with embargoed validation and final periods."""
    from .universe_snapshot import load as load_universe, sample
    import hashlib
    import subprocess

    from .data.bar_contract import IntradayStore
    from .v3_backtest import run_v3_backtest
    from .v3_probability import fit, report_markdown, save

    if not symbols_file.exists():
        console.print(f"[red]Universe snapshot not found:[/] {symbols_file}")
        raise typer.Exit(2)
    frozen = sample(load_universe(symbols_file), sample_size, seed)
    store = IntradayStore(intraday_store_path)
    first, last, sessions = store.coverage(list(frozen))
    if first is None or last is None or (last - first).days < settings.reliability_min_history_days:
        console.print(
            f"[red]Calibration blocked:[/] immutable 15-minute coverage is {first or 'missing'} "
            f"to {last or 'missing'} ({sessions} sessions); at least "
            f"{settings.reliability_min_history_days} calendar days are required."
        )
        raise typer.Exit(1)
    failures = store.coverage_failures(
        list(frozen), last - timedelta(days=settings.reliability_min_history_days), last
    )
    if failures:
        console.print(
            f"[red]Calibration blocked:[/] {len(failures)} sampled symbols lack complete "
            "two-year store coverage."
        )
        for failure in failures[:10]:
            console.print(f"  [dim]{failure}[/]")
        raise typer.Exit(1)
    console.print(
        f"[dim]Replaying {len(frozen)} point-in-time symbols with real next-bar fills; "
        "development, validation and final test are separated by five sessions…[/]"
    )
    result = run_v3_backtest(
        symbols=frozen, max_symbols=0, horizon_sessions=horizon, score_modules=False,
        cache_only=cache_only, intraday_store=store,
    )
    # The membership list did not exist before its snapshot date. Using it to select
    # earlier trades would let later survival/liquidity knowledge leak into the sample.
    snapshot_date = getattr(frozen, "as_of", None)
    if snapshot_date is not None:
        result.trades = [
            trade for trade in result.trades if trade.entered_at.date() > snapshot_date
        ]
    if not result.trades:
        console.print("[red]No completed trades were available to calibrate.[/]")
        raise typer.Exit(1)
    try:
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path.cwd(), capture_output=True,
            text=True, check=False,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=Path.cwd(), capture_output=True,
            text=True, check=False,
        ).stdout.strip())
        model = fit(
            result.trades,
            source=(
                f"{symbols_file} · uniform sample {len(frozen)} · seed {seed} · "
                f"{horizon} sessions"
            ),
            embargo_sessions=settings.v3_probability_embargo_sessions,
            provenance={
                "code_revision": revision,
                "code_dirty": dirty,
                "universe_snapshot": str(symbols_file),
                "universe_hash": digest(symbols_file),
                "data_hash": digest(intraday_store_path),
            },
        )
    except ValueError as exc:
        console.print(f"[red]Could not calibrate:[/] {exc}")
        raise typer.Exit(1) from exc
    save(model, output)

    table = Table(title="V3 corrected final test", header_style="bold")
    table.add_column("Setup")
    table.add_column("Target")
    table.add_column("Model")
    table.add_column("Raw final", justify="right")
    table.add_column("Raw wins", justify="right")
    table.add_column("Conservative", justify="right")
    table.add_column("Net", justify="right")
    table.add_column("Model selected", justify="right")
    table.add_column("Verdict")
    for strategy in model.strategies.values():
        check = strategy.raw_final
        table.add_row(
            strategy.setup, f"{strategy.target_r}R", strategy.method, str(check.n),
            f"{check.probability:.1%}" if check.n else "—",
            f"{check.lower:.1%}" if check.n else "—",
            f"{check.conservative_net_r:+.2f}R" if check.n else "—",
            str(strategy.final.n),
            "[green]PASS[/]" if strategy.approved else "[red]PAPER ONLY[/]",
        )
    console.print(table)
    verdict = "[green]approved for forward paper qualification[/]" if model.approved else "[red]funded alerts disabled[/]"
    console.print(
        f"Model is {verdict}. Development {model.development_start}→{model.development_end}; "
        f"validation {model.validation_start}→{model.validation_end}; final "
        f"{model.final_start}→{model.final_end}."
    )
    report_path = Path("docs") / f"{date.today()}-v3-corrected-probability-report.md"
    report_path.write_text(report_markdown(model), encoding="utf-8")
    console.print(f"[green]Written:[/] {output}")
    console.print(f"[green]Written:[/] {report_path}")


@app.command("v3-backfill-intraday")
def v3_backfill_intraday(
    symbols_file: Path = typer.Option(..., "--symbols-file", help="Frozen universe snapshot."),
    sample_size: int = typer.Option(
        150, "--sample", help="Uniform frozen-universe sample; 0 means every symbol."
    ),
    seed: int = typer.Option(1, help="Random seed shared with v3-calibrate."),
    start: str = typer.Option(..., "--start", help="First calendar date, YYYY-MM-DD."),
    end: str = typer.Option(..., "--end", help="Last completed calendar date, YYYY-MM-DD."),
    store_path: Path = typer.Option(
        Path("data/intraday_15m.sqlite"), "--store", help="Destination immutable store."
    ),
) -> None:
    """Backfill validated Upstox candles for reliability calibration."""
    from .data.bar_contract import IntradayStore
    from .data.upstox import UpstoxClient
    from .universe_snapshot import load as load_universe, sample

    try:
        start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        console.print("[red]Backfill refused:[/] dates must use YYYY-MM-DD.")
        raise typer.Exit(2) from exc
    if (end_day - start_day).days < settings.reliability_min_history_days:
        console.print(
            f"[red]Backfill refused:[/] request at least "
            f"{settings.reliability_min_history_days} calendar days."
        )
        raise typer.Exit(2)
    if end_day >= date.today():
        console.print("[red]Backfill refused:[/] --end must be a completed prior date.")
        raise typer.Exit(2)
    client = UpstoxClient()
    if not client.authenticated:
        console.print("[red]Backfill blocked:[/] authenticated Upstox data is required.")
        raise typer.Exit(1)
    store = IntradayStore(store_path)
    symbols = list(sample(load_universe(symbols_file), sample_size, seed))
    failures = []
    for position, symbol in enumerate(symbols, 1):
        frame = client.historical_intraday(symbol, start_day, end_day, interval="15m")
        if frame is None or frame.empty:
            failures.append(f"{symbol}: provider returned no complete history")
            continue
        try:
            store.append(symbol, frame, source="Upstox-v2", adjustment_status="unadjusted")
        except ValueError as exc:
            failures.append(f"{symbol}: {exc}")
        if position % 10 == 0:
            console.print(f"[dim]{position}/{len(symbols)} symbols processed[/]")
    if failures:
        console.print(f"[red]Backfill incomplete:[/] {len(failures)} symbol(s) failed.")
        for failure in failures[:20]:
            console.print(f"  [dim]{failure}[/]")
        raise typer.Exit(1)
    console.print(f"[green]Validated immutable store:[/] {store_path}")


@app.command("v3-monitor")
def v3_monitor(
    limit: int = typer.Option(
        0, help="Cap the intraday pass. 0 = evaluate every candidate (recommended)."
    ),
    per_day: int = typer.Option(1, help="Maximum probability-approved alerts per scan."),
    setup: list[str] = typer.Option(
        ["reclaim"], "--setup", help="Production is restricted to long reclaim setups."
    ),
    once: bool = typer.Option(
        True, "--once/--loop", help="Run once, or repeat shortly after every 15-minute close."
    ),
    model_path: Path = typer.Option(
        Path("data/v3_probability.json"), "--model", help="Validated probability model."
    ),
) -> None:
    """Issue alerts only from live bars and a validated, adequately sampled model."""
    import time as clock

    from .data import MarketData
    from .engines.v3_scan import run_v3_scan
    from .report.v3_report import render_v3, write_v3_brief
    from .report.v3_website import write_v3_html
    from .v3_probability import apply_to_scan, load as load_probability, market_is_open

    if not model_path.exists():
        console.print(
            "[red]No probability model.[/] Run [bold]asymmetry v3-calibrate "
            "--symbols-file <snapshot.json>[/] first."
        )
        raise typer.Exit(1)
    try:
        model = load_probability(model_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    model_expired = bool(
        model.final_end
        and (date.today() - date.fromisoformat(model.final_end)).days
        > settings.v3_probability_max_age_days
    )
    if model_expired:
        console.print(
            f"[yellow]Paper mode:[/] the evidence is more than "
            f"{settings.v3_probability_max_age_days} days old. Funded alerts remain disabled "
            "until v3-calibrate is run again."
        )
    if not MarketData().live_available:
        console.print(
            "[yellow]Paper mode:[/] live Upstox data is unavailable. Refresh authentication "
            "with [bold]asymmetry auth[/]; delayed prices cannot create or qualify an alert."
        )
        raise typer.Exit(1)
    if once and not market_is_open():
        console.print(
            "[yellow]The NSE cash session is closed.[/] No current prediction can be "
            "issued; run this during 09:15–15:30 IST."
        )
        raise typer.Exit(1)

    while True:
        from . import v3_events

        settled = v3_events.settle()
        rollout = v3_events.rollout_status()
        rollout_phase = "paper" if model_expired else rollout.phase
        rollout_reason = "the probability model has expired" if model_expired else rollout.reason
        scan = run_v3_scan(
            date.today(), max_intraday=limit, max_per_day=0,
            setups=tuple(setup) if setup else None,
        )
        scan.rollout_phase = rollout_phase
        scan.rollout_progress = f"{rollout.progress}; {rollout_reason}"
        apply_to_scan(
            scan, model, strict=True, require_live=True,
            require_capital=True,
        )
        if per_day > 0 and len(scan.trades) > per_day:
            overflow = scan.trades[per_day:]
            for candidate in overflow:
                candidate.rejected_by = "daily cap"
                candidate.reject_detail = f"outside the best {per_day} approved alerts"
            scan.near_miss = overflow + scan.near_miss
            scan.trades = scan.trades[:per_day]
        console.print(render_v3(scan))
        console.print(f"\n[green]Written:[/] {write_v3_brief(scan)}")
        console.print(f"[green]Written:[/] {write_v3_html(scan)}")
        added = v3_events.record_scan(scan, model)
        console.print(
            f"[dim]Immutable forward events: {added} signals · {settled} settlements · "
            f"{v3_events.EVENT_PATH}[/]"
        )
        if once:
            break
        # Wake five seconds after the next exchange-aligned 15-minute boundary. The scan
        # discards the forming candle as an additional protection against early signals.
        delay = 15 * 60 - (clock.time() % (15 * 60)) + 5
        console.print(f"[dim]Next check in {delay / 60:.1f} minutes. Ctrl+C stops.[/]")
        try:
            clock.sleep(delay)
        except KeyboardInterrupt:
            console.print("\n[yellow]Monitor stopped.[/]")
            break


@app.command("v3-fill")
def v3_fill(
    symbol: str = typer.Argument(..., help="Symbol with an open V3 forward record."),
    price: float = typer.Option(..., "--price", help="Actual execution price."),
) -> None:
    """Record an actual paper/live fill so realised slippage can be monitored."""
    from .v3_events import record_manual_fill

    try:
        record = record_manual_fill(symbol, price)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Appended fill[/] {record.symbol} at ₹{price:,.2f}; "
        f"slippage {record.actual_slippage_pct:.3f}%."
    )


@app.command()
def ui(
    port: int = typer.Option(8765, help="Loopback port for the panel."),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the panel in your browser on start."
    ),
    width: int = typer.Option(150, help="Console width the commands render at."),
) -> None:
    """Run every command from a browser instead of here.

    Binds to 127.0.0.1 only. The page posts a command id, never a command line.
    """
    from .ui import serve

    serve(port=port, open_browser=open_browser, width=width)


@app.command()
def site() -> None:
    """Build the static site in public/ from every generated brief."""
    from .report.site import build_site

    path = build_site()
    console.print(f"[green]Site built:[/] {path}")


@app.command()
def backtest(
    days: int = typer.Option(180, help="Calendar days back from the last stored day."),
    horizon: int = typer.Option(10, help="Forward holding period in trading days."),
    step: int = typer.Option(5, help="Sample every Nth trading day."),
    full: bool = typer.Option(
        True, "--full/--shortlist", help="Rank the whole universe, or only the shortlist."
    ),
    regime: bool = typer.Option(
        True,
        "--regime/--no-regime",
        help="Assess regime per day. Slow (option chain + macro); skip for a quick read.",
    ),
) -> None:
    """Walk forward and measure whether the ranking actually predicts anything."""
    from datetime import timedelta

    from .backtest import run_backtest, run_rank_backtest
    from .report.render import render_backtest
    from .storage import latest_stored_date

    end = latest_stored_date()
    if end is None:
        console.print("[red]No stored history. Run `asymmetry backfill` first.[/]")
        raise typer.Exit(1)

    start = end - timedelta(days=days)
    if full:
        report = run_rank_backtest(
            start, end, horizon=horizon, step=step, with_regime=regime
        )
    else:
        report = run_backtest(start, end, horizon=horizon, step=step)
    console.print(render_backtest(report, full=full))


@app.command()
def brief(
    on: str = typer.Option(None, "--date", help="Trading date (YYYY-MM-DD), default latest."),
    top: int = typer.Option(10, help="Shortlist size."),
    html: bool = typer.Option(False, "--html", help="Also render the HTML dashboard."),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Re-score news and filings. --no-refresh reuses stored catalysts (much faster).",
    ),
) -> None:
    """Compose all five engines into a dated brief (Markdown, optionally HTML)."""
    from .report.brief import generate_brief

    target = date.fromisoformat(on) if on else None
    for path in generate_brief(target, top_n=top, html=html, refresh_catalysts=refresh):
        console.print(f"[green]Written:[/] {path}")


if __name__ == "__main__":
    app()
