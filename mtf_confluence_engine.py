from __future__ import annotations

"""
Market Mind V2 — Multi-Timeframe Confluence Layer

Combines:
- Existing M5 confluence evidence
- M5, M15, H1, and H4 structural biases
- Higher-timeframe conflict detection

All scores are heuristic. They are not probabilities or validated
performance measures. This module does not place trades.
"""

TIMEFRAME_WEIGHTS = {
    "H4": 4.0,
    "H1": 3.0,
    "M15": 2.0,
    "M5": 1.0,
}

M5_WEIGHT = 0.60
MTF_WEIGHT = 0.40


def normalize_bias(value) -> str:
    text = str(value or "").strip().upper()

    if "BULL" in text:
        return "BULLISH"

    if "BEAR" in text:
        return "BEARISH"

    return "NEUTRAL"


def safe_score(result, possible_keys) -> float:
    """Read a score without failing if the key name differs."""

    for key in possible_keys:
        value = result.get(key)

        try:
            number = float(value)
            if number == number and abs(number) != float("inf"):
                return max(0.0, min(100.0, number))
        except (TypeError, ValueError):
            continue

    return 0.0


def get_m5_scores(m5_confluence):
    bullish = safe_score(
        m5_confluence,
        ("bullish_score", "bullish_evidence", "bullish_points"),
    )

    bearish = safe_score(
        m5_confluence,
        ("bearish_score", "bearish_evidence", "bearish_points"),
    )

    return bullish, bearish


def calculate_mtf_context(biases):
    """
    Higher timeframes carry more weight.
    Neutral timeframes contribute no directional points.
    """

    bullish_points = 0.0
    bearish_points = 0.0
    available_weight = 0.0
    normalized = {}

    for timeframe, weight in TIMEFRAME_WEIGHTS.items():
        bias = normalize_bias(biases.get(timeframe))
        normalized[timeframe] = bias

        if bias == "BULLISH":
            bullish_points += weight
            available_weight += weight
        elif bias == "BEARISH":
            bearish_points += weight
            available_weight += weight

    if available_weight == 0:
        return {
            "biases": normalized,
            "bullish_share": 50.0,
            "bearish_share": 50.0,
            "net_score": 0.0,
            "direction": "NEUTRAL",
            "available_weight": 0.0,
        }

    bullish_share = 100.0 * bullish_points / available_weight
    bearish_share = 100.0 * bearish_points / available_weight
    net_score = bullish_share - bearish_share

    if net_score > 0:
        direction = "BULLISH"
    elif net_score < 0:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    return {
        "biases": normalized,
        "bullish_share": bullish_share,
        "bearish_share": bearish_share,
        "net_score": net_score,
        "direction": direction,
        "available_weight": available_weight,
    }


def evaluate_mtf_confluence(m5_confluence, timeframe_biases):
    """
    Produce a separate multi-timeframe context assessment.

    This does not overwrite or modify the original M5 confluence result.
    """

    mtf = calculate_mtf_context(timeframe_biases)
    bullish_m5, bearish_m5 = get_m5_scores(m5_confluence)

    m5_total = bullish_m5 + bearish_m5

    if m5_total > 0:
        m5_bullish_share = 100.0 * bullish_m5 / m5_total
        m5_bearish_share = 100.0 * bearish_m5 / m5_total
    else:
        m5_bullish_share = 50.0
        m5_bearish_share = 50.0

    mtf_bullish_share = mtf["bullish_share"]
    mtf_bearish_share = mtf["bearish_share"]

    composite_bullish = (
        M5_WEIGHT * m5_bullish_share
        + MTF_WEIGHT * mtf_bullish_share
    )

    composite_bearish = (
        M5_WEIGHT * m5_bearish_share
        + MTF_WEIGHT * mtf_bearish_share
    )

    m5_direction = normalize_bias(
        m5_confluence.get("structure_bias")
        or m5_confluence.get("bias")
        or m5_confluence.get("direction")
    )

    if m5_direction == "NEUTRAL":
        if bullish_m5 > bearish_m5:
            m5_direction = "BULLISH"
        elif bearish_m5 > bullish_m5:
            m5_direction = "BEARISH"

    h4_bias = mtf["biases"]["H4"]
    h1_bias = mtf["biases"]["H1"]
    m15_bias = mtf["biases"]["M15"]
    m5_bias = mtf["biases"]["M5"]

    reasons = []

    if m5_direction != "NEUTRAL":
        reasons.append(f"M5 confluence direction: {m5_direction}.")
    else:
        reasons.append("M5 confluence direction is unclear.")

    reasons.append(
        f"Weighted multi-timeframe direction: {mtf['direction']}."
    )

    if h4_bias != "NEUTRAL" and m5_direction != "NEUTRAL":
        if h4_bias != m5_direction:
            classification = "MIXED — HIGHER-TIMEFRAME CONFLICT"
            reasons.append(
                f"H4 is {h4_bias}, opposing the M5 direction."
            )
        elif mtf["direction"] == m5_direction:
            classification = f"{m5_direction} CONTEXT ALIGNED"
            reasons.append(
                "H4 and the weighted multi-timeframe direction "
                "support the M5 direction."
            )
        else:
            classification = "MIXED — TIMEFRAMES DISAGREE"
            reasons.append(
                "The weighted timeframe direction does not confirm M5."
            )
    elif (
        m5_direction != "NEUTRAL"
        and mtf["direction"] == m5_direction
    ):
        classification = f"{m5_direction} CONTEXT, H4 UNCONFIRMED"
        reasons.append(
            "The weighted direction agrees with M5, but H4 is neutral."
        )
    else:
        classification = "LOW / MIXED DIRECTIONAL AGREEMENT"
        reasons.append(
            "There is not enough directional agreement for an aligned label."
        )

    # Explicitly report conflicts instead of hiding them in a score.
    conflicts = []

    directional_biases = {
        "M5": m5_bias,
        "M15": m15_bias,
        "H1": h1_bias,
        "H4": h4_bias,
    }

    for timeframe, bias in directional_biases.items():
        if bias != "NEUTRAL" and m5_direction != "NEUTRAL":
            if bias != m5_direction:
                conflicts.append(
                    f"{timeframe} ({bias}) conflicts with M5 "
                    f"({m5_direction})"
                )

    return {
        "classification": classification,
        "m5_direction": m5_direction,
        "m5_bullish_share": round(m5_bullish_share, 1),
        "m5_bearish_share": round(m5_bearish_share, 1),
        "mtf_direction": mtf["direction"],
        "mtf_bullish_share": round(mtf_bullish_share, 1),
        "mtf_bearish_share": round(mtf_bearish_share, 1),
        "composite_bullish": round(composite_bullish, 1),
        "composite_bearish": round(composite_bearish, 1),
        "timeframe_biases": mtf["biases"],
        "conflicts": conflicts,
        "reasons": reasons,
        "method": (
            "Exploratory weighted heuristic: 60% normalized M5 evidence "
            "and 40% weighted timeframe direction. Not calibrated."
        ),
    }


def display_mtf_confluence(result):
    print("\n" + "=" * 64)
    print("MARKET MIND V2 — MULTI-TIMEFRAME CONFLUENCE")
    print("=" * 64)

    print(f"Classification: {result['classification']}")
    print(f"M5 direction:   {result['m5_direction']}")
    print(f"MTF direction:  {result['mtf_direction']}")

    print("\nTIMEFRAME BIASES")
    print("-" * 42)

    for timeframe in ("H4", "H1", "M15", "M5"):
        print(
            f"{timeframe:>3}: "
            f"{result['timeframe_biases'].get(timeframe, 'NEUTRAL')}"
        )

    print("\nNORMALIZED DIRECTIONAL CONTEXT")
    print("-" * 42)
    print(
        f"M5 bullish evidence share: "
        f"{result['m5_bullish_share']:.1f}/100"
    )
    print(
        f"M5 bearish evidence share: "
        f"{result['m5_bearish_share']:.1f}/100"
    )
    print(
        f"MTF bullish share:          "
        f"{result['mtf_bullish_share']:.1f}/100"
    )
    print(
        f"MTF bearish share:          "
        f"{result['mtf_bearish_share']:.1f}/100"
    )

    print("\nEXPLORATORY COMPOSITE")
    print("-" * 42)
    print(f"Bullish context: {result['composite_bullish']:.1f}/100")
    print(f"Bearish context: {result['composite_bearish']:.1f}/100")

    print("\nREASONS")
    for reason in result["reasons"]:
        print(f"  • {reason}")

    if result["conflicts"]:
        print("\nTIMEFRAME CONFLICTS")
        for conflict in result["conflicts"]:
            print(f"  ! {conflict}")

    print("\nIMPORTANT")
    print(result["method"])
    print(
        "These scores are not probabilities or verified trading edges. "
        "Use historical testing before relying on them."
    )
    print("=" * 64)