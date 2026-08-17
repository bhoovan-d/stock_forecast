"""Settings for the Asymmetry Engine.

Secrets and tunables live in ``.env``; nothing here is hardcoded into engine logic. The
factor weights and the R:R gate in particular are meant to be edited as the system is
tuned against live results, so they are settings rather than constants.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
BRIEF_DIR = DATA_DIR / "briefs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Upstox (live tier) ────────────────────────────────────────────────────
    # The user completes the OAuth login themselves and pastes the resulting token here.
    # We never handle their credentials, and no order-placement endpoint is implemented.
    upstox_access_token: str | None = None
    upstox_api_key: str | None = None
    upstox_api_secret: str | None = None
    upstox_redirect_uri: str = "http://localhost:8080/callback"

    # ── LLM cascade (catalyst engine) ─────────────────────────────────────────
    llm_provider_chain: str = "cerebras,groq,gemini,anthropic"

    cerebras_api_key: str | None = None
    cerebras_model: str = "gpt-oss-120b"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"

    groq_api_key: str | None = None
    # `llama-3.3-70b-versatile` was decommissioned and 404s on this account — checked
    # against Groq's /models on 18 Aug 2026, which no longer lists any llama chat model.
    # A dead link in the chain is not free: the failure costs a full 60s timeout per item
    # before the cascade moves on, which is most of why a day of filings took 165s.
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    # ── Universe / liquidity gate ─────────────────────────────────────────────
    universe_index: str = "nifty500"
    # A stock must clear this median daily turnover (INR) to be tradeable at all.
    min_median_turnover_inr: float = 5.0e7  # ₹5 crore
    min_median_volume: float = 50_000
    liquidity_lookback_days: int = 20

    # ── V3 scoring weights (Spec V3 §16) ──────────────────────────────────────
    # Sector leadership is a *scoring* factor here, not a gate: V3 §16 names the hard filters
    # explicitly as 4R, stop distance, liquidity and basic technical validity, and nothing
    # else may reject.
    #
    # `structure` is the weekly/daily trend the trade is taken with or against. It used to be
    # fed the setup detector's own quality by mistake, which is why a name in a weekly
    # downtrend could score 90 on "structure"; the daily setup now has its own module so the
    # two cannot be confused again. `carry` is the 60m/120m continuation grade and carries
    # the largest single weight — it answers whether a position survives 1-5 sessions, which
    # is the question the engine previously never asked.
    #
    # These must sum to 1.0; `test_v3_weights_sum_to_one` enforces it.
    v3_weight_rs_nifty: float = 0.12
    v3_weight_rs_sector: float = 0.12
    v3_weight_sector_leadership: float = 0.10
    v3_weight_structure: float = 0.12
    v3_weight_setup_quality: float = 0.10
    v3_weight_entry_quality: float = 0.08
    v3_weight_catalyst: float = 0.12
    v3_weight_volatility: float = 0.06
    v3_weight_carry: float = 0.18

    # ── V3 carry gate (60m/120m) ──────────────────────────────────────────────
    # A setup that clears every checklist condition still has to grade out. Missing 60m data
    # fails closed: unproven is not the same as fine.
    v3_carry_score_floor: float = 60.0

    # Which setups the carry gate may reject.
    #
    # Measured, not chosen. Over 2,527 replayed 15m triggers the gate moved mean R by setup:
    #
    #     base-breakout   +0.75R -> +1.59R   (65 -> 9 trades)
    #     continuation    -0.80R -> -0.42R   (1,780 -> 69)
    #     reclaim         +0.30R -> -0.07R   (682 -> 35)
    #
    # It destroys the edge on the one setup that had one. A sweep-and-reclaim is a
    # counter-trend entry by construction — price has just taken out a prior low — so a
    # continuation test selects the reclaims that are already extended and aligned, which
    # are precisely the ones with the least asymmetry left. The gate is a continuation-regime
    # test, so it applies only to setups that are continuation trades. Carry is still
    # measured and reported for the others; it simply cannot reject them.
    v3_carry_gated_setups: str = "base-breakout,continuation"
    # Two components are requirements, not contributors. A high total assembled from
    # alignment and range position while volume is absent and the next opposing level sits
    # inside the target is not a carry setup — it is the score hiding a fatal miss, which is
    # the failure the whole gate exists to prevent.
    v3_carry_min_volume_score: float = 40.0
    v3_carry_min_headroom_score: float = 40.0
    # Base breakout: the base must be tight, and the breakout bar must carry real volume.
    # Volume is the discriminator — price clearing a base is common and mostly worthless.
    base_breakout_window: int = 8
    base_breakout_max_depth_pct: float = 8.0
    base_breakout_min_volume_mult: float = 2.0

    # Composite relative strength (V3 §6): level *and* direction of strength, so a stock
    # with high but deteriorating RS ranks below one that is high and accelerating.
    rs_weight_vs_nifty: float = 0.40
    rs_weight_vs_sector: float = 0.35
    rs_weight_acceleration: float = 0.25

    # ── V3 hard filters (§1, §13) ─────────────────────────────────────────────
    # A *range*, not just a ceiling. Below the floor the "stop" sits inside normal noise
    # and is not an invalidation level at all.
    min_stop_pct: float = 0.5
    v3_max_stop_pct: float = 1.5
    # Selectivity target (§17). Exceeding it means raising the threshold, never loosening
    # the 4R or stop requirements.
    target_setups_per_month: int = 15

    # ── The fifth hard filter: a catalyst must exist (added 18 Aug 2026) ───────
    # V3 §16 fixed the hard filters at four, and this codebase enforced that: the carry gate
    # reports under *technical validity* precisely so it would not become a fifth. That
    # mapping was honest — a missing 60m carry structure genuinely is a technical
    # invalidity. A missing catalyst is not, and filing it under technical validity to
    # preserve the count would be a lie about why a name was refused.
    #
    # So this is a real fifth filter, added on the owner's explicit instruction. §12 asks
    # every published setup to answer "why now", and until now a name with no answer was
    # merely scored down (catalyst weight 0.12, neutral 50) and published anyway.
    #
    # **Default off, on the measurement.** It was armed when first added and then measured
    # the same day (docs/2026-08-18-catalyst-filter-measurement.md), twice, on both a
    # filings-occurred definition and an LLM-judged one. Neither supports it. Blended it
    # looks positive (+0.11R gate-off, +0.09R admitted) but both figures are setup mix: per
    # setup it removes edge from the two that have any — base-breakout +0.01R with a
    # catalyst against +0.72R without, reclaim +0.06R against +0.12R — and inside the
    # admitted population 69% of the with-catalyst cohort's total R comes from 3 of its 50
    # trades. The largest like-for-like comparison, 47 reclaims against 630, says the
    # filter costs ~0.06R per trade. It also refuses ~93% of candidates: 10 of 135 survived
    # on 14 Aug, PIIND — the only name published that day — among the refused.
    #
    # Not deleted: the code is correct and tested, and one source stays untestable. News
    # RSS serves ~48h and cannot be backfilled, so no historical run sees what the live
    # filter would. Revisit with forward-collected data; `--require-catalyst` arms it.
    v3_require_catalyst: bool = False

    # ── Master scoring weights (Engineer Brief §17) ───────────────────────────
    # Engineering defaults, explicitly *not* claimed optimal. The brief is emphatic that
    # these may only be changed by walk-forward testing, and never tuned on the final test
    # period.
    weight_catalyst: float = 0.20          # why now
    weight_structure: float = 0.15         # daily/weekly structural quality
    weight_relative_strength: float = 0.15 # leadership, incl. RS acceleration
    weight_sector: float = 0.10            # sector/peer confirmation
    weight_volume: float = 0.10            # demand confirmation
    weight_volatility: float = 0.10        # compression -> expansion, move potential
    weight_fno: float = 0.10               # derivative confirmation
    weight_entry_quality: float = 0.05     # 15m/30m trigger quality
    weight_regime: float = 0.05            # environment

    # ── Hard gates (Engineer Brief §1, §13, §18) ──────────────────────────────
    # Both are non-negotiable rejections, not score penalties. A setup failing either is
    # not downgraded — it is refused.
    min_reward_risk: float = 4.0
    # The legacy daily-screening brief keeps its own, looser gate. The two engines answer
    # different questions — the screen surveys the market, the specification hunts a rare
    # asymmetric setup — and sharing one constant would silently retune the screen (and the
    # deployed CI brief) every time the specification is adjusted.
    screen_min_reward_risk: float = 2.0
    # Maximum initial stop distance as a percentage of entry. If the technically valid
    # invalidation sits further away, the setup is REJECTED; the stop is never tightened
    # to manufacture the R multiple.
    max_stop_pct: float = 1.4
    # Holding horizon in trading sessions.
    min_holding_sessions: int = 1
    max_holding_sessions: int = 5

    risk_budget_inr: float = 5000.0
    atr_period: int = 14
    atr_stop_multiple: float = 1.5

    # ── Costs, for expected value (Brief §16) ─────────────────────────────────
    # Round-trip cost as a fraction of turnover: brokerage, STT, exchange and stamp duty,
    # GST. EV that ignores these systematically overstates the edge.
    cost_roundtrip_pct: float = 0.12
    slippage_pct: float = 0.05
    # Probability that an overnight gap executes the stop worse than its stated price.
    gap_risk_pct: float = 0.15

    # ── Resistance clearance (Brief §14, §18) ─────────────────────────────────
    # A 4R target sitting beyond major resistance is only accepted when the model assigns
    # at least this probability to clearing that level.
    min_resistance_clearance_prob: float = 0.35
    # Resistance within this fraction of the move to target counts as "before" the target.
    resistance_lookback_weeks: int = 104

    # ── Macro fair value (Engine 4) ───────────────────────────────────────────
    macro_lookback_days: int = 250
    macro_ridge_alpha: float = 1.0
    # Below this R², the macro gap is reported but explicitly marked unreliable and
    # contributes nothing to ranking. Without this guard the gap is noise.
    macro_min_r2: float = 0.40

    # ── Regime (Engine 1) ─────────────────────────────────────────────────────
    vix_percentile_lookback_days: int = 252
    breadth_ma_period: int = 50
    risk_free_rate: float = 0.065  # for Black-Scholes IV solve

    # ── HTTP ──────────────────────────────────────────────────────────────────
    # Yahoo throttles hard: a fast burst of ~11 symbols failed outright during testing,
    # while the same symbols succeeded when paced. These defaults are not optional.
    yahoo_min_interval_sec: float = 1.2
    yahoo_max_retries: int = 4
    http_timeout_sec: float = 30.0
    cache_ttl_intraday_sec: int = 300
    cache_ttl_daily_sec: int = 3600

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "asymmetry.db"


settings = Settings()

for _d in (DATA_DIR, CACHE_DIR, BRIEF_DIR):
    _d.mkdir(parents=True, exist_ok=True)
