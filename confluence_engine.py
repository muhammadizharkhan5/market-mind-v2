"""
MARKET MIND V2 — CONFLUENCE ENGINE

Combines:
- Market structure
- Liquidity and sweep candidates
- Fair Value Gaps
- Order Blocks

This is a heuristic analysis module.
It does not place trades or guarantee outcomes.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import fvg_engine
import order_block_engine


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

MIN_CONFLUENCE_SCORE = 60.0
MAX_ZONES_TO_REPORT = 3

# Proximity threshold is based on recent average candle range.
RANGE_LOOKBACK = 14
RANGE_MULTIPLIER = 1.5
MIN_PROXIMITY_PERCENT = 0.0005


# ---------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------

def safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass

    return None


def normalize_direction(value: Any) -> str:
    text = str(value or "").strip().upper()

    if "BULL" in text:
        return "BULLISH"

    if "BEAR" in text:
        return "BEARISH"

    return "NEUTRAL"


def average_candle_range(
    candles: Sequence[Dict[str, Any]]
) -> float:
    """Calculate the mean high-low range of recent candles."""

    recent = candles[-RANGE_LOOKBACK:]
    ranges = []

    for candle in recent:
        high = safe_float(candle.get("high"))
        low = safe_float(candle.get("low"))

        if high is None or low is None or high < low:
            continue

        ranges.append(high - low)

    if not ranges:
        return 0.0

    return sum(ranges) / len(ranges)


def get_zone_status(
    engine_function: Any,
    zone: Dict[str, Any],
    candles: List[Dict[str, Any]]
) -> str:
    """Get status using the existing engine's own logic."""

    try:
        result = engine_function(zone, candles)

        if isinstance(result, dict):
            return str(
                result.get(
                    "status",
                    result.get("state", result.get("label", "UNKNOWN"))
                )
            )

        return str(result)

    except Exception:
        return "STATUS UNKNOWN"


def zone_distance(
    price: float,
    lower: float,
    upper: float
) -> float:
    """Return zero when price is inside a zone."""

    if lower <= price <= upper:
        return 0.0

    if price < lower:
        return lower - price

    return price - upper


def is_zone_near(
    price: float,
    lower: float,
    upper: float,
    threshold: float
) -> bool:
    return zone_distance(price, lower, upper) <= threshold


# ---------------------------------------------------------
# ZONE PREPARATION
# ---------------------------------------------------------

def prepare_zones(
    zones: Any,
    candles: List[Dict[str, Any]],
    status_function: Any,
    current_price: float,
    proximity: float,
    zone_name: str
) -> List[Dict[str, Any]]:
    """Validate zones and attach status and proximity information."""

    prepared = []

    for zone in zones or []:
        if not isinstance(zone, dict):
            continue

        lower = safe_float(zone.get("lower"))
        upper = safe_float(zone.get("upper"))

        if lower is None or upper is None:
            continue

        if lower <= 0 or upper <= 0 or lower > upper:
            continue

        zone_type = normalize_direction(zone.get("type"))

        if zone_type == "NEUTRAL":
            continue

        status = get_zone_status(
            status_function,
            zone,
            candles
        ).upper()

        distance = zone_distance(
            current_price,
            lower,
            upper
        )

        prepared.append({
            "name": zone_name,
            "type": zone_type,
            "lower": lower,
            "upper": upper,
            "distance": distance,
            "near": distance <= proximity,
            "status": status,
            "formed_time": zone.get("formed_time"),
            "source": zone,
        })

    return prepared


def zone_is_usable(zone: Dict[str, Any]) -> bool:
    """Exclude zones whose status clearly says they are no longer valid."""

    status = zone["status"]

    unusable_terms = (
        "INVALIDATED",
        "FULLY MITIGATED",
        "FULLY CROSSED",
        "INVALID DATA",
        "STATUS UNKNOWN",
        "STATUS ERROR",
    )

    return not any(term in status for term in unusable_terms)


# ---------------------------------------------------------
# MAIN CONFLUENCE FUNCTION
# ---------------------------------------------------------

def build_confluence(
    candles: List[Dict[str, Any]],
    structure: Dict[str, Any],
    liquidity: Dict[str, Any],
    fvgs: List[Dict[str, Any]],
    order_blocks: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Combine the four engines into an explainable market assessment.

    Parameters:
        candles:
            Chronologically ordered completed candles.
        structure:
            Result from analyze_market_structure(candles).
        liquidity:
            Result from detect_liquidity(candles).
        fvgs:
            Result from detect_fvgs(candles).
        order_blocks:
            Result from detect_order_blocks(candles).
        current_price:
            Optional trusted current quote.

    IMPORTANT:
        Do not pass a known stale broker quote as current_price.
        If omitted, the latest completed candle close is used.
    """

    if not candles:
        raise ValueError("Confluence requires completed candles.")

    if not isinstance(structure, dict):
        raise ValueError("Structure result must be a dictionary.")

    if not isinstance(liquidity, dict):
        raise ValueError("Liquidity result must be a dictionary.")

    latest_candle = candles[-1]

    if current_price is None:
        price = safe_float(latest_candle.get("close"))
        price_source = "LATEST COMPLETED CANDLE CLOSE"
    else:
        price = safe_float(current_price)
        price_source = "CALLER-PROVIDED QUOTE"

    if price is None or price <= 0:
        raise ValueError("No valid analysis price is available.")

    avg_range = average_candle_range(candles)

    proximity = max(
        avg_range * RANGE_MULTIPLIER,
        price * MIN_PROXIMITY_PERCENT
    )

    # -----------------------------------------------------
    # 1. STRUCTURE BIAS
    # -----------------------------------------------------

    structure_bias = normalize_direction(
        structure.get("bias")
    )

    latest_event = structure.get("latest_event") or {}

    if not isinstance(latest_event, dict):
        latest_event = {}

    event_type = str(
        latest_event.get("type", "")
    ).upper()

    event_direction = normalize_direction(
        latest_event.get("direction")
    )

    # -----------------------------------------------------
    # 2. PREPARE FVG AND ORDER-BLOCK ZONES
    # -----------------------------------------------------

    prepared_fvgs = prepare_zones(
        zones=fvgs,
        candles=candles,
        status_function=fvg_engine.assess_fvg_status,
        current_price=price,
        proximity=proximity,
        zone_name="FVG",
    )

    prepared_obs = prepare_zones(
        zones=order_blocks,
        candles=candles,
        status_function=order_block_engine.assess_block_status,
        current_price=price,
        proximity=proximity,
        zone_name="ORDER BLOCK",
    )

    usable_fvgs = [
        zone for zone in prepared_fvgs
        if zone_is_usable(zone)
    ]

    usable_obs = [
        zone for zone in prepared_obs
        if zone_is_usable(zone)
    ]

    nearby_fvgs = [
        zone for zone in usable_fvgs
        if zone["near"]
    ]

    nearby_obs = [
        zone for zone in usable_obs
        if zone["near"]
    ]

    # -----------------------------------------------------
    # 3. LIQUIDITY SWEEP CANDIDATES
    # -----------------------------------------------------

    buy_sweeps = liquidity.get("buy_sweeps", []) or []
    sell_sweeps = liquidity.get("sell_sweeps", []) or []

    # These are candidates from the liquidity engine, not
    # independent confirmation of a trade setup.
    has_buy_sweep = len(buy_sweeps) > 0
    has_sell_sweep = len(sell_sweeps) > 0

    # -----------------------------------------------------
    # 4. SCORE THE EVIDENCE
    # -----------------------------------------------------

    bullish_points = 0.0
    bearish_points = 0.0

    bullish_reasons = []
    bearish_reasons = []
    neutral_reasons = []

    # Market structure is the main directional input.
    if structure_bias == "BULLISH":
        bullish_points += 35
        bullish_reasons.append(
            "Structure engine reports bullish bias."
        )

    elif structure_bias == "BEARISH":
        bearish_points += 35
        bearish_reasons.append(
            "Structure engine reports bearish bias."
        )

    else:
        neutral_reasons.append(
            "Structure engine has no clear directional bias."
        )

    # Recent structure event can add modest supporting weight.
    if event_direction == "BULLISH":
        bullish_points += 10
        bullish_reasons.append(
            f"Latest structure event is {event_type or 'BULLISH'}."
        )

    elif event_direction == "BEARISH":
        bearish_points += 10
        bearish_reasons.append(
            f"Latest structure event is {event_type or 'BEARISH'}."
        )

    # Nearby uninvalidated FVGs.
    for zone in nearby_fvgs:
        if zone["type"] == "BULLISH":
            bullish_points += 10
            bullish_reasons.append(
                "Price is near a usable bullish FVG."
            )
            break

    for zone in nearby_fvgs:
        if zone["type"] == "BEARISH":
            bearish_points += 10
            bearish_reasons.append(
                "Price is near a usable bearish FVG."
            )
            break

    # Nearby uninvalidated order blocks.
    for zone in nearby_obs:
        if zone["type"] == "BULLISH":
            bullish_points += 15
            bullish_reasons.append(
                "Price is near a usable bullish order block."
            )
            break

    for zone in nearby_obs:
        if zone["type"] == "BEARISH":
            bearish_points += 15
            bearish_reasons.append(
                "Price is near a usable bearish order block."
            )
            break

    # Sweep evidence is deliberately weighted modestly.
    if has_sell_sweep:
        bullish_points += 10
        bullish_reasons.append(
            "Liquidity engine reported a potential sell-side sweep."
        )

    if has_buy_sweep:
        bearish_points += 10
        bearish_reasons.append(
            "Liquidity engine reported a potential buy-side sweep."
        )

    bullish_points = min(100.0, bullish_points)
    bearish_points = min(100.0, bearish_points)

    # -----------------------------------------------------
    # 5. DETERMINE THE ASSESSMENT
    # -----------------------------------------------------

    difference = bullish_points - bearish_points

    if (
        bullish_points >= MIN_CONFLUENCE_SCORE
        and difference >= 20
    ):
        assessment = "BULLISH CONFLUENCE"

    elif (
        bearish_points >= MIN_CONFLUENCE_SCORE
        and difference <= -20
    ):
        assessment = "BEARISH CONFLUENCE"

    elif abs(difference) < 15:
        assessment = "MIXED / NO CLEAR CONFLUENCE"

    elif difference > 0:
        assessment = "BULLISH LEAN — INSUFFICIENT CONFLUENCE"

    else:
        assessment = "BEARISH LEAN — INSUFFICIENT CONFLUENCE"

    # Detect directional conflict explicitly.
    if bullish_points > 0 and bearish_points > 0:
        conflict = True
        neutral_reasons.append(
            "Bullish and bearish evidence are both present."
        )
    else:
        conflict = False

    # -----------------------------------------------------
    # 6. SORT NEAREST ZONES FIRST
    # -----------------------------------------------------

    nearby_fvgs.sort(key=lambda zone: zone["distance"])
    nearby_obs.sort(key=lambda zone: zone["distance"])

    def public_zone(zone: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": zone["type"],
            "lower": zone["lower"],
            "upper": zone["upper"],
            "distance": round(zone["distance"], 5),
            "status": zone["status"],
            "formed_time": zone["formed_time"],
        }

    # -----------------------------------------------------
    # 7. RETURN STRUCTURED RESULT
    # -----------------------------------------------------

    return {
        "symbol": "XAUUSD",
        "price": price,
        "price_source": price_source,
        "latest_candle_time": latest_candle.get("time"),
        "structure_bias": structure_bias,
        "latest_structure_event": {
            "type": event_type or "UNKNOWN",
            "direction": event_direction,
            "level": latest_event.get("level"),
            "time": latest_event.get("time"),
        },
        "assessment": assessment,
        "bullish_score": round(bullish_points, 1),
        "bearish_score": round(bearish_points, 1),
        "evidence_conflict": conflict,
        "bullish_reasons": list(dict.fromkeys(bullish_reasons)),
        "bearish_reasons": list(dict.fromkeys(bearish_reasons)),
        "neutral_reasons": list(dict.fromkeys(neutral_reasons)),
        "liquidity": {
            "buy_sweep_candidates": len(buy_sweeps),
            "sell_sweep_candidates": len(sell_sweeps),
        },
        "zones": {
            "nearby_fvgs": [
                public_zone(zone)
                for zone in nearby_fvgs[:MAX_ZONES_TO_REPORT]
            ],
            "nearby_order_blocks": [
                public_zone(zone)
                for zone in nearby_obs[:MAX_ZONES_TO_REPORT]
            ],
        },
        "method": (
            "Rule-based confluence score; not a probability, "
            "guaranteed signal, or financial advice."
        ),
    }


# ---------------------------------------------------------
# DISPLAY FUNCTION
# ---------------------------------------------------------

def display_confluence(result: Dict[str, Any]) -> None:
    """Print a readable confluence report."""

    print("\n" + "=" * 66)
    print("MARKET MIND V2 — CONFLUENCE REPORT")
    print("=" * 66)

    print("Symbol:", result["symbol"])
    print("Analysis price:", f'{result["price"]:.2f}')
    print("Price source:", result["price_source"])
    print("Structure bias:", result["structure_bias"])
    print("Assessment:", result["assessment"])

    print("\nCONFLUENCE SCORES")
    print("-" * 40)
    print("Bullish evidence:", result["bullish_score"], "/ 100")
    print("Bearish evidence:", result["bearish_score"], "/ 100")
    print("Evidence conflict:", result["evidence_conflict"])

    print("\nBULLISH EVIDENCE")
    if result["bullish_reasons"]:
        for reason in result["bullish_reasons"]:
            print("  +", reason)
    else:
        print("  None identified.")

    print("\nBEARISH EVIDENCE")
    if result["bearish_reasons"]:
        for reason in result["bearish_reasons"]:
            print("  -", reason)
    else:
        print("  None identified.")

    print("\nNEUTRAL NOTES")
    if result["neutral_reasons"]:
        for reason in result["neutral_reasons"]:
            print("  *", reason)
    else:
        print("  None.")

    liquidity = result["liquidity"]

    print("\nLIQUIDITY")
    print(
        "Potential buy-side sweeps:",
        liquidity["buy_sweep_candidates"]
    )
    print(
        "Potential sell-side sweeps:",
        liquidity["sell_sweep_candidates"]
    )

    zones = result["zones"]

    print("\nNEAREST USABLE FVG ZONES")

    if zones["nearby_fvgs"]:
        for zone in zones["nearby_fvgs"]:
            print(
                f'  {zone["type"]}: '
                f'{zone["lower"]:.2f}–{zone["upper"]:.2f} '
                f'| {zone["status"]} '
                f'| distance {zone["distance"]:.2f}'
            )
    else:
        print("  No usable FVG zone within the proximity threshold.")

    print("\nNEAREST USABLE ORDER BLOCKS")

    if zones["nearby_order_blocks"]:
        for zone in zones["nearby_order_blocks"]:
            print(
                f'  {zone["type"]}: '
                f'{zone["lower"]:.2f}–{zone["upper"]:.2f} '
                f'| {zone["status"]} '
                f'| distance {zone["distance"]:.2f}'
            )
    else:
        print(
            "  No usable order block within the proximity threshold."
        )

    print("\nNOTE")
    print(result["method"])
    print("=" * 66)
    