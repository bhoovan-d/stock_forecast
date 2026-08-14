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
    groq_model: str = "llama-3.3-70b-versatile"
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
    # Seven factors, six at 15% and volatility at 10%. Sector leadership is a *scoring*
    # factor here, not a gate: V3 §16 names the hard filters explicitly as 4R, stop
    # distance, liquidity and basic technical validity, and nothing else may reject.
    v3_weight_rs_nifty: float = 0.15
    v3_weight_rs_sector: float = 0.15
    v3_weight_sector_leadership: float = 0.15
    v3_weight_structure: float = 0.15
    v3_weight_entry_quality: float = 0.15
    v3_weight_catalyst: float = 0.15
    v3_weight_volatility: float = 0.10

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
