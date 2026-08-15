"""Every CLI command described once, so the panel cannot drift away from the CLI.

This registry is the only place that knows how a command is spelled. The browser posts a
command id and a ``{field: value}`` map; :func:`build_argv` looks the command up here and
assembles the argument list itself. Nothing the page sends can become an argument that is
not declared below, and no shell is involved at any point.

Adding a CLI command means adding an entry here — the form, the validation and the run
button all follow from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A symbol is the one free value that reaches the CLI as a *positional*, so it must not be
# able to start with a dash and arrive as an option.
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9&.\-]{0,19}$")

# Field kinds:
#   int/float/text/date  a value option, emitted as "--flag value" (or positional)
#   flag                 a boolean that is emitted only when true
#   toggle               a boolean with an explicit negative form (--refresh/--no-refresh)
#   choice               one of `choices`
#   multi                zero or more of `choices`, the flag repeated per selection


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str
    default: Any = None
    flag: str = ""            # empty means a positional argument
    off_flag: str = ""        # toggle only
    choices: tuple[str, ...] = ()
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class Command:
    id: str
    argv: tuple[str, ...]
    title: str
    group: str
    blurb: str
    runtime: str = ""
    fields: tuple[Field, ...] = ()
    danger: str = ""          # shown as a caution before the run button
    outputs: tuple[str, ...] = dc_field(default_factory=tuple)


COMMANDS: tuple[Command, ...] = (
    # ── Health and setup ──────────────────────────────────────────────────────
    Command(
        id="doctor",
        argv=("doctor",),
        title="Doctor",
        group="Health",
        blurb="Probe every data source and report which tier will feed the next brief.",
        runtime="~30s",
    ),
    Command(
        id="auth",
        argv=("auth",),
        title="Upstox auth",
        group="Health",
        blurb=(
            "Refresh the live-tier token. The login flow opens in your own browser and "
            "talks to Upstox directly from this machine."
        ),
        runtime="~1 min",
        fields=(
            Field(
                "manual", "Print the steps instead of opening a browser", "flag",
                default=False, flag="--manual",
            ),
        ),
        danger="Opens a browser window and waits for the redirect. Cancel if it stalls.",
    ),
    Command(
        id="backfill",
        argv=("backfill",),
        title="Backfill history",
        group="Health",
        blurb="Download bhavcopy + delivery history into SQLite. Everything else reads this.",
        runtime="1–5 min",
        fields=(
            Field("days", "Calendar days of history", "int", default=400, flag="--days"),
        ),
    ),

    # ── Engines ───────────────────────────────────────────────────────────────
    Command(
        id="regime",
        argv=("regime",),
        title="Regime",
        group="Engines",
        blurb="Engine 1 — aggressive, selective or defensive, and the five inputs behind it.",
        runtime="~30s",
        fields=(
            Field("on", "Trading date", "date", default="", flag="--date",
                  help="Blank = the latest stored session."),
        ),
    ),
    Command(
        id="scan",
        argv=("scan",),
        title="Scan",
        group="Engines",
        blurb="Engines 3+5 — rank the liquid universe and build trade plans for the top names.",
        runtime="1–3 min",
        fields=(
            Field("on", "Trading date", "date", default="", flag="--date"),
            Field("top", "Shortlist size", "int", default=10, flag="--top"),
            Field("no_catalyst", "Skip the LLM catalyst pass", "flag", default=False,
                  flag="--no-catalyst", help="Much faster, and the catalyst factor goes neutral."),
        ),
    ),
    Command(
        id="brief",
        argv=("brief",),
        title="Daily brief",
        group="Engines",
        blurb="All five engines composed into one dated brief.",
        runtime="3–8 min",
        fields=(
            Field("on", "Trading date", "date", default="", flag="--date"),
            Field("top", "Shortlist size", "int", default=10, flag="--top"),
            Field("html", "Also render the HTML dashboard", "flag", default=True, flag="--html"),
            Field("refresh", "Re-score news and filings", "toggle", default=True,
                  flag="--refresh", off_flag="--no-refresh",
                  help="Off reuses stored catalysts and is much faster."),
        ),
        outputs=("briefs",),
    ),
    Command(
        id="spec",
        argv=("spec",),
        title="Engineer Brief spec",
        group="Engines",
        blurb="The stricter engine: 4R minimum, 1.4% maximum stop, 1–5 sessions, TRADE/WATCH/REJECT.",
        runtime="1–3 min",
        fields=(
            Field("on", "Trading date", "date", default="", flag="--date"),
            Field("evaluate", "Names given the full multi-timeframe pass", "int",
                  default=30, flag="--evaluate"),
            Field("refresh", "Re-score news and filings via LLM", "flag", default=False,
                  flag="--refresh"),
            Field("refresh_rates", "Recompute historical base rates", "flag", default=False,
                  flag="--refresh-rates", help="Adds roughly 45 seconds."),
        ),
        outputs=("briefs",),
    ),
    Command(
        id="v3",
        argv=("v3",),
        title="Specification V3",
        group="Engines",
        blurb="NIFTY 500, long + short, 4R, 0.5–1.5% stop. The current engine.",
        runtime="4–8 min",
        fields=(
            Field("on", "Trading date", "date", default="", flag="--date"),
            Field("setup", "Setups", "multi", default=["reclaim"], flag="--setup",
                  choices=("reclaim", "continuation", "base-breakout"),
                  help=("Reclaim is the only one measured above break-even. "
                        "base-breakout is new and not yet measured.")),
            Field("per_day", "Maximum shown per day", "int", default=2, flag="--per-day",
                  help="0 = uncapped. V3 targets ~10–15 a month."),
            Field("min_score", "Quality-score override", "float", default=0.0,
                  flag="--min-score", help="0 = use the regime-derived threshold."),
            Field("limit", "Cap the intraday pass", "int", default=0, flag="--limit",
                  help="0 = evaluate every candidate (recommended)."),
            Field("refresh", "Re-score news and filings via LLM", "toggle", default=False,
                  flag="--refresh", off_flag="--no-refresh"),
        ),
        outputs=("briefs",),
    ),

    # ── Evidence ──────────────────────────────────────────────────────────────
    Command(
        id="v3-backtest",
        argv=("v3-backtest",),
        title="V3 backtest",
        group="Evidence",
        blurb="Replay the engine's own entries on 15-minute bars. Does a setup reach 4R first?",
        runtime="3–10 min",
        fields=(
            Field("symbols", "Symbols to replay", "int", default=60, flag="--symbols"),
            Field("horizon", "Holding horizon (sessions)", "int", default=5, flag="--horizon"),
        ),
    ),
    Command(
        id="backtest",
        argv=("backtest",),
        title="Rank backtest",
        group="Evidence",
        blurb="Walk forward and measure whether the ranking predicts anything at all.",
        runtime="2–15 min",
        fields=(
            Field("days", "Calendar days back", "int", default=180, flag="--days"),
            Field("horizon", "Forward holding period (trading days)", "int", default=10,
                  flag="--horizon"),
            Field("step", "Sample every Nth trading day", "int", default=5, flag="--step"),
            Field("full", "Rank the whole universe", "toggle", default=True,
                  flag="--full", off_flag="--shortlist"),
            Field("regime", "Assess regime per day", "toggle", default=False,
                  flag="--regime", off_flag="--no-regime",
                  help="Slow — option chain and macro per sampled day."),
        ),
    ),

    # ── Journal ───────────────────────────────────────────────────────────────
    Command(
        id="journal-log",
        argv=("journal", "log"),
        title="Log a decision",
        group="Journal",
        blurb="Record what you actually did about a call the system made.",
        runtime="instant",
        fields=(
            Field("symbol", "Symbol", "text", default="", required=True,
                  help="Ticker as the brief spells it, e.g. RELIANCE."),
            Field("action", "Action", "choice", default="taken",
                  choices=("taken", "skipped"), required=True),
            Field("price", "Your fill price", "float", default="", flag="--price"),
            Field("qty", "Your quantity", "int", default="", flag="--qty"),
            Field("note", "Why", "text", default="", flag="--note"),
        ),
    ),
    Command(
        id="journal-settle",
        argv=("journal", "settle"),
        title="Settle open calls",
        group="Journal",
        blurb="Mark open calls against stored prices.",
        runtime="~10s",
        fields=(
            Field("horizon", "Trading days before expiry", "int", default=10, flag="--horizon"),
        ),
    ),
    Command(
        id="journal-review",
        argv=("journal", "review"),
        title="Review decisions",
        group="Journal",
        blurb="The system's calls versus what you actually did.",
        runtime="~5s",
        fields=(
            Field("days", "Look-back window (days)", "int", default=90, flag="--days"),
        ),
    ),

    # ── Publish ───────────────────────────────────────────────────────────────
    Command(
        id="site",
        argv=("site",),
        title="Build static site",
        group="Publish",
        blurb="Rebuild public/ from every generated brief. Vercel serves this directory.",
        runtime="~5s",
        outputs=("site",),
    ),
)

BY_ID = {c.id: c for c in COMMANDS}
GROUPS = tuple(dict.fromkeys(c.group for c in COMMANDS))


def _coerce(field: Field, raw: Any) -> Any:
    """Validate one posted value, raising ValueError with a message fit for the UI."""
    if field.kind in ("flag", "toggle"):
        return bool(raw)
    if field.kind == "multi":
        values = raw if isinstance(raw, list) else ([raw] if raw else [])
        for value in values:
            if value not in field.choices:
                raise ValueError(f"{field.label}: {value!r} is not a valid choice")
        return values
    if raw is None:
        raw = ""
    text = str(raw).strip()
    if not text:
        if field.required:
            raise ValueError(f"{field.label} is required")
        return ""
    if field.kind == "int":
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{field.label} must be a whole number") from None
    if field.kind == "float":
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{field.label} must be a number") from None
    if field.kind == "date":
        if not DATE_RE.match(text):
            raise ValueError(f"{field.label} must look like 2026-08-14")
        return text
    if field.kind == "choice":
        if text not in field.choices:
            raise ValueError(f"{field.label}: {text!r} is not a valid choice")
        return text
    # Free text. The only one that reaches a shell-free argv is the journal note, but a
    # symbol still has to look like a symbol.
    if field.name == "symbol":
        if not SYMBOL_RE.match(text):
            raise ValueError("Symbol may only contain letters, digits, & . and -")
        return text.upper()
    return text[:500]


def build_argv(command: Command, values: dict[str, Any]) -> list[str]:
    """Turn a validated field map into the argument list for the CLI."""
    argv: list[str] = []
    positional: list[str] = []
    for field in command.fields:
        value = _coerce(field, values.get(field.name, field.default))
        if field.kind == "flag":
            if value:
                argv.append(field.flag)
        elif field.kind == "toggle":
            argv.append(field.flag if value else field.off_flag)
        elif field.kind == "multi":
            for item in value:
                argv += [field.flag, item]
        elif value == "":
            continue
        elif not field.flag:
            positional.append(str(value))
        else:
            argv += [field.flag, str(value)]
    # Positionals first, so the line reads the way it would have been typed.
    return [*command.argv, *positional, *argv]


def as_json() -> list[dict[str, Any]]:
    """The registry, shaped for the page that renders the forms."""
    return [
        {
            "id": c.id,
            "title": c.title,
            "group": c.group,
            "blurb": c.blurb,
            "runtime": c.runtime,
            "danger": c.danger,
            "outputs": list(c.outputs),
            "cli": "asymmetry " + " ".join(c.argv),
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "kind": f.kind,
                    "default": f.default,
                    "choices": list(f.choices),
                    "help": f.help,
                    "required": f.required,
                }
                for f in c.fields
            ],
        }
        for c in COMMANDS
    ]
