"""Deterministic post-validator for the ETV report.

Enforces arithmetic and identity invariants the LLM cannot be trusted
with: probability normalisation, scenario-price identity, intrinsic-vs-
tradable separation, weighted ETV, asymmetry, decision gates.
"""
from __future__ import annotations

_DECOMP_KEYS = (
    "fundamental",
    "regime_adjustment",
    "market_expectations_adjustment",
    "optionality",
    "behavioral_premium",
)


def validate_report(report: dict, spot: float | None) -> dict:
    """Deterministic post-validation.

    Enforces:
      * scenario probabilities sum to 100 (normalised)
      * each scenario `price` equals Σ(value_decomposition) within ±$1
      * `etv.probability_weighted_etv` = Σ(p_s × price_s) / 100
      * `economic_value.central_estimate` = same weighted sum
      * `etv.expected_return_pct` = (ETV − spot) / spot × 100
      * `asymmetry.ratio` = |weighted upside %| / |weighted downside %|
      * `decision.confidence_pct` ≤ 90
      * Decision = NO TRADE if asymmetry < 2 OR confidence < 55

    Emits a `validation` block with corrections applied and warnings raised.
    Mutates `report` in place; returns it.
    """
    warnings: list[str] = []
    corrections: list[str] = []

    def _fix_scenarios(block_name: str) -> tuple[float, dict, dict, dict]:
        block = report.get(block_name) or {}
        scns = {s: (block.get(s) or {}) for s in ("bear", "base", "bull")}
        # Probability normalisation
        probs = {s: float(scns[s].get("probability_pct") or 0) for s in scns}
        total = sum(probs.values())
        if total <= 0:
            warnings.append(f"{block_name}: all probabilities zero/missing")
            return 0.0, *scns.values()  # type: ignore[return-value]
        if abs(total - 100) > 0.5:
            for s in scns:
                scns[s]["probability_pct"] = round(probs[s] * 100.0 / total, 1)
            corrections.append(
                f"{block_name}: probabilities normalised from {total:.1f} to 100"
            )
            probs = {s: scns[s]["probability_pct"] for s in scns}
        # Weighted price (identity enforcement below will refine prices)
        wprice = sum(probs[s] * float(scns[s].get("price") or 0) for s in scns) / 100.0
        return wprice, scns["bear"], scns["base"], scns["bull"]

    # Economic-value block — probabilities only at this stage
    _econ_w_pre, _eb, _ebase, _ebull = _fix_scenarios("economic_value")
    econ_block = report.get("economic_value") or {}

    # ETV block — probabilities only
    _etv_w_pre, eb, ebase, ebull = _fix_scenarios("etv")
    etv_block = report.get("etv") or {}

    # Enforce identity: economic_value = intrinsic (fundamental only),
    #                   etv = fundamental + 4 layered components
    econ_scns = {"bear": _eb, "base": _ebase, "bull": _ebull}
    etv_scns = {"bear": eb, "base": ebase, "bull": ebull}
    for s in ("bear", "base", "bull"):
        ev = econ_scns[s]
        et = etv_scns[s]
        ev_d = ev.get("value_decomposition") or {}
        et_d = et.get("value_decomposition") or {}
        # 1. economic_value = STRICT intrinsic (fundamental only).
        #    Zero out the other four components; set price = fundamental.
        if ev_d:
            zeroed: list[str] = []
            for k in ("regime_adjustment", "market_expectations_adjustment",
                      "optionality", "behavioral_premium"):
                v = ev_d.get(k)
                if v is not None and abs(float(v)) > 0.5:
                    zeroed.append(f"{k}={float(v):+.0f}")
                ev_d[k] = 0
            fund = float(ev_d.get("fundamental") or 0)
            old_ev_price = ev.get("price")
            if old_ev_price is None or abs(float(old_ev_price) - fund) > 1:
                ev["price"] = round(fund)
                corrections.append(
                    f"economic_value.{s}.price: {old_ev_price} → ${ev['price']} (= fundamental)"
                )
            if zeroed:
                corrections.append(
                    f"economic_value.{s}: zeroed non-fundamental components ({', '.join(zeroed)})"
                )
            ev["value_decomposition"] = ev_d
        # 2. Force ETV.fundamental to match economic_value.fundamental
        ev_fund = ev_d.get("fundamental") if ev_d else None
        if ev_fund is not None:
            et_fund = et_d.get("fundamental")
            if et_fund is None or abs(float(et_fund) - float(ev_fund)) > 0.5:
                et_d["fundamental"] = ev_fund
                corrections.append(
                    f"etv.{s}.fundamental: {et_fund} → {ev_fund} (match economic intrinsic)"
                )
        # 3. Recompute ETV price = fundamental + the 4 layered components
        if et_d:
            new_etv_price = sum(float(et_d.get(k) or 0) for k in _DECOMP_KEYS)
            old_etv_price = et.get("price")
            if old_etv_price is None or abs(float(old_etv_price) - new_etv_price) > 1:
                et["price"] = round(new_etv_price)
                corrections.append(
                    f"etv.{s}.price: {old_etv_price} → ${et['price']} (= Σ decomposition)"
                )
            et["value_decomposition"] = et_d
        # 4. Force matching probabilities (econ wins — it's the structural anchor)
        if ev.get("probability_pct") is not None and \
                et.get("probability_pct") != ev.get("probability_pct"):
            old_p = et.get("probability_pct")
            et["probability_pct"] = ev["probability_pct"]
            corrections.append(
                f"etv.{s}.probability_pct: {old_p} → {ev['probability_pct']} (match econ)"
            )

    # Recompute weighted sums AFTER identity enforcement
    etv_w = sum(float(etv_scns[s].get("probability_pct") or 0)
                * float(etv_scns[s].get("price") or 0)
                for s in etv_scns) / 100.0
    econ_w = sum(float(econ_scns[s].get("probability_pct") or 0)
                 * float(econ_scns[s].get("price") or 0)
                 for s in econ_scns) / 100.0
    if econ_w:
        econ_block["central_estimate"] = round(econ_w)

    if etv_w:
        old = etv_block.get("probability_weighted_etv")
        new = round(etv_w, 2)
        if old is None or abs(float(old) - new) > 0.5:
            etv_block["probability_weighted_etv"] = new
            corrections.append(
                f"etv.probability_weighted_etv: {old} → {new} (weighted)"
            )
        # Aggregate decomposition (probability-weighted components)
        agg = {k: 0.0 for k in _DECOMP_KEYS}
        any_present = False
        for s, sc in (("bear", eb), ("base", ebase), ("bull", ebull)):
            decomp = sc.get("value_decomposition") or {}
            p = float(sc.get("probability_pct") or 0) / 100.0
            for k in _DECOMP_KEYS:
                v = decomp.get(k)
                if v is not None:
                    any_present = True
                    agg[k] += p * float(v)
        if any_present:
            etv_block["weighted_decomposition"] = {k: round(v, 2) for k, v in agg.items()}
            etv_block["weighted_decomposition_sum"] = round(sum(agg.values()), 2)
        # Expected return
        if spot and spot > 0:
            er = (new - spot) / spot * 100.0
            old_er = etv_block.get("expected_return_pct")
            if old_er is None or abs(float(old_er) - er) > 0.3:
                etv_block["expected_return_pct"] = round(er, 2)
                corrections.append(
                    f"etv.expected_return_pct: {old_er} → {round(er, 2)}"
                )
            etv_block["current_price"] = spot

    # Asymmetry
    asym_block = report.get("asymmetry") or {}
    if spot and spot > 0 and etv_w:
        up = 0.0
        dn = 0.0
        for s, sc in (("bear", eb), ("base", ebase), ("bull", ebull)):
            p = float(sc.get("probability_pct") or 0) / 100.0
            px = float(sc.get("price") or 0)
            ret = (px - spot) / spot * 100.0
            if ret >= 0:
                up += p * ret
            else:
                dn += p * abs(ret)
        ratio = (up / dn) if dn > 1e-6 else float("inf")
        asym_block["upside_pct_weighted"] = round(up, 2)
        asym_block["downside_pct_weighted"] = round(dn, 2)
        asym_block["ratio"] = round(ratio, 2) if ratio != float("inf") else None
        corrections.append(
            f"asymmetry: upside={up:.1f}%, downside={dn:.1f}%, ratio={ratio:.2f}"
        )

    # Decision rule enforcement
    dec_block = report.get("decision") or {}
    conf = float(dec_block.get("confidence_pct") or 0)
    if conf > 90:
        dec_block["confidence_pct"] = 90
        corrections.append(f"decision.confidence_pct: {conf} → 90 (cap)")
        conf = 90
    ratio = asym_block.get("ratio")
    no_trade_reasons: list[str] = []
    if isinstance(ratio, (int, float)) and ratio < 2:
        no_trade_reasons.append(f"asymmetry {ratio:.2f} < 2")
    if conf < 55:
        no_trade_reasons.append(f"confidence {conf:.0f} < 55")
    if no_trade_reasons and dec_block.get("decision") == "TRADE":
        dec_block["decision"] = "NO TRADE"
        dec_block["direction"] = "NEUTRAL"
        corrections.append("decision: TRADE → NO TRADE (" + "; ".join(no_trade_reasons) + ")")

    report["validation"] = {
        "warnings": warnings,
        "corrections": corrections,
        "passed": len(warnings) == 0,
    }
    return report
