"""HTML dashboard for Specification V3 (§18, §24).

The information design follows §24 directly: show only the highest-quality setups, state
exactly why each qualifies and exactly what would invalidate it, and never phrase anything
as an instruction.

Two visual decisions carry the meaning:

* **The funnel is shown as a shrinking bar**, because the selectivity *is* the product. A
  reader should see 473 become 2 and understand that the rejections are the work.
* **Risk and reward are drawn to scale.** A 4R payoff against a sub-1.5% stop is the entire
  thesis, and a proportional bar communicates that instantly where two numbers do not.

Long is green, short is red — semantic colour reserved for direction. The accent stays a
steel cyan used only for structure, since in a trading interface red and green are data.
"""

from __future__ import annotations

import html
from datetime import datetime

from ..config import BRIEF_DIR, settings
from ..engines.v3_scan import V3Candidate, V3Scan
from .theme import TOKENS

_CSS = TOKENS + """
.wrap{max-width:1060px;margin:0 auto;padding:0 24px 88px}
.masthead{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end;justify-content:space-between;
  padding:44px 0 20px;border-bottom:2px solid var(--text)}
.brand h1{margin:0 0 6px;font-size:30px;font-weight:680;letter-spacing:-.025em}
.brand .sub{color:var(--muted);font-size:13.5px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  padding:5px 10px;border:1px solid var(--line-strong);border-radius:3px;color:var(--text-dim);
  background:var(--surface);white-space:nowrap}
.chip.pos{border-color:var(--pos);color:var(--pos);background:var(--pos-soft)}
.chip.caution{border-color:var(--caution);color:var(--caution);background:var(--caution-soft)}

section{margin-top:44px}
h2{font-size:19px;font-weight:640;letter-spacing:-.015em;margin:0 0 6px;
  padding-bottom:8px;border-bottom:1px solid var(--line)}
.lede{color:var(--muted);font-size:13px;margin:0 0 18px}

/* funnel */
.funnel{display:flex;flex-direction:column;gap:7px;background:var(--surface);
  border:1px solid var(--line);border-radius:6px;padding:18px 20px;box-shadow:var(--shadow)}
.frow{display:grid;grid-template-columns:190px 1fr 62px;gap:12px;align-items:center}
.frow .lbl{font-size:13px;color:var(--text-dim)}
.frow .bar{height:16px;background:var(--accent-soft);border-radius:3px;overflow:hidden}
.frow .bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
.frow .val{font-family:var(--mono);font-size:13px;text-align:right;
  font-variant-numeric:tabular-nums}
.frow.final .lbl,.frow.final .val{font-weight:680;color:var(--text)}
.frow.final .bar i{background:var(--pos)}

/* setup card */
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  box-shadow:var(--shadow);overflow:hidden;margin-bottom:16px}
.card .head{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;
  padding:16px 20px;border-bottom:1px solid var(--line);background:var(--raised)}
.card .tkr{font-family:var(--mono);font-size:18px;font-weight:680;letter-spacing:-.01em}
.dir{font-family:var(--mono);font-size:11px;letter-spacing:.08em;padding:3px 8px;border-radius:3px}
.dir.long{background:var(--pos-soft);color:var(--pos)}
.dir.short{background:var(--neg-soft);color:var(--neg)}
.card .co{color:var(--muted);font-size:12.5px}
.card .score{margin-left:auto;font-family:var(--mono);font-size:15px;font-weight:660}
.setup-tag{font-family:var(--mono);font-size:11px;color:var(--text-dim);
  border:1px solid var(--line-strong);border-radius:3px;padding:2px 7px}

.levels{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1px;
  background:var(--line)}
.lv{background:var(--surface);padding:13px 16px}
.lv .k{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin-bottom:4px}
.lv .v{font-family:var(--mono);font-size:15px;font-variant-numeric:tabular-nums}
.lv.stop .v{color:var(--neg)}
.lv.target .v{color:var(--pos)}

.rr{padding:16px 20px;border-top:1px solid var(--line)}
.rr .track{display:flex;height:26px;border-radius:3px;overflow:hidden;
  border:1px solid var(--line-strong)}
.rr .risk{background:var(--neg-soft);border-right:2px solid var(--neg);display:flex;
  align-items:center;padding-left:8px}
.rr .reward{background:var(--pos-soft);display:flex;align-items:center;justify-content:flex-end;
  padding-right:8px}
.rr span{font-family:var(--mono);font-size:10.5px;font-weight:600;white-space:nowrap}
.rr .risk span{color:var(--neg)}
.rr .reward span{color:var(--pos)}

.why{padding:4px 20px 18px}
.why dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:8px 14px;font-size:13px}
.why dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);padding-top:3px;white-space:nowrap}
.why dd{margin:0;color:var(--text-dim);line-height:1.5}
.why dd b{color:var(--text)}
.chain{font-family:var(--mono);font-size:11.5px;color:var(--text-dim)}

table{border-collapse:collapse;width:100%;min-width:520px}
.scroller{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:6px;box-shadow:var(--shadow)}
thead th{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:500;text-align:left;padding:11px 14px;
  border-bottom:1px solid var(--line-strong);background:var(--raised);white-space:nowrap}
tbody td{padding:11px 14px;border-bottom:1px solid var(--line);font-size:13px}
tbody tr:last-child td{border-bottom:0}
td.num{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums}

.empty{background:var(--surface);border:1px solid var(--line);border-radius:6px;
  padding:32px 26px;text-align:center}
.empty .h{font-size:17px;font-weight:620;margin-bottom:7px}
.empty .p{color:var(--text-dim);font-size:13.5px;max-width:56ch;margin:0 auto}
.note{margin-top:22px;padding:14px 18px;border-left:3px solid var(--caution);
  background:var(--caution-soft);border-radius:0 5px 5px 0;font-size:12.5px;color:var(--text-dim)}
.note b{color:var(--text)}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:16px;
  flex-wrap:wrap}
@media (max-width:640px){
  .frow{grid-template-columns:130px 1fr 50px;gap:8px}
  .why dl{grid-template-columns:1fr}
  .why dt{padding-top:8px}
}
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _funnel(scan: V3Scan) -> str:
    stages = [
        ("NIFTY 500 liquid", scan.liquid, False),
        ("Showing a V3 setup", scan.with_setup, False),
        ("Cleared the quality floor", scan.cleared_floor, False),
        ("Shown today", len(scan.trades), True),
    ]
    top = max((count for _l, count, _f in stages), default=1) or 1
    rows = ""
    for label, count, final in stages:
        width = max(count / top * 100, 0.6)
        rows += (
            f'<div class="frow{" final" if final else ""}">'
            f'<span class="lbl">{_esc(label)}</span>'
            f'<span class="bar"><i style="width:{width:.1f}%"></i></span>'
            f'<span class="val">{count}</span></div>'
        )
    return f'<div class="funnel">{rows}</div>'


def _card(candidate: V3Candidate) -> str:
    plan = candidate.plan
    if plan is None:
        return ""

    risk = abs(plan.entry - plan.stop)
    reward = abs(plan.target - plan.entry)
    total = risk + reward
    risk_pct = risk / total * 100 if total else 20

    trigger = (
        "live now"
        if plan.is_live
        else f"triggered {_esc(plan.triggered_at)}"
    )

    return f"""<div class="card">
  <div class="head">
    <span class="tkr">{_esc(candidate.symbol)}</span>
    <span class="dir {candidate.direction}">{candidate.direction.upper()}</span>
    <span class="setup-tag">{_esc(candidate.setup.kind.value)}</span>
    <span class="co">{_esc(candidate.company)}</span>
    <span class="score">{candidate.score:.1f}</span>
  </div>
  <div class="levels">
    <div class="lv"><div class="k">Entry</div><div class="v">{plan.entry:,.2f}</div></div>
    <div class="lv stop"><div class="k">Stop</div>
      <div class="v">{plan.stop:,.2f} <span style="font-size:11px">({plan.stop_pct:.2f}%)</span></div></div>
    <div class="lv target"><div class="k">4R target</div>
      <div class="v">{plan.target:,.2f} <span style="font-size:11px">({plan.target_pct:+.1f}%)</span></div></div>
    <div class="lv"><div class="k">Quantity</div><div class="v">{plan.quantity}</div></div>
  </div>
  <div class="rr">
    <div class="track">
      <div class="risk" style="width:{risk_pct:.1f}%"><span>risk {risk:,.2f}</span></div>
      <div class="reward" style="width:{100 - risk_pct:.1f}%"><span>reward {reward:,.2f}</span></div>
    </div>
  </div>
  <div class="why"><dl>
    <dt>Why this stock</dt><dd>RS vs NIFTY <b>{candidate.rs_nifty_pct:.0f}th</b> pct ·
      vs sector <b>{candidate.rs_sector_pct:.0f}th</b> · acceleration
      {candidate.rs_accel_pct:.0f}th</dd>
    <dt>Why this sector</dt><dd>{_esc(candidate.sector)} — <b>{_esc(candidate.sector_state)}</b>
      ({candidate.sector_percentile:.0f}th pct)</dd>
    <dt>Why now</dt><dd>{_esc(candidate.why_now)}</dd>
    <dt>Why it can move</dt><dd>ADR <b>{candidate.adr_pct:.1f}%</b> · ATR
      {candidate.atr_pct:.1f}% · needs <b>{plan.target_pct:.1f}%</b></dd>
    <dt>Structure</dt><dd class="chain">W {_esc(candidate.weekly_note)} &rarr;
      D {_esc(candidate.daily_note)}""" + (
        f" &rarr; 60m {_esc(candidate.hourly_note)}" if candidate.hourly_note else ""
    ) + f"""</dd>
    <dt>Trigger</dt><dd>{trigger}</dd>
    <dt>Where I'm wrong</dt><dd><b>{_esc(plan.invalidation)}</b></dd>
  </dl></div>
</div>"""


def build_v3_html(scan: V3Scan) -> str:
    regime_class = "pos" if scan.regime == "aggressive" else "caution"
    setups = "".join(_card(c) for c in scan.trades) or (
        '<div class="empty"><div class="h">Nothing qualified today.</div>'
        f'<div class="p">V3 targets roughly {settings.target_setups_per_month} setups a '
        "month, not a daily list. Most sessions produce none, and the specification is "
        "explicit that the 4R and stop-distance requirements never loosen to fill a "
        "quota.</div></div>"
    )

    rejects = ""
    if scan.reject_counts:
        rows = "".join(
            f"<tr><td>{_esc(name)}</td><td class='num'>{count}</td></tr>"
            for name, count in sorted(scan.reject_counts.items(), key=lambda kv: -kv[1])
        )
        rejects = (
            "<section><h2>Which filter was binding</h2>"
            "<p class='lede'>The hard filters are the only things that may reject: 4R "
            "feasibility, stop distance, liquidity and technical validity.</p>"
            f"<div class='scroller'><table><thead><tr><th>Hard filter</th>"
            f"<th style='text-align:right'>n</th></tr></thead><tbody>{rows}</tbody>"
            "</table></div></section>"
        )

    watch = ""
    if scan.near_miss:
        rows = "".join(
            f"<tr><td style='font-family:var(--mono)'>{_esc(c.symbol)}</td>"
            f"<td>{c.direction}</td><td>{_esc(c.reject_detail or c.rejected_by)}</td></tr>"
            for c in scan.near_miss[:15]
        )
        watch = (
            "<section><h2>Watch</h2>"
            "<p class='lede'>Cleared the hard filters or came close, but not today's best. "
            "Worth checking tomorrow.</p>"
            f"<div class='scroller'><table><thead><tr><th>Symbol</th><th>Dir</th>"
            f"<th>Why not</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
        )

    return f"""<title>V3 Setups — {_esc(scan.as_of)}</title>
<style>{_CSS}</style>
<div class="wrap">
  <header class="masthead">
    <div class="brand">
      <h1>Specification V3</h1>
      <div class="sub">{_esc(scan.as_of)} · NIFTY 500 · long + short · 1–5 sessions</div>
    </div>
    <div class="chips">
      <span class="chip {regime_class}">{_esc(scan.regime_note)}</span>
      <span class="chip">4R minimum</span>
      <span class="chip">stop {settings.min_stop_pct:.1f}–{settings.v3_max_stop_pct:.1f}%</span>
      <span class="chip">{_esc(scan.tier)}</span>
    </div>
  </header>

  <section>
    <h2>Selectivity</h2>
    <p class="lede">The rejections are the product. V3 targets roughly
      {settings.target_setups_per_month} setups a month, so the funnel is meant to be brutal.</p>
    {_funnel(scan)}
  </section>

  <section>
    <h2>Today's setups</h2>
    <p class="lede">{scan.cleared_floor} cleared the quality floor; the best
      {len(scan.trades)} are shown.</p>
    {setups}
  </section>

  {rejects}
  {watch}

  <div class="note">
    <b>Decision support, not execution.</b> Every level here is a setup for your own
    judgement — never an instruction to buy or sell. This system holds no credentials that
    can trade and contains no order-placement code.
  </div>

  <footer>
    <span>Right market → right sector → right stock → right time → right risk/reward</span>
    <span class="num">Generated {datetime.now():%d %b %Y %H:%M}</span>
  </footer>
</div>"""


def write_v3_html(scan: V3Scan) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{scan.as_of}-v3.html"
    path.write_text(build_v3_html(scan), encoding="utf-8")
    return str(path)
