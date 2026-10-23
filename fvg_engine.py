from datetime import datetime, timezone
import math

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Phase 4C: Fair Value Gap Engine
# Improved: Displacement + Filtering + Mitigation
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
TIMEFRAME_NAME = "M5"

BARS_TO_FETCH = 100
MAX_FVGS_TO_DISPLAY = 10

# --------------------------------------------
# FVG FILTER SETTINGS
# --------------------------------------------

# Minimum FVG width in XAUUSD price units.
# 0.0 accepts every positive-width geometric gap.
MIN_GAP_SIZE = 0.0

# Middle-candle displacement requirements.
ENABLE_DISPLACEMENT_FILTER = True

# Minimum fraction of the middle candle's range
# occupied by its real body.
MIN_BODY_TO_RANGE_RATIO = 0.45

# Middle body must be at least this multiple of
# the average body of preceding completed candles.
MIN_BODY_TO_AVG_RATIO = 1.0

# Number of preceding candles used for comparison.
DISPLACEMENT_LOOKBACK = 20

# A zero-range middle candle cannot qualify as
# a displacement candle.
MIN_CANDLE_RANGE = 0.0


def fetch_completed_candles():
    """Fetch and validate completed candles from MT5."""

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        TIMEFRAME,
        1,  # Exclude the currently forming candle
        BARS_TO_FETCH,
    )

    if rates is None or len(rates) == 0:
        print("Could not retrieve candles:", mt5.last_error())
        return None

    candles = []

    for rate in rates:
        candle = {
            "time": int(rate["time"]),
            "open": float(rate["open"]),
            "high": float(rate["high"]),
            "low": float(rate["low"]),
            "close": float(rate["close"]),
            "volume": int(rate["tick_volume"]),
        }

        prices = [
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
        ]

        if not all(
            math.isfinite(price) and price > 0
            for price in prices
        ):
            print("Invalid candle price detected.")
            return None

        if (
            candle["high"] < candle["low"]
            or candle["high"] < max(
                candle["open"], candle["close"]
            )
            or candle["low"] > min(
                candle["open"], candle["close"]
            )
            or candle["volume"] < 0
        ):
            print("Invalid OHLC or volume data detected.")
            return None

        candles.append(candle)

    candles.sort(key=lambda candle: candle["time"])

    timestamps = [candle["time"] for candle in candles]

    if len(set(timestamps)) != len(timestamps):
        print("Duplicate candle timestamps detected.")
        return None

    return candles


def assess_displacement(candles, middle_index):
    """
    Measure the middle candle's displacement characteristics.

    Returns body-to-range ratio, body-to-average ratio,
    and whether the candle passes the configured filter.
    """

    middle = candles[middle_index]

    body = abs(middle["close"] - middle["open"])
    candle_range = middle["high"] - middle["low"]

    if candle_range <= MIN_CANDLE_RANGE:
        body_to_range = 0.0
    else:
        body_to_range = body / candle_range

    # Use preceding candles only. Do not use the middle
    # candle or any future candle in the baseline.
    start = max(
        0,
        middle_index - DISPLACEMENT_LOOKBACK,
    )

    previous_bodies = [
        abs(candles[j]["close"] - candles[j]["open"])
        for j in range(start, middle_index)
    ]

    if previous_bodies:
        average_body = sum(previous_bodies) / len(
            previous_bodies
        )
    else:
        # Early-history fallback when no prior candle exists.
        average_body = body

    if average_body > 0:
        body_to_average = body / average_body
    else:
        body_to_average = 0.0

    passes = (
        candle_range > MIN_CANDLE_RANGE
        and body_to_range >= MIN_BODY_TO_RANGE_RATIO
        and body_to_average >= MIN_BODY_TO_AVG_RATIO
    )

    return {
        "middle_candle_body": body,
        "middle_candle_range": candle_range,
        "body_to_range_ratio": body_to_range,
        "body_to_average_ratio": body_to_average,
        "average_previous_body": average_body,
        "displacement_passed": passes,
    }


def detect_fvgs(candles):
    """
    Detect three-candle Fair Value Gap candidates.

    Bullish FVG:
        Third candle low > first candle high.

    Bearish FVG:
        Third candle high < first candle low.

    The middle candle's body and displacement are measured.
    Optional filtering rejects candidates whose middle candle
    fails the configured displacement thresholds.
    """

    fvgs = []

    if len(candles) < 3:
        return fvgs

    for i in range(2, len(candles)):
        first = candles[i - 2]
        middle = candles[i - 1]
        third = candles[i]

        displacement = assess_displacement(
            candles,
            i - 1,
        )

        # ----------------------------------------
        # BULLISH FVG
        # ----------------------------------------

        bullish_gap = third["low"] - first["high"]

        if bullish_gap > MIN_GAP_SIZE:
            if (
                not ENABLE_DISPLACEMENT_FILTER
                or displacement["displacement_passed"]
            ):
                fvgs.append({
                    "type": "BULLISH",
                    "lower": first["high"],
                    "upper": third["low"],
                    "size": bullish_gap,
                    "formed_time": third["time"],
                    "formed_index": i,
                    **displacement,
                })

        # ----------------------------------------
        # BEARISH FVG
        # ----------------------------------------

        bearish_gap = first["low"] - third["high"]

        if bearish_gap > MIN_GAP_SIZE:
            if (
                not ENABLE_DISPLACEMENT_FILTER
                or displacement["displacement_passed"]
            ):
                fvgs.append({
                    "type": "BEARISH",
                    "lower": third["high"],
                    "upper": first["low"],
                    "size": bearish_gap,
                    "formed_time": third["time"],
                    "formed_index": i,
                    **displacement,
                })

    return fvgs


def assess_fvg_status(fvg, candles):
    """
    Assess the FVG using completed candles after formation.

    UNTESTED:
        No later candle overlaps the FVG.

    TOUCHED:
        Price reaches the near boundary without penetrating
        the zone's interior.

    PARTIALLY MITIGATED:
        Price enters the zone but does not reach its far edge.

    FULLY MITIGATED:
        Price reaches the far boundary of the zone.

    FULLY CROSSED:
        A single later candle's range spans both boundaries.

    These labels describe OHLC price ranges, not tick-by-tick
    order flow. They do not establish institutional activity.
    """

    lower = fvg["lower"]
    upper = fvg["upper"]

    later_candles = candles[fvg["formed_index"] + 1:]

    if not later_candles:
        return "UNTESTED"

    touched = False
    partially_mitigated = False
    fully_mitigated = False

    for candle in later_candles:
        candle_low = candle["low"]
        candle_high = candle["high"]

        # No overlap with the FVG zone.
        if candle_low > upper or candle_high < lower:
            continue

        touched = True

        # One candle spans the entire FVG.
        if candle_low <= lower and candle_high >= upper:
            return "FULLY CROSSED"

        if fvg["type"] == "BULLISH":
            # A bullish FVG is normally revisited from above.
            if candle_low <= lower:
                fully_mitigated = True
            elif candle_low < upper:
                partially_mitigated = True

        else:
            # A bearish FVG is normally revisited from below.
            if candle_high >= upper:
                fully_mitigated = True
            elif candle_high > lower:
                partially_mitigated = True

    if fully_mitigated:
        return "FULLY MITIGATED"

    if partially_mitigated:
        return "PARTIALLY MITIGATED"

    if touched:
        return "TOUCHED"

    return "UNTESTED"


def display_fvgs(fvgs, candles):
    """Print detected FVG candidates in console."""

    info = mt5.symbol_info(SYMBOL)
    digits = info.digits if info is not None else 2

    last_candle = candles[-1]

    last_time = datetime.fromtimestamp(
        last_candle["time"],
        tz=timezone.utc,
    )

    print("\n" + "=" * 56)
    print("MARKET MIND V2 — FAIR VALUE GAP ANALYSIS")
    print("=" * 56)
    print("Symbol:", SYMBOL)
    print("Timeframe:", TIMEFRAME_NAME)
    print("Completed candles:", len(candles))
    print("Last completed candle UTC:", last_time.isoformat())
    print(
        f"Last completed close: "
        f"{last_candle['close']:.{digits}f}"
    )
    print("FVG candidates detected:", len(fvgs))

    print("\nFILTER SETTINGS")
    print("Minimum gap size:", MIN_GAP_SIZE)
    print("Displacement filter:", ENABLE_DISPLACEMENT_FILTER)
    print(
        "Minimum body/range ratio:",
        f"{MIN_BODY_TO_RANGE_RATIO:.2f}",
    )
    print(
        "Minimum body/average ratio:",
        f"{MIN_BODY_TO_AVG_RATIO:.2f}",
    )

    if not fvgs:
        print("\nNo FVG candidates passed the current filters.")
        print("Consider reviewing the displacement thresholds.")
        print("=" * 56)
        return

    # Most recently formed candidates first.
    recent_fvgs = sorted(
        fvgs,
        key=lambda fvg: fvg["formed_index"],
        reverse=True,
    )[:MAX_FVGS_TO_DISPLAY]

    print(f"\nMOST RECENT {len(recent_fvgs)} FVG CANDIDATES")

    for number, fvg in enumerate(recent_fvgs, start=1):
        formed_time = datetime.fromtimestamp(
            fvg["formed_time"],
            tz=timezone.utc,
        )

        status = assess_fvg_status(fvg, candles)

        print(f"\n{number}. {fvg['type']} FVG")
        print(f"   Lower boundary: {fvg['lower']:.{digits}f}")
        print(f"   Upper boundary: {fvg['upper']:.{digits}f}")
        print(f"   Gap size:       {fvg['size']:.{digits}f}")
        print(f"   Formed UTC:     {formed_time.isoformat()}")
        print(f"   Status:         {status}")

        print(
            "   Middle body:    "
            f"{fvg['middle_candle_body']:.{digits}f}"
        )
        print(
            "   Body/range:     "
            f"{fvg['body_to_range_ratio']:.1%}"
        )
        print(
            "   Body/average:   "
            f"{fvg['body_to_average_ratio']:.2f}x"
        )
        print(
            "   Displacement:   "
            + (
                "PASS"
                if fvg["displacement_passed"]
                else "FAIL"
            )
        )

    print("\nNote:")
    print("- FVGs are geometric price-gap candidates.")
    print("- Displacement thresholds are configurable heuristics.")
    print("- Status uses subsequent completed candle ranges.")
    print("- OHLC data cannot establish the exact intrabar path.")
    print("- These are not trade signals or guaranteed reaction zones.")
    print("=" * 56)


def main():
    print("MARKET MIND V2 — FVG ENGINE")

    if not mt5.initialize(
        path=MT5_PATH,
        timeout=60000,
    ):
        print("MT5 connection failed:", mt5.last_error())
        return

    try:
        if mt5.account_info() is None:
            print("No MT5 account information available.")
            return

        if not mt5.symbol_select(SYMBOL, True):
            print("Could not select symbol:", SYMBOL)
            return

        candles = fetch_completed_candles()

        if candles is None:
            return

        print("Completed candles retrieved:", len(candles))

        fvgs = detect_fvgs(candles)
        display_fvgs(fvgs, candles)

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()