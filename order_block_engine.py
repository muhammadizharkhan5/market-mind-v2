from datetime import datetime, timezone
from statistics import median
import math

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Order Block Engine — Consolidated Version
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
TIMEFRAME_NAME = "M5"

BARS_TO_FETCH = 150
SWING_STRENGTH = 3
BODY_LOOKBACK = 20
DISPLACEMENT_MULTIPLIER = 1.5
MAX_BLOCKS_TO_DISPLAY = 10

# Search at most this many candles backward for the
# opposing candle that defines a candidate block.
OB_SEARCH_LOOKBACK = 10

# A bullish block is considered invalidated if a later
# candle CLOSES below its lower boundary before the break.
# A bearish block is invalidated if a later candle CLOSES
# above its upper boundary before the break.
#
# These are explicit heuristic rules, not universal
# definitions of order blocks.


def fetch_completed_candles():
    """Fetch and validate completed MT5 candles."""

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


def find_swings(candles, strength=SWING_STRENGTH):
    """
    Find strict swing highs and lows.

    A swing is only usable for a break after all of its
    right-side confirmation candles have completed.
    """

    highs = []
    lows = []

    if strength < 1 or len(candles) < (2 * strength + 1):
        return highs, lows

    for i in range(strength, len(candles) - strength):
        current = candles[i]

        left = candles[i - strength:i]
        right = candles[i + 1:i + strength + 1]

        surrounding = left + right

        if all(
            current["high"] > candle["high"]
            for candle in surrounding
        ):
            highs.append({
                "index": i,
                "price": current["high"],
                "time": current["time"],
            })

        if all(
            current["low"] < candle["low"]
            for candle in surrounding
        ):
            lows.append({
                "index": i,
                "price": current["low"],
                "time": current["time"],
            })

    return highs, lows


def candle_body(candle):
    """Return the absolute real-body size."""

    return abs(candle["close"] - candle["open"])


def is_bullish(candle):
    return candle["close"] > candle["open"]


def is_bearish(candle):
    return candle["close"] < candle["open"]


def block_invalidated_before_break(
    candles,
    block_index,
    break_index,
    direction,
):
    """
    Reject a block if a completed candle CLOSES beyond
    its invalidation boundary before the displacement.

    The displacement candle itself is excluded.
    """

    block = candles[block_index]

    for j in range(block_index + 1, break_index):
        candle = candles[j]

        if direction == "BULLISH":
            if candle["close"] < block["low"]:
                return True
        else:
            if candle["close"] > block["high"]:
                return True

    return False


def find_block_candle(candles, break_index, direction):
    """
    Find the nearest eligible opposing candle before a break.

    An already invalidated candidate is skipped. If no eligible
    opposing candle exists within the configured lookback,
    return None.
    """

    start = max(0, break_index - OB_SEARCH_LOOKBACK)

    for j in range(break_index - 1, start - 1, -1):
        candidate = candles[j]

        if direction == "BULLISH":
            if not is_bearish(candidate):
                continue
        else:
            if not is_bullish(candidate):
                continue

        if block_invalidated_before_break(
            candles,
            j,
            break_index,
            direction,
        ):
            continue

        return j

    return None


def detect_order_blocks(candles):
    """
    Detect heuristic bullish and bearish order-block candidates.

    Requirements:
    1. A sufficiently large displacement body.
    2. A close beyond a previously confirmed swing level.
    3. A close-to-close crossing of that swing level.
    4. An eligible opposing candle before the break.
    5. No close-based invalidation of that block before the break.

    Swing selection uses the most recently formed eligible swing
    among the levels crossed by the displacement candle.

    Results are deduplicated by block identity. If the same block
    is associated with multiple breaks, its earliest qualifying
    break is retained.
    """

    if (
        BODY_LOOKBACK < 1
        or SWING_STRENGTH < 1
        or OB_SEARCH_LOOKBACK < 1
        or DISPLACEMENT_MULTIPLIER <= 0
    ):
        print("Invalid order-block configuration.")
        return []

    minimum_required = (
        BODY_LOOKBACK + (SWING_STRENGTH * 2) + 2
    )

    if len(candles) < minimum_required:
        return []

    swing_highs, swing_lows = find_swings(candles)

    blocks = []
    seen_blocks = set()

    for i in range(BODY_LOOKBACK, len(candles)):
        displacement = candles[i]

        previous_close = candles[i - 1]["close"]
        current_close = displacement["close"]

        body = candle_body(displacement)

        # Exclude the displacement candle from its own baseline.
        previous_bodies = [
            candle_body(candle)
            for candle in candles[i - BODY_LOOKBACK:i]
            if candle_body(candle) > 0
        ]

        if not previous_bodies:
            continue

        baseline = median(previous_bodies)

        if baseline <= 0:
            continue

        displacement_ratio = body / baseline

        if displacement_ratio < DISPLACEMENT_MULTIPLIER:
            continue

        # Only swings confirmed before the break candle began.
        confirmed_highs = [
            swing for swing in swing_highs
            if swing["index"] + SWING_STRENGTH < i
        ]

        confirmed_lows = [
            swing for swing in swing_lows
            if swing["index"] + SWING_STRENGTH < i
        ]

        direction = None
        broken_level = None
        selected_swing = None

        # ----------------------------------------
        # BULLISH BREAK
        # ----------------------------------------

        if is_bullish(displacement):
            broken = [
                swing for swing in confirmed_highs
                if previous_close <= swing["price"] < current_close
            ]

            if broken:
                selected_swing = max(
                    broken,
                    key=lambda swing: swing["index"],
                )
                direction = "BULLISH"
                broken_level = selected_swing["price"]

        # ----------------------------------------
        # BEARISH BREAK
        # ----------------------------------------

        elif is_bearish(displacement):
            broken = [
                swing for swing in confirmed_lows
                if previous_close >= swing["price"] > current_close
            ]

            if broken:
                selected_swing = max(
                    broken,
                    key=lambda swing: swing["index"],
                )
                direction = "BEARISH"
                broken_level = selected_swing["price"]

        if direction is None:
            continue

        block_index = find_block_candle(
            candles,
            i,
            direction,
        )

        if block_index is None:
            continue

        block_candle = candles[block_index]

        block = {
            "type": direction,
            "lower": block_candle["low"],
            "upper": block_candle["high"],
            "formed_time": block_candle["time"],
            "formed_index": block_index,
            "displacement_time": displacement["time"],
            "displacement_index": i,
            "broken_level": broken_level,
            "swing_index": selected_swing["index"],
            "swing_time": selected_swing["time"],
            "body_size": body,
            "baseline_body": baseline,
            "displacement_ratio": displacement_ratio,
        }

        # Deduplicate repeated breaks tied to the same block.
        key = (
            block["type"],
            block["formed_time"],
            round(block["lower"], 8),
            round(block["upper"], 8),
        )

        if key in seen_blocks:
            continue

        seen_blocks.add(key)
        blocks.append(block)

    # Preserve chronological order in the engine output.
    blocks.sort(key=lambda block: block["displacement_index"])

    return blocks


def assess_block_status(block, candles):
    """
    Assess post-break interaction in chronological order.

    UNTESTED:
        No subsequent completed candle overlaps the zone.

    TOUCHED:
        Price reaches the zone boundary without penetrating it.

    PARTIALLY MITIGATED:
        Price penetrates the zone but does not reach its far edge.

    FULLY MITIGATED:
        Price reaches or passes the far boundary without a single
        candle necessarily spanning both boundaries.

    FULLY CROSSED:
        A later candle's range spans the entire zone.

    INVALIDATED:
        A later candle CLOSES beyond the far boundary.

    INVALIDATED takes precedence because it describes a later
    close-based breach. These are heuristic OHLC classifications.
    """

    lower = block["lower"]
    upper = block["upper"]

    if lower >= upper:
        return "INVALID DATA"

    block_time = block["displacement_time"]

    later_candles = [
        candle for candle in candles
        if candle["time"] > block_time
    ]

    if not later_candles:
        return "UNTESTED"

    touched = False
    partially_mitigated = False
    fully_mitigated = False
    fully_crossed = False
    invalidated = False

    for candle in later_candles:
        candle_low = candle["low"]
        candle_high = candle["high"]
        candle_close = candle["close"]

        overlaps = (
            candle_low <= upper
            and candle_high >= lower
        )

        if overlaps:
            touched = True

            # A single candle spans both zone boundaries.
            if candle_low <= lower and candle_high >= upper:
                fully_crossed = True

            elif block["type"] == "BULLISH":
                if candle_low <= lower:
                    fully_mitigated = True
                elif candle_low < upper:
                    partially_mitigated = True

            else:
                if candle_high >= upper:
                    fully_mitigated = True
                elif candle_high > lower:
                    partially_mitigated = True

        # Invalidation is based on a close beyond the far edge.
        if block["type"] == "BULLISH":
            if candle_close < lower:
                invalidated = True
        else:
            if candle_close > upper:
                invalidated = True

    if invalidated:
        return "INVALIDATED"

    if fully_crossed:
        return "FULLY CROSSED"

    if fully_mitigated:
        return "FULLY MITIGATED"

    if partially_mitigated:
        return "PARTIALLY MITIGATED"

    if touched:
        return "TOUCHED"

    return "UNTESTED"


def display_order_blocks(blocks, candles):
    """Print recent order-block candidates and their status."""

    info = mt5.symbol_info(SYMBOL)
    digits = info.digits if info is not None else 2

    last = candles[-1]

    last_time = datetime.fromtimestamp(
        last["time"],
        tz=timezone.utc,
    )

    print("\n" + "=" * 58)
    print("MARKET MIND V2 — ORDER BLOCK ANALYSIS")
    print("=" * 58)
    print("Symbol:", SYMBOL)
    print("Timeframe:", TIMEFRAME_NAME)
    print("Completed candles:", len(candles))
    print("Last completed candle UTC:", last_time.isoformat())
    print(f"Last completed close: {last['close']:.{digits}f}")
    print("Candidate order blocks detected:", len(blocks))

    recent = sorted(
        blocks,
        key=lambda block: block["displacement_index"],
        reverse=True,
    )[:MAX_BLOCKS_TO_DISPLAY]

    if not recent:
        print("\nNo candidates found with the current rules.")
        print("=" * 58)
        return

    print(f"\nMOST RECENT {len(recent)} CANDIDATES")

    for number, block in enumerate(recent, start=1):
        formed = datetime.fromtimestamp(
            block["formed_time"],
            tz=timezone.utc,
        )

        displaced = datetime.fromtimestamp(
            block["displacement_time"],
            tz=timezone.utc,
        )

        status = assess_block_status(block, candles)

        print(f"\n{number}. {block['type']} ORDER BLOCK")
        print(
            f"   Lower boundary:     "
            f"{block['lower']:.{digits}f}"
        )
        print(
            f"   Upper boundary:     "
            f"{block['upper']:.{digits}f}"
        )
        print(
            f"   Broken swing level: "
            f"{block['broken_level']:.{digits}f}"
        )
        print(f"   Block candle UTC:   {formed.isoformat()}")
        print(f"   Break candle UTC:   {displaced.isoformat()}")
        print(
            f"   Displacement body:  "
            f"{block['body_size']:.{digits}f}"
        )
        print(
            f"   Body baseline:      "
            f"{block['baseline_body']:.{digits}f}"
        )
        print(
            f"   Body/baseline:      "
            f"{block['displacement_ratio']:.2f}x"
        )
        print(f"   Status:             {status}")

    print("\nNote:")
    print("- These are heuristic order-block candidates.")
    print("- Swing levels require completed right-side confirmation.")
    print("- Pre-break invalidation uses candle closes.")
    print("- Mitigation and invalidation use completed candle OHLC.")
    print("- This engine does not prove institutional activity.")
    print("- No future price direction or trade outcome is guaranteed.")
    print("=" * 58)


def main():
    print("MARKET MIND V2 — ORDER BLOCK ENGINE")

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

        blocks = detect_order_blocks(candles)
        display_order_blocks(blocks, candles)

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()