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

    # ── Selection weights (Engine 3) ──────────────────────────────────────────
    # Catalyst is weighted highest by design: the brief calls for most effort there.
    weight_catalyst: float = 0.35
    weight_relative_strength: float = 0.25
    weight_price_structure: float = 0.20
    weight_volume: float = 0.15
    weight_liquidity: float = 0.05

    # ── Trade engine (Engine 5) ───────────────────────────────────────────────
    # The non-negotiable gate. A plan below this R is not emitted at all.
    min_reward_risk: float = 2.0
    risk_budget_inr: float = 5000.0
    atr_period: int = 14
    atr_stop_multiple: float = 1.5

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
