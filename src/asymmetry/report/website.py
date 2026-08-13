"""HTML dashboard export.

The Markdown brief is a document; this is an instrument panel. The information design
differs accordingly: state is encoded in form as well as number (regime stripe, diverging
score cells, proportional risk/reward bars) so what matters reads at a glance.

One rule drives the palette: in a trading interface red and green *are data*. They mean
direction and risk, so they cannot also be the brand accent. The accent is therefore a
steel cyan used only for structure — rules, labels, active states — and never for a value.
"""

from __future__ import annotations

import html
from datetime import date

from ..config import BRIEF_DIR
from ..models import Candidate, RegimeReport, RegimeVerdict, ScanResult

_VERDICT_TOKEN = {
    RegimeVerdict.AGGRESSIVE: "pos",
    RegimeVerdict.SELECTIVE: "caution",
    RegimeVerdict.DEFENSIVE: "neg",
}

_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ground:#F4F6F8; --surface:#FFFFFF; --raised:#FAFBFC;
  --line:#DDE3EA; --line-strong:#C3CCD6;
  --text:#111820; --text-dim:#4B586A; --muted:#6E7C8E;
  --accent:#22697F; --accent-soft:#E2F0F5;
  --pos:#12805A; --neg:#C0392F; --caution:#9A6A12;
  --pos-soft:#E3F4EC; --neg-soft:#FBE9E7; --caution-soft:#FAF0DC;
  --shadow:0 1px 2px rgba(16,26,38,.06),0 4px 16px rgba(16,26,38,.05);
  --sans:ui-sans-serif,-apple-system,"Segoe UI Variable Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Mono","SF Mono","Roboto Mono",Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --ground:#0A0D12; --surface:#121820; --raised:#182029;
    --line:#232D3A; --line-strong:#33404F;
    --text:#E6EBF2; --text-dim:#A2AEBF; --muted:#7C8899;
    --accent:#62B6D9; --accent-soft:#12303B;
    --pos:#3FBF87; --neg:#F0736B; --caution:#DFAA4B;
    --pos-soft:#0F2A20; --neg-soft:#2E1614; --caution-soft:#2B2211;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 20px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"]{
  --ground:#0A0D12; --surface:#121820; --raised:#182029;
  --line:#232D3A; --line-strong:#33404F;
  --text:#E6EBF2; --text-dim:#A2AEBF; --muted:#7C8899;
  --accent:#62B6D9; --accent-soft:#12303B;
  --pos:#3FBF87; --neg:#F0736B; --caution:#DFAA4B;
  --pos-soft:#0F2A20; --neg-soft:#2E1614; --caution-soft:#2B2211;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 20px rgba(0,0,0,.3);
}
:root[data-theme="light"]{
  --ground:#F4F6F8; --surface:#FFFFFF; --raised:#FAFBFC;
  --line:#DDE3EA; --line-strong:#C3CCD6;
  --text:#111820; --text-dim:#4B586A; --muted:#6E7C8E;
  --accent:#22697F; --accent-soft:#E2F0F5;
  --pos:#12805A; --neg:#C0392F; --caution:#9A6A12;
  --pos-soft:#E3F4EC; --neg-soft:#FBE9E7; --caution-soft:#FAF0DC;
  --shadow:0 1px 2px rgba(16,26,38,.06),0 4px 16px rgba(16,26,38,.05);
}

body{margin:0;background:var(--ground);color:var(--text);
  font-family:var(--sans);font-size:15px;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 96px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}

/* ── masthead ─────────────────────────────────────────────── */
.masthead{display:flex;flex-wrap:wrap;gap:20px;align-items:flex-end;
  justify-content:space-between;padding:44px 0 22px;border-bottom:2px solid var(--text)}
.brand{display:flex;flex-direction:column;gap:6px}
.brand h1{margin:0;font-size:31px;font-weight:680;letter-spacing:-.025em;text-wrap:balance}
.brand .sub{color:var(--muted);font-size:13.5px}
.meta{display:flex;gap:9px;flex-wrap:wrap}
.chip{font-family:var(--mono);font-size:11px;letter-spacing:.07em;text-transform:uppercase;
  padding:5px 10px;border:1px solid var(--line-strong);border-radius:3px;color:var(--text-dim);
  background:var(--surface);white-space:nowrap}
.chip.live{border-color:var(--pos);color:var(--pos);background:var(--pos-soft)}
.chip.degraded{border-color:var(--caution);color:var(--caution);background:var(--caution-soft)}

/* ── step sections ────────────────────────────────────────── */
.step{margin-top:52px}
.step-head{display:flex;align-items:baseline;gap:14px;margin-bottom:18px;
  padding-bottom:9px;border-bottom:1px solid var(--line)}
.step-n{font-family:var(--mono);font-size:11.5px;color:var(--accent);
  letter-spacing:.13em;font-weight:600;white-space:nowrap}
.step-head h2{margin:0;font-size:19.5px;font-weight:640;letter-spacing:-.015em}
.step-head .q{color:var(--muted);font-size:13.5px;font-style:italic;margin-left:auto;
  text-align:right}

/* ── regime ───────────────────────────────────────────────── */
.regime{background:var(--surface);border:1px solid var(--line);border-radius:6px;
  box-shadow:var(--shadow);overflow:hidden;display:grid;
  grid-template-columns:minmax(230px,300px) 1fr}
.verdict{padding:26px 24px;border-right:1px solid var(--line);
  display:flex;flex-direction:column;gap:13px;position:relative}
.verdict::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--vc)}
.verdict .label{font-family:var(--mono);font-size:10.5px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted)}
.verdict .state{font-size:25px;font-weight:660;letter-spacing:-.02em;color:var(--vc);
  line-height:1.15;text-wrap:balance}
.dot{width:9px;height:9px;border-radius:50%;background:var(--vc);display:inline-block;
  margin-right:9px;vertical-align:middle;box-shadow:0 0 0 3px color-mix(in srgb,var(--vc) 22%,transparent)}
.tally{font-family:var(--mono);font-size:13px;color:var(--text-dim);
  padding-top:12px;border-top:1px solid var(--line)}
.tally b{color:var(--vc);font-size:16px}

.inputs{display:flex;flex-direction:column}
.input-row{display:grid;grid-template-columns:132px 66px 1fr;gap:14px;align-items:center;
  padding:13px 22px;border-bottom:1px solid var(--line)}
.input-row:last-child{border-bottom:0}
.input-row .nm{font-size:13.5px;font-weight:560}
.input-row .dt{font-size:12.5px;color:var(--text-dim);line-height:1.45}
/* diverging three-cell: negative | neutral | positive */
.diverge{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;height:19px}
.diverge span{border-radius:2px;background:var(--line);position:relative}
.diverge span.on{background:var(--sc)}
.diverge span.on::after{content:"";position:absolute;inset:0;border-radius:2px;
  box-shadow:0 0 0 1px color-mix(in srgb,var(--sc) 55%,transparent)}

.gamma{padding:18px 22px;border-top:1px solid var(--line);background:var(--raised)}
.gamma .cap{font-family:var(--mono);font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--muted);margin-bottom:13px}
.axis{position:relative;height:34px;margin:0 4px}
.axis .track{position:absolute;top:15px;left:0;right:0;height:3px;background:var(--line);
  border-radius:2px}
.axis .fill{position:absolute;top:15px;height:3px;border-radius:2px;background:var(--accent)}
.axis .tick{position:absolute;top:6px;width:2px;height:21px;background:var(--text);
  transform:translateX(-1px)}
.axis .tick.flip{background:var(--caution);width:2px}
.axis .lab{position:absolute;top:-2px;font-family:var(--mono);font-size:10.5px;
  color:var(--text-dim);transform:translateX(-50%);white-space:nowrap}
.gamma .note{margin-top:9px;font-size:12.5px;color:var(--text-dim)}
.gamma .note b{font-family:var(--mono);color:var(--text)}

/* ── shortlist ────────────────────────────────────────────── */
.scroller{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:6px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;min-width:760px}
thead th{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);font-weight:500;text-align:left;padding:12px 14px;
  border-bottom:1px solid var(--line-strong);white-space:nowrap;background:var(--raised)}
thead th.r{text-align:right}
tbody td{padding:12px 14px;border-bottom:1px solid var(--line);font-size:13.5px;
  vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--raised)}
td.r{text-align:right}
.rank{font-family:var(--mono);color:var(--muted);font-size:12px}
.tkr{font-family:var(--mono);font-weight:640;font-size:13.5px;letter-spacing:-.01em}
.tkr .co{display:block;font-family:var(--sans);font-weight:400;font-size:11.5px;
  color:var(--muted);letter-spacing:0;margin-top:2px}
.sect{font-size:12px;color:var(--text-dim)}
.score{font-family:var(--mono);font-weight:640;font-size:14.5px}

/* factor micro-bars */
.factors{display:flex;gap:5px;align-items:flex-end;height:26px}
.fbar{width:11px;background:var(--line);border-radius:1px;position:relative;
  display:flex;align-items:flex-end;overflow:hidden}
.fbar i{display:block;width:100%;background:var(--accent);border-radius:1px}
.fbar.cat i{background:var(--caution)}
.flegend{display:flex;gap:5px;margin-top:5px}
.flegend span{width:11px;font-family:var(--mono);font-size:8.5px;color:var(--muted);
  text-align:center}

/* ── why ──────────────────────────────────────────────────── */
.why-grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.why{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:5px;padding:17px 19px;box-shadow:var(--shadow)}
.why h3{margin:0 0 3px;font-family:var(--mono);font-size:14px;font-weight:640}
.why .co{color:var(--muted);font-size:12px;margin-bottom:12px}
.why dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:7px 12px;font-size:12.5px}
.why dt{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);padding-top:2px}
.why dd{margin:0;color:var(--text-dim);line-height:1.5}
.why dd b{color:var(--text);font-family:var(--mono)}

/* ── trade plans: the asymmetry bar ───────────────────────── */
.plans{display:flex;flex-direction:column;gap:12px}
.plan{background:var(--surface);border:1px solid var(--line);border-radius:6px;
  padding:17px 20px;box-shadow:var(--shadow);display:grid;gap:15px;
  grid-template-columns:minmax(150px,190px) 1fr minmax(96px,auto);align-items:center}
.plan .id{display:flex;flex-direction:column;gap:3px}
.plan .id .t{font-family:var(--mono);font-weight:660;font-size:15px}
.plan .id .s{font-size:11.5px;color:var(--muted);line-height:1.4}
.plan .warn{font-size:11px;color:var(--caution);background:var(--caution-soft);
  border-radius:3px;padding:4px 7px;margin-top:5px;line-height:1.35}

.rr{display:flex;flex-direction:column;gap:7px;min-width:0}
.bar{display:flex;height:26px;border-radius:3px;overflow:hidden;
  border:1px solid var(--line-strong)}
.bar .risk{background:var(--neg-soft);border-right:2px solid var(--neg);
  display:flex;align-items:center;justify-content:flex-start;padding-left:8px}
.bar .reward{background:var(--pos-soft);display:flex;align-items:center;
  justify-content:flex-end;padding-right:8px}
.bar span{font-family:var(--mono);font-size:10.5px;font-weight:600;white-space:nowrap}
.bar .risk span{color:var(--neg)}
.bar .reward span{color:var(--pos)}
.levels{display:flex;justify-content:space-between;font-family:var(--mono);font-size:11px;
  color:var(--muted);gap:8px}
.levels b{color:var(--text);font-weight:600}
.levels .mid{color:var(--text)}

.rmult{text-align:right;display:flex;flex-direction:column;gap:2px}
.rmult .v{font-family:var(--mono);font-size:22px;font-weight:680;color:var(--pos);
  letter-spacing:-.02em;line-height:1}
.rmult .k{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted)}
.rmult .q{font-size:11px;color:var(--text-dim);margin-top:3px}

/* ── watch + notes ────────────────────────────────────────── */
.watch{background:var(--surface);border:1px dashed var(--line-strong);border-radius:6px;
  padding:6px 20px}
.watch .row{display:flex;justify-content:space-between;gap:14px;padding:10px 0;
  border-bottom:1px solid var(--line);font-size:13px;align-items:center}
.watch .row:last-child{border-bottom:0}
.watch .why-not{color:var(--neg);font-family:var(--mono);font-size:11.5px}
.empty{background:var(--surface);border:1px solid var(--line);border-radius:6px;
  padding:34px 26px;text-align:center}
.empty .h{font-size:17px;font-weight:620;margin-bottom:7px}
.empty .p{color:var(--text-dim);font-size:13.5px;max-width:52ch;margin:0 auto}

.legend{margin-top:16px;padding:15px 19px;background:var(--raised);border:1px solid var(--line);
  border-radius:5px;font-size:12.5px;color:var(--text-dim);line-height:1.6}
.legend b{color:var(--text)}
footer{margin-top:56px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;display:flex;justify-content:space-between;
  gap:16px;flex-wrap:wrap}
.disclaim{margin-top:26px;padding:15px 19px;border-left:3px solid var(--caution);
  background:var(--caution-soft);border-radius:0 5px 5px 0;font-size:12.5px;color:var(--text-dim)}
.disclaim b{color:var(--text)}

@media (max-width:820px){
  .regime{grid-template-columns:1fr}
  .verdict{border-right:0;border-bottom:1px solid var(--line)}
  .input-row{grid-template-columns:106px 58px 1fr;gap:10px;padding:12px 16px}
  .plan{grid-template-columns:1fr;gap:13px}
  .rmult{text-align:left;flex-direction:row;align-items:baseline;gap:9px}
  .step-head{flex-wrap:wrap}
  .step-head .q{margin-left:0;width:100%;text-align:left}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _regime_block(regime: RegimeReport) -> str:
    token = _VERDICT_TOKEN[regime.verdict]
    rows = []
    for component in regime.components:
        # Diverging cells: which of (negative, neutral, positive) is lit.
        cells = ""
        for slot, score in enumerate((-1, 0, 1)):
            colour = {
                -1: "var(--neg)", 0: "var(--muted)", 1: "var(--pos)",
            }[score]
            on = " on" if component.score == score else ""
            cells += f'<span class="{on.strip()}" style="--sc:{colour}"></span>'
        rows.append(
            f'<div class="input-row">'
            f'<div class="nm">{_esc(component.name)}</div>'
            f'<div class="diverge">{cells}</div>'
            f'<div class="dt">{_esc(component.detail)}</div>'
            f"</div>"
        )

    gamma = ""
    if regime.net_gex is not None and regime.spot:
        spot, flip = regime.spot, regime.gamma_flip
        # Position spot and flip on a shared axis spanning both, with headroom.
        if flip:
            lo = min(spot, flip) * 0.985
            hi = max(spot, flip) * 1.015
            span = hi - lo
            spot_pct = (spot - lo) / span * 100
            flip_pct = (flip - lo) / span * 100
            fill_lo, fill_hi = sorted((spot_pct, flip_pct))
            marks = (
                f'<div class="fill" style="left:{fill_lo:.1f}%;width:{fill_hi - fill_lo:.1f}%"></div>'
                f'<div class="tick" style="left:{spot_pct:.1f}%"></div>'
                f'<div class="lab" style="left:{spot_pct:.1f}%">spot {spot:,.0f}</div>'
                f'<div class="tick flip" style="left:{flip_pct:.1f}%"></div>'
                f'<div class="lab" style="left:{flip_pct:.1f}%;top:20px">flip {flip:,.0f}</div>'
            )
            distance = (spot / flip - 1) * 100
            note = (
                f"Spot sits <b>{distance:+.1f}%</b> from the gamma flip. "
                f"Net dealer gamma <b>{regime.net_gex / 1e9:,.1f}bn</b> — "
                + (
                    "dealers hedge <b>against</b> the move, so expect chop."
                    if regime.net_gex > 0
                    else "dealers hedge <b>with</b> the move, so breakouts have room."
                )
            )
        else:
            marks = '<div class="tick" style="left:50%"></div>'
            note = f"Net dealer gamma <b>{regime.net_gex / 1e9:,.1f}bn</b>."
        gamma = (
            '<div class="gamma"><div class="cap">Dealer gamma positioning</div>'
            f'<div class="axis"><div class="track"></div>{marks}</div>'
            f'<div class="note">{note} This is context for how the tape behaves — '
            "never an entry signal.</div></div>"
        )

    return (
        f'<div class="regime" style="--vc:var(--{token})">'
        f'<div class="verdict">'
        f'<div class="label">Today\'s posture</div>'
        f'<div class="state"><span class="dot"></span>{_esc(regime.verdict.headline)}</div>'
        f'<div class="tally">Composite <b>{regime.total:+d}</b> <span style="color:var(--muted)">'
        f"of ±5</span></div></div>"
        f'<div><div class="inputs">{"".join(rows)}</div>{gamma}</div></div>'
    )


def _factor_bars(cand: Candidate) -> str:
    factors = [
        ("RS", cand.factors.relative_strength, ""),
        ("Vo", cand.factors.volume, ""),
        ("St", cand.factors.price_structure, ""),
        ("Ca", cand.factors.catalyst, "cat"),
        ("Lq", cand.factors.liquidity, ""),
    ]
    bars, keys = "", ""
    for label, value, extra in factors:
        height = max(6, min(100, value if value == value else 0))
        bars += (
            f'<div class="fbar {extra}" title="{_esc(label)} {value:.0f}">'
            f'<i style="height:{height:.0f}%"></i></div>'
        )
        keys += f"<span>{label}</span>"
    return f'<div class="factors">{bars}</div><div class="flegend">{keys}</div>'


def _shortlist(result: ScanResult) -> str:
    if not result.candidates:
        return (
            '<div class="empty"><div class="h">No name cleared the gate today.</div>'
            '<div class="p">This is a valid outcome, not a failure. The gate exists to '
            "say no — a session without a 2R setup is a session to sit out.</div></div>"
        )
    rows = ""
    for i, cand in enumerate(result.candidates, 1):
        rows += (
            f"<tr><td class='rank'>{i:02d}</td>"
            f"<td class='tkr'>{_esc(cand.symbol)}<span class='co'>{_esc(cand.company)}</span></td>"
            f"<td class='sect'>{_esc(cand.sector)}</td>"
            f"<td>{_factor_bars(cand)}</td>"
            f"<td class='r score'>{cand.total_score:.1f}</td>"
            f"<td class='r num' style='color:var(--text-dim)'>{_money(cand.close)}</td></tr>"
        )
    return (
        '<div class="scroller"><table><thead><tr>'
        "<th>#</th><th>Stock</th><th>Sector</th><th>Factors</th>"
        "<th class='r'>Score</th><th class='r'>Close</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        '<div class="legend">Factor bars, left to right: <b>RS</b> relative strength '
        "(vs NIFTY and vs sector) · <b>Vo</b> volume and delivery expansion · "
        "<b>St</b> price structure · <b>Ca</b> catalyst · <b>Lq</b> liquidity. "
        "Each is a percentile across the liquid universe, so 90 means top decile — not 90%.</div>"
    )


def _why(result: ScanResult) -> str:
    cards = ""
    for cand in result.candidates:
        has_macro = cand.macro.reliable and cand.macro.gap_pct is not None
        if not cand.catalyst_note and not has_macro:
            continue
        items = ""
        if cand.catalyst_note:
            items += f"<dt>Catalyst</dt><dd>{_esc(cand.catalyst_note)}</dd>"
        if has_macro:
            direction = "above" if cand.macro.gap_pct > 0 else "below"
            drivers = ", ".join(f"{n} {b:+.3f}" for n, b in cand.macro.top_drivers)
            items += (
                f"<dt>Macro</dt><dd>Trading <b>{abs(cand.macro.gap_pct):.1f}%</b> {direction} "
                f"macro-implied fair value <b>₹{_money(cand.macro.fair_value)}</b>"
                f"<br><span style='color:var(--muted);font-size:11.5px'>R²={cand.macro.r_squared:.2f}"
                f" · drivers: {_esc(drivers)}</span></dd>"
            )
        cards += (
            f'<div class="why"><h3>{_esc(cand.symbol)}</h3>'
            f'<div class="co">{_esc(cand.company)}</div><dl>{items}</dl></div>'
        )
    if not cards:
        return (
            '<div class="legend">No scored catalyst or reliable macro gap on today\'s '
            "shortlist. These names rank on price and participation alone — a weaker reason "
            "to expect movement.</div>"
        )
    return f'<div class="why-grid">{cards}</div>'


def _plans(result: ScanResult) -> str:
    planned = [c for c in result.candidates if c.plan]
    if not planned:
        return ""
    blocks = ""
    for cand in planned:
        plan = cand.plan
        risk = plan.entry - plan.stop
        reward = plan.target - plan.entry
        total = risk + reward
        risk_pct = risk / total * 100 if total else 50
        warning = (
            f'<div class="warn">{_esc(cand.earnings_flag)}</div>'
            if cand.earnings_flag
            else ""
        )
        blocks += (
            f'<div class="plan"><div class="id">'
            f'<div class="t">{_esc(cand.symbol)}</div>'
            f'<div class="s">Invalidation: {_esc(plan.invalidation)}</div>{warning}</div>'
            f'<div class="rr"><div class="bar">'
            f'<div class="risk" style="width:{risk_pct:.1f}%"><span>risk ₹{risk:,.2f}</span></div>'
            f'<div class="reward" style="width:{100 - risk_pct:.1f}%">'
            f"<span>reward ₹{reward:,.2f}</span></div></div>"
            f'<div class="levels"><span>stop <b>{_money(plan.stop)}</b></span>'
            f'<span class="mid">entry <b>{_money(plan.entry)}</b></span>'
            f"<span>target <b>{_money(plan.target)}</b></span></div></div>"
            f'<div class="rmult"><div class="k">Reward : risk</div>'
            f'<div class="v">{plan.reward_risk:.1f}<span style="font-size:13px">×</span></div>'
            f'<div class="q">{plan.quantity} sh</div></div></div>'
        )
    return (
        f'<div class="plans">{blocks}</div>'
        '<div class="legend">Bar widths are drawn to scale — the green portion is how much '
        "further the target sits than the stop. That visible asymmetry <b>is</b> the trade "
        "thesis: every plan here clears the 2R gate, and anything below it was rejected "
        "outright rather than shown with a warning.</div>"
    )


def _watch(result: ScanResult) -> str:
    if not result.watchlist:
        return ""
    rows = ""
    for cand in result.watchlist:
        rows += (
            f'<div class="row"><span class="tkr">{_esc(cand.symbol)}</span>'
            f'<span class="why-not">{_esc(cand.rejected_reason)}</span></div>'
        )
    return (
        '<div class="step"><div class="step-head"><span class="step-n">05</span>'
        "<h2>Watch, not trade</h2>"
        '<span class="q">Strong stock, insufficient asymmetry</span></div>'
        f'<div class="watch">{rows}</div></div>'
    )


def build_html(result: ScanResult) -> str:
    tier = result.tier or "unknown"
    tier_class = "live" if "LIVE" in tier.upper() else "degraded"
    steps = [
        ("01", "Market regime", "Should I be aggressive today?", _regime_block(result.regime)),
        ("02", "Shortlist", "Which stock is most likely to move?", _shortlist(result)),
        ("03", "The reason", "Why should this stock move at all?", _why(result)),
        ("04", "Trade plans", "Can I make 2R?", _plans(result)),
    ]
    body = ""
    for number, title, question, content in steps:
        if not content:
            continue
        body += (
            f'<section class="step"><div class="step-head"><span class="step-n">{number}</span>'
            f"<h2>{title}</h2><span class='q'>{_esc(question)}</span></div>{content}</section>"
        )

    return f"""<title>Asymmetry Brief — {result.as_of:%d %b %Y}</title>
<style>{_CSS}</style>
<div class="wrap">
  <header class="masthead">
    <div class="brand">
      <h1>Asymmetry Brief</h1>
      <div class="sub">{result.as_of:%A, %d %B %Y} · NSE equities</div>
    </div>
    <div class="meta">
      <span class="chip {tier_class}">{_esc(tier)}</span>
      <span class="chip">{result.liquid_size}/{result.universe_size} liquid</span>
      <span class="chip">{len(result.candidates)} shortlisted</span>
    </div>
  </header>
  {body}
  {_watch(result)}
  <div class="disclaim">
    <b>Decision support, not execution.</b> This system holds no broker credentials that can
    trade and contains no order-placement code. Every level here is a starting point for your
    own judgement, not a recommendation — you place each order yourself.
  </div>
  <footer>
    <span>Regime → catalyst → selection → macro → asymmetry</span>
    <span class="num">Generated {result.as_of:%Y-%m-%d}</span>
  </footer>
</div>"""


def export_html(result: ScanResult, path: str | None = None) -> str:
    """Write the dashboard next to the Markdown brief."""
    target = path or str(BRIEF_DIR / f"{result.as_of:%Y-%m-%d}.html")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(build_html(result))
    return target
