"""Reporting for the Engineer Brief specification (§19, §24).

The dashboard rules from §24 shape this directly: show the best candidates first, separate
TRADE / WATCH / REJECT, state exactly why something qualifies and exactly what would
invalidate it, and never phrase anything as an autonomous instruction to buy.

Rejections are reported as counts rather than rows. Four hundred rejection objects are
noise; the *distribution* of why they failed is the useful signal, and on this spec it is
often the most informative part of the whole report.
"""

from __future__ import annotations

from ..config import BRIEF_DIR, settings
from ..spec import SpecCandidate, SpecScan, Verdict


def _chain_block(candidate: SpecCandidate) -> list[str]:
    rungs = candidate.chain.rungs
    if not rungs:
        return []
    lines = [
        "",
        "| Timeframe | Trend | Structure | Setup | Supports long |",
        "| --- | --- | --- | --- | :---: |",
    ]
    for rung in rungs:
        lines.append(
            f"| {rung.timeframe} | {rung.trend.value} | {rung.structure or '—'} | "
            f"{rung.setup.value} | {'yes' if rung.supportive else 'no'} |"
        )
    return lines


def _candidate_block(candidate: SpecCandidate) -> list[str]:
    plan = candidate.plan
    lines = [
        f"### {candidate.rank}. {candidate.symbol} — {candidate.company}",
        "",
        f"*{candidate.sector} · {candidate.instrument} · score {candidate.score:.1f}*",
        "",
        f"**Why now:** {candidate.why_now}",
    ]
    if candidate.sector_quadrant:
        lines.append(f"**Context:** {candidate.sector_quadrant}")

    if plan is not None:
        lines += [
            "",
            "| | Price | Note |",
            "| --- | --: | --- |",
            f"| Entry trigger | {plan.entry:,.2f} | {plan.trigger} |",
            f"| Initial stop | {plan.stop:,.2f} | {plan.stop_pct:.2f}% — {plan.invalidation} |",
            f"| {settings.min_reward_risk:.0f}R target | {plan.target_4r:,.2f} | "
            f"+{plan.target_pct:.2f}% from entry |",
        ]
        if plan.nearest_resistance:
            marker = "before target" if plan.resistance_before_target else "beyond target"
            lines.append(
                f"| Nearest resistance | {plan.nearest_resistance:,.2f} | "
                f"{plan.resistance_distance_pct:+.2f}% — {marker}, "
                f"clearance {plan.clearance_probability:.0%} |"
            )
        lines.append(f"| Quantity | {plan.quantity} | at the configured risk budget |")

    probability = candidate.probability
    if probability.sample_size:
        lines += [
            "",
            f"**P(target before stop):** 1d {probability.p_1d:.0%} · "
            f"3d {probability.p_3d:.0%} · **5d {probability.p_5d:.0%}** "
            f"({probability.note})",
            f"**Expected value:** {candidate.expected_value.ev_r:+.2f}R after costs "
            f"({candidate.expected_value.note})",
        ]
        if not probability.calibrated:
            lines.append(
                "> Sample is thin — treat this probability as indicative, not calibrated."
            )

    volatility = candidate.volatility
    lines += [
        "",
        f"**Move potential:** expected 1d {volatility.expected_move_1d:.1f}% · "
        f"3d {volatility.expected_move_3d:.1f}% · 5d {volatility.expected_move_5d:.1f}% "
        f"· ATR {volatility.atr_pct:.2f}% ({volatility.atr_percentile:.0f}th pct)",
    ]
    if candidate.participation_note:
        lines.append(f"**Feasibility:** {candidate.participation_note}")
    if volatility.compressed:
        lines.append(
            f"**Compression:** BB width {volatility.bb_width_percentile:.0f}th pct"
            + (f", NR7" if volatility.nr7 else "")
            + (f", {volatility.compression_days}d tight" if volatility.compression_days else "")
        )

    if candidate.fno.has_fno:
        lines.append(f"**F&O:** {candidate.fno.note}")
        if candidate.fno.call_oi_wall:
            lines.append(
                f"**Option-implied resistance:** {candidate.fno.call_oi_wall:,.0f} "
                f"(heaviest call OI)"
            )

    lines += _chain_block(candidate)

    if plan is not None:
        lines += [
            "",
            f"**Invalidation:** a close below {plan.stop:,.2f} ({plan.invalidation}) "
            "voids the thesis.",
        ]
    lines.append("")
    return lines


def build_spec_markdown(scan: SpecScan) -> str:
    lines = [
        f"# Asymmetry — specification scan, {scan.as_of}",
        "",
        f"*{settings.min_reward_risk:.0f}R minimum · max initial stop "
        f"{settings.max_stop_pct:.1f}% · {settings.min_holding_sessions}–"
        f"{settings.max_holding_sessions} session horizon*",
        "",
        f"*Data tier: {scan.tier} · {scan.liquid_size}/{scan.universe_size} liquid · "
        f"{scan.evaluated} fully evaluated*",
        "",
        "> Decision support only. Nothing here is an instruction to buy — each entry is a "
        "setup for your own judgement, and the system places no orders.",
        "",
        "---",
        "",
        "## Market context",
        "",
        f"{scan.market_note}",
        "",
        "---",
        "",
    ]

    lines += [f"## TRADE — {len(scan.trades)} qualified", ""]
    if scan.trades:
        for candidate in scan.trades:
            lines += _candidate_block(candidate)
    else:
        lines += [
            "**Nothing qualified today.**",
            "",
            f"This is the expected outcome most days. A {settings.min_reward_risk:.0f}R "
            f"target with a stop capped at {settings.max_stop_pct:.1f}% requires a "
            f"{settings.min_reward_risk * settings.max_stop_pct:.1f}% move within five "
            "sessions, against an invalidation tight enough to survive normal noise. Very "
            "few setups clear that bar, and the specification is explicit that a minimum "
            "number of trades must never be forced.",
            "",
        ]

    if scan.watch:
        lines += ["---", "", f"## WATCH — {len(scan.watch)}", "",
                  "Structurally interesting, but something specific is missing.", "",
                  "| Symbol | Score | What is missing |", "| --- | --: | --- |"]
        for candidate in scan.watch[:15]:
            detail = candidate.reject_detail or candidate.reject_reason.value
            lines.append(f"| {candidate.symbol} | {candidate.score:.1f} | {detail} |")
        lines.append("")

    if scan.reject_counts:
        lines += ["---", "", f"## REJECT — {scan.total_rejected}", "",
                  "Why candidates failed. This distribution is the most useful diagnostic "
                  "in the report: it shows which constraint is actually binding.", "",
                  "| Reason | Count |", "| --- | --: |"]
        for reason, count in sorted(scan.reject_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    lines += [
        "---",
        "",
        "### How to read this",
        "",
        "1. **The chain runs downward.** Weekly and daily set context; 60m confirms; "
        "30m forms the setup; 15m triggers. A trigger against a bearish weekly is rejected, "
        "not discounted.",
        "2. **The stop is invalidation, not a percentage.** Where the valid structural stop "
        f"exceeded {settings.max_stop_pct:.1f}%, the setup was rejected rather than having "
        "its stop tightened to manufacture the R multiple.",
        f"3. **{settings.min_reward_risk:.0f}R is checked for plausibility**, not just "
        "arithmetic — against overhead resistance and the stock's own expected move.",
        "4. **EV ranks; the gates decide.** A positive expected value never rescues a setup "
        "that failed a hard gate.",
        "",
    ]
    return "\n".join(lines)


def write_spec_brief(scan: SpecScan) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{scan.as_of}-spec.md"
    path.write_text(build_spec_markdown(scan), encoding="utf-8")
    return str(path)
