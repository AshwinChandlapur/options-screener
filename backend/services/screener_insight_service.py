"""
LLM-powered trade insight for a single CSP screener row — v2 regime-aware.

Produces cycle-adjusted value bands (Bear / Normal / Bull) for any ticker,
evaluates the CSP strike against those bands, and applies a VIX × cycle
matrix gate before issuing ENTER / WAIT / SKIP.

Enriches each request with company profile + VIX regime from data_service
before calling Azure OpenAI. The LLM is instructed not to hallucinate facts
beyond what it receives.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Literal, Optional

from openai import AzureOpenAI

from services.data_service import get_news, get_ohlc, get_ticker_info

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Azure OpenAI config — same env vars as dcf_service / llm_extractor
# ---------------------------------------------------------------------------
_AZURE_KEY = os.getenv("AZURE_OPENAI_KEY", "")
_AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
_AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InsightRequest:
    symbol: str
    price: float
    strike: float
    premium: float
    dte: int
    expiration: str
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str        # e.g. "IVP:25 Tr:15 SMA:3 SLP:0 RSI:16 OI:18"
    strike_detail: str     # e.g. "Δ:20 BA:24 LQ:15 ROC:35"
    roc_annualized: Optional[float]
    rsi: float
    iv_hv_ratio: Optional[float]      # kept for back-compat — NOT used in prompt
    iv_percentile: Optional[float]    # v3.3 scored ENV factor (0–100)
    dist_from_52w_high_pct: float


@dataclass(frozen=True)
class InsightResult:
    verdict: Literal["ENTER", "WAIT", "SKIP"]
    confidence: float
    summary: str
    regime_drivers: str    # "BTC price + AI data center capex"
    current_regime: str    # "Mid-cycle — BTC ~$82K, recovering from Jan lows"
    stock_cycle: str       # "Bear" | "Normal" | "Bull"
    bear_band: str         # "$15–$35"
    normal_band: str       # "$40–$65"
    bull_band: str         # "$80+" or "$80–$120+"
    strike_context: str    # "Strike $50 sits at the floor of Normal — solid if mid-cycle holds"
    key_risk: str          # single sentence
    vix_regime: str        # "Calm" | "Normal" | "Elevated" | "Panic" | "Unknown"

class InsightError(Exception):
    """Raised when the insight call cannot be completed."""


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

_ENV_MAX = {"IVP": 35, "Tr": 15, "SMA": 5, "SLP": 5, "RSI": 20, "OI": 20}
_ENV_LABELS = {
    "IVP": "IV Percentile",
    "Tr":  "52W High Distance",
    "SMA": "SMA50/200 Alignment",
    "SLP": "SMA50 10d Slope",
    "RSI": "RSI(14)",
    "OI": "Chain Liquidity",
}
_STRIKE_MAX = {"Δ": 25, "BA": 25, "LQ": 15, "ROC": 35}
_STRIKE_LABELS = {
    "Δ": "Delta Position",
    "BA": "Bid-Ask Spread",
    "LQ": "Strike Liquidity",
    "ROC": "Annualised Return",
}


def _parse_detail(detail: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in detail.split():
        idx = part.find(":")
        if idx > 0:
            try:
                out[part[:idx]] = float(part[idx + 1:])
            except ValueError:
                pass
    return out


def _format_factors(detail: str, max_map: dict[str, int], label_map: dict[str, str]) -> list[dict]:
    pts = _parse_detail(detail)
    result = []
    for key, max_val in max_map.items():
        earned = pts.get(key, 0.0)
        result.append({
            "factor": label_map.get(key, key),
            "earned": round(earned),
            "max": max_val,
            "pct": round(earned / max_val * 100) if max_val else 0,
        })
    return result


def _compute_1d_change(symbol: str) -> Optional[float]:
    """Returns today's 1-day % price change, or None on failure."""
    try:
        df = get_ohlc(symbol, period="5d")
        if len(df) < 2:
            return None
        prev = float(df["Close"].iloc[-2])
        curr = float(df["Close"].iloc[-1])
        if prev == 0:
            return None
        return round((curr / prev - 1) * 100, 2)
    except Exception as exc:
        logger.warning("1d change fetch failed for %s: %s", symbol, exc)
        return None


_SYSTEM_PROMPT = """\
You are an expert options trader specialising in Cash-Secured Puts (CSP).

Your task is NOT to rephrase the screener score. Your task is to produce an
independent, regime-conditioned framework that helps the trader decide whether
they are comfortable owning 100 shares at the strike price.

Follow these five steps exactly:

STEP 1 — IDENTIFY REGIME DRIVERS
  From ticker_profile (sector, industry, business_summary) and recent_headlines,
  identify 1–2 primary external drivers that determine this stock’s valuation cycle.
  Examples: “BTC price + AI capex”, “consumer spending + commodity costs”,
  “interest rates + credit spreads”, “oil price + refining margins”.
  Output → regime_drivers (10 words max)

STEP 2 — ASSESS CURRENT REGIME
  From recent_headlines and one_day_change_pct, classify the current cycle:
  Bear (stress / contraction), Normal (stable / ranging), or Bull (expansion / momentum).
  Briefly state why (one clause, e.g. “BTC ~$82K, recovering from Jan lows”).
  Output → current_regime (15 words max), stock_cycle (exactly one of: Bear, Normal, Bull)

STEP 3 — PRODUCE VALUE BANDS
  Produce three non-overlapping dollar bands:
  - bear_band:   floor near 52w_low, ceiling below Normal floor
  - normal_band: must satisfy low < current_price < high (brackets current price)
  - bull_band:   floor at Normal ceiling; open-ended top is acceptable (e.g. "$80+")
  Format: "$X–$Y" for bounded, "$X+" for open-ended. Integer dollar values only.
  Output → bear_band, normal_band, bull_band

STEP 4 — EVALUATE THE STRIKE
  Compare the given strike to the three bands.
  State where the strike sits relative to the Normal band floor and the bear band ceiling.
  State what assignment at this strike means in a bear-cycle scenario.
  Output → strike_context (20 words max)

STEP 5 — VERDICT using VIX × cycle matrix
  Use the following gate:

  stock_cycle / vix_regime |  Calm   Normal  Elevated  Panic
  -------------------------+--------------------------------------
  Bear                     |  SKIP   SKIP    WAIT      SKIP
  Normal                   |  WAIT   ENTER   ENTER     WAIT
  Bull                     |  ENTER  ENTER   ENTER     WAIT

  Override rules:
  - If strike > Normal band ceiling → always SKIP (catching falling knife on assignment)
  - If earnings_within_dte is true and stock_cycle is Bear → always SKIP
  - WAIT is preferred over SKIP when regime is uncertain or data is thin

  Output → verdict (ENTER | WAIT | SKIP)

Output rules:
  - Reason ONLY from data you are given. Do not invent or assume facts.
  - If no headlines are provided, reason from ticker_profile and scores alone.
  - summary: 2–3 sentences on the regime + verdict rationale. Do not repeat the numbers.
  - key_risk: one sentence — the single scenario that would cause maximum loss.
  - confidence: 0.0–1.0 reflecting how clearly the data supports the verdict.
"""


def _build_user_prompt(req: InsightRequest, one_day_change_pct: Optional[float], news: list[dict], ticker_profile: dict) -> str:
    env_factors = _format_factors(req.env_detail, _ENV_MAX, _ENV_LABELS)
    strike_factors = _format_factors(req.strike_detail, _STRIKE_MAX, _STRIKE_LABELS)
    payload = {
        "symbol": req.symbol,
        "current_price": req.price,
        "one_day_change_pct": one_day_change_pct,
        "ticker_profile": {
            "sector": ticker_profile.get("sector"),
            "industry": ticker_profile.get("industry"),
            "business_summary": ticker_profile.get("business_summary"),
            "52w_high": ticker_profile.get("52w_high"),
            "52w_low": ticker_profile.get("52w_low"),
        },
        "market_context": {
            "vix": ticker_profile.get("vix_current"),
            "vix_regime": ticker_profile.get("vix_regime", "Unknown"),
        },
        "trade": {
            "strike": req.strike,
            "premium": req.premium,
            "dte": req.dte,
            "expiration": req.expiration,
            "breakeven": round(req.strike - req.premium, 2),
            "otm_pct": round((req.strike - req.price) / req.price * 100, 1) if req.price else None,
        },
        "scores": {
            "final": round(req.final_score, 1),
            "env": round(req.env_score, 1),
            "strike": round(req.strike_score, 1),
        },
        "env_factors": env_factors,
        "strike_factors": strike_factors,
        "supporting_data": {
            "rsi_14": round(req.rsi, 1) if not math.isnan(req.rsi) else None,
            "iv_percentile": round(req.iv_percentile, 1) if req.iv_percentile is not None else None,
            "dist_from_52w_high_pct": round(req.dist_from_52w_high_pct, 1),
            "roc_annualized_pct": round(req.roc_annualized, 1) if req.roc_annualized is not None else None,
        },
        "recent_headlines": news,
    }
    return json.dumps(payload, indent=2)


_RESPONSE_SCHEMA = {
    "name": "screener_insight",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "verdict", "confidence", "summary",
            "regime_drivers", "current_regime", "stock_cycle",
            "bear_band", "normal_band", "bull_band",
            "strike_context", "key_risk",
        ],
        "properties": {
            "verdict":        {"type": "string", "enum": ["ENTER", "WAIT", "SKIP"]},
            "confidence":     {"type": "number"},
            "summary":        {"type": "string"},
            "regime_drivers": {"type": "string"},
            "current_regime": {"type": "string"},
            "stock_cycle":    {"type": "string", "enum": ["Bear", "Normal", "Bull"]},
            "bear_band":      {"type": "string"},
            "normal_band":    {"type": "string"},
            "bull_band":      {"type": "string"},
            "strike_context": {"type": "string"},
            "key_risk":       {"type": "string"},
        },
    },
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_insight(req: InsightRequest) -> InsightResult:
    """
    Fetches news, computes 1d change, calls Azure OpenAI, returns InsightResult.
    Raises InsightError with a human-readable message on failure.
    """
    if not _AZURE_KEY or not _AZURE_ENDPOINT:
        raise InsightError(
            "Azure OpenAI is not configured. Set AZURE_OPENAI_KEY and "
            "AZURE_OPENAI_ENDPOINT in backend/.env"
        )

    news = get_news(req.symbol, max_age_hours=72, max_items=8)
    one_day_change = _compute_1d_change(req.symbol)
    ticker_profile = get_ticker_info(req.symbol)

    client = AzureOpenAI(
        api_key=_AZURE_KEY,
        azure_endpoint=_AZURE_ENDPOINT,
        api_version=_AZURE_API_VERSION,
    )

    try:
        response = client.chat.completions.create(
            model=_AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(req, one_day_change, news, ticker_profile)},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_SCHEMA},
            temperature=0.3,
            max_tokens=900,
        )
    except Exception as exc:
        logger.exception("Azure OpenAI call failed for %s insight", req.symbol)
        raise InsightError(f"LLM call failed: {exc}") from exc

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Insight JSON parse failed for %s: %s — raw: %.200s", req.symbol, exc, raw)
        raise InsightError("LLM returned malformed JSON") from exc

    verdict = data.get("verdict", "WAIT")
    if verdict not in ("ENTER", "WAIT", "SKIP"):
        verdict = "WAIT"

    return InsightResult(
        verdict=verdict,
        confidence=float(max(0.0, min(1.0, data.get("confidence", 0.5)))),
        summary=str(data.get("summary", "")),
        regime_drivers=str(data.get("regime_drivers", "")),
        current_regime=str(data.get("current_regime", "")),
        stock_cycle=str(data.get("stock_cycle", "Normal")),
        bear_band=str(data.get("bear_band", "")),
        normal_band=str(data.get("normal_band", "")),
        bull_band=str(data.get("bull_band", "")),
        strike_context=str(data.get("strike_context", "")),
        key_risk=str(data.get("key_risk", "")),
        vix_regime=str(ticker_profile.get("vix_regime", "Unknown")),
    )
