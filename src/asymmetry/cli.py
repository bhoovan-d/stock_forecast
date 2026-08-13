"""Command line interface."""

from __future__ import annotations

import sys
from datetime import date, timedelta

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
