from datetime import datetime, timezone
import math

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Phase 4A: Market Structure Analysis
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"

TIMEFRAME = mt5.TIMEFRAME_M5
TIMEFRAME_NAME = "M5"

BARS_TO_FETCH = 100
SWING_STRENGTH = 3
MAX_EVENTS_TO_DISPLAY = 10


# ============================================
# DATA RETRIEVAL AND VALIDATION
# ============================================

def fetch_completed_candles():
    """Fetch and validate completed candles in chronological order."""

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

        if not all(math.isfinite(price) and price > 0 for price in prices):
            print("Invalid candle price detected.")
            return None

        if (
            candle["high"] < max(candle["open"], candle["close"])
            or candle["low"] > min(candle["open"], candle["close"])
            or candle["high"] < candle["low"]
            or candle["volume"] < 0
        ):
            print("Candle failed OHLC validation.")
            return None

        candles.append(candle)

    candles.sort(key=lambda candle: candle["time"])

    timestamps = [candle["time"] for candle in candles]

    if len(set(timestamps)) != len(timestamps):
        print("Duplicate candle timestamps detected.")
        return None

    if any(
        timestamps[i] >= timestamps[i + 1]
        for i in range(len(timestamps) - 1)
    ):
        print("Candle timestamps are not strictly chronological.")
        return None

    return candles


# ============================================
# SWING DETECTION
# ============================================

def find_swings(candles, strength=SWING_STRENGTH):
    """
    Detect strict swing highs and lows.

    A swing is confirmed only after the required candles
    on its right side have completed.

    confirmed_index records when the swing becomes known.
    """

    if not isinstance(strength, int) or isinstance(strength, bool):
        raise ValueError("Swing strength must be an integer.")

    if strength < 1:
        raise ValueError("Swing strength must be at least 1.")

    swing_highs = []
    swing_lows = []

    for i in range(strength, len(candles) - strength):
        current = candles[i]

        left = candles[i - strength:i]
        right = candles[i + 1:i + strength + 1]

        if all(
            current["high"] > candle["high"]
            for candle in left + right
        ):
            swing_highs.append({
                "index": i,
                "confirmed_index": i + strength,
                "time": current["time"],
                "price": current["high"],
            })

        if all(
            current["low"] < candle["low"]
            for candle in left + right
        ):
            swing_lows.append({
                "index": i,
                "confirmed_index": i + strength,
                "time": current["time"],
                "price": current["low"],
            })

    return swing_highs, swing_lows


# ============================================
# MARKET STRUCTURE ANALYSIS
# ============================================

def analyze_market_structure(candles):
    """
    Detect close-based breaks of confirmed swing levels.

    A break is recorded only on the candle that crosses
    a level. Repeated closes beyond the same level do not
    generate repeated events.

    The first detected break establishes an initial bias.
    A break in the opposite direction is classified as
    CHoCH; a break in the established direction is BOS.

    This is a rule-based structural model, not a complete
    discretionary ICT/SMC interpretation.
    """

    minimum_candles = SWING_STRENGTH * 2 + 10

    if candles is None or len(candles) < minimum_candles:
        print("Not enough candles for market structure analysis.")
        return None

    swing_highs, swing_lows = find_swings(candles)

    if not swing_highs or not swing_lows:
        print("Insufficient confirmed swings for structure analysis.")
        return None

    last_candle = candles[-1]
    last_index = len(candles) - 1

    # Only swings confirmed before the candle being evaluated
    # may be used to identify a break.
    active_highs = []
    active_lows = []

    broken_high_indices = set()
    broken_low_indices = set()

    high_cursor = 0
    low_cursor = 0

    events = []
    bias = "NEUTRAL"

    for i in range(1, len(candles)):
        previous_close = candles[i - 1]["close"]
        current_close = candles[i]["close"]

        # Add only swings that were confirmed before this candle.
        while (
            high_cursor < len(swing_highs)
            and swing_highs[high_cursor]["confirmed_index"] < i
        ):
            active_highs.append(swing_highs[high_cursor])
            high_cursor += 1

        while (
            low_cursor < len(swing_lows)
            and swing_lows[low_cursor]["confirmed_index"] < i
        ):
            active_lows.append(swing_lows[low_cursor])
            low_cursor += 1

        crossed_highs = []
        crossed_lows = []

        for swing in active_highs:
            swing_index = swing["index"]

            if swing_index in broken_high_indices:
                continue

            level = swing["price"]

            # If price was already above the level before this
            # evaluation, it cannot be counted as a fresh break.
            if previous_close > level:
                broken_high_indices.add(swing_index)
                continue

            if previous_close <= level < current_close:
                crossed_highs.append(swing)
                broken_high_indices.add(swing_index)

        for swing in active_lows:
            swing_index = swing["index"]

            if swing_index in broken_low_indices:
                continue

            level = swing["price"]

            if previous_close < level:
                broken_low_indices.add(swing_index)
                continue

            if previous_close >= level > current_close:
                crossed_lows.append(swing)
                broken_low_indices.add(swing_index)

        # A single event represents the most relevant crossed
        # level in the direction of the close.
        event_swing = None
        direction = None

        if crossed_highs and not crossed_lows:
            event_swing = max(
                crossed_highs,
                key=lambda swing: swing["price"],
            )
            direction = "BULLISH"

        elif crossed_lows and not crossed_highs:
            event_swing = min(
                crossed_lows,
                key=lambda swing: swing["price"],
            )
            direction = "BEARISH"

        if direction is None:
            continue

        if bias == "NEUTRAL":
            event_type = "BOS"
            explanation = "Initial structural break; bias established"
        elif direction == bias:
            event_type = "BOS"
            explanation = "Break in the established structural direction"
        else:
            event_type = "CHoCH"
            explanation = "Break against the previously established bias"

        event = {
            "index": i,
            "time": candles[i]["time"],
            "close": current_close,
            "direction": direction,
            "type": event_type,
            "level": event_swing["price"],
            "swing_index": event_swing["index"],
            "swing_time": event_swing["time"],
            "explanation": explanation,
        }

        events.append(event)
        bias = direction

    latest_high = swing_highs[-1] if swing_highs else None
    latest_low = swing_lows[-1] if swing_lows else None

    latest_event = events[-1] if events else None

    if latest_event is None:
        structure = "No fresh swing-level break detected"
    else:
        structure = (
            f"{latest_event['direction']} {latest_event['type']}"
        )

    return {
        "time": last_candle["time"],
        "close": last_candle["close"],
        "bias": bias,
        "structure": structure,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "latest_swing_high": latest_high,
        "latest_swing_low": latest_low,
        "events": events,
        "latest_event": latest_event,
        "candles_analyzed": len(candles),
    }


# ============================================
# REPORTING
# ============================================

def format_utc(timestamp):
    """Convert a Unix timestamp into a readable UTC timestamp."""

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def display_analysis(result):
    """Display the market structure report."""

    if result is None:
        print("Analysis unavailable.")
        return

    print("\n" + "=" * 58)
    print("MARKET MIND V2 — MARKET STRUCTURE ENGINE")
    print("=" * 58)

    print("Symbol:", SYMBOL)
    print("Timeframe:", TIMEFRAME_NAME)
    print("Completed candles analyzed:", result["candles_analyzed"])
    print("Last completed candle UTC:", format_utc(result["time"]))
    print(f"Last completed close: {result['close']:.3f}")
    print("Latest structure event:", result["structure"])
    print("Structural bias:", result["bias"])

    high = result["latest_swing_high"]
    low = result["latest_swing_low"]

    if high is not None:
        print(
            f"Latest confirmed swing high: {high['price']:.3f}"
            f" | {format_utc(high['time'])}"
        )
    else:
        print("Latest confirmed swing high: Not found")

    if low is not None:
        print(
            f"Latest confirmed swing low:  {low['price']:.3f}"
            f" | {format_utc(low['time'])}"
        )
    else:
        print("Latest confirmed swing low: Not found")

    print("Swing highs detected:", len(result["swing_highs"]))
    print("Swing lows detected: ", len(result["swing_lows"]))
    print("Structure events detected:", len(result["events"]))

    print("\nRECENT STRUCTURE EVENTS")
    print("-" * 58)

    recent_events = result["events"][-MAX_EVENTS_TO_DISPLAY:]

    if not recent_events:
        print("No fresh close-based swing breaks detected.")
    else:
        for event in recent_events:
            event_time = format_utc(event["time"])

            print(
                f"{event_time} | "
                f"{event['direction']} {event['type']} | "
                f"Level: {event['level']:.3f} | "
                f"Close: {event['close']:.3f}"
            )

    if result["latest_event"] is not None:
        print("\nLatest event explanation:")
        print(result["latest_event"]["explanation"])

    print("\nImportant limitations:")
    print("- Swing points require right-side candle confirmation.")
    print("- BOS/CHoCH labels follow this engine's explicit rules.")
    print("- This module does not confirm trade entries.")
    print("- Analysis is not a trade recommendation or guarantee.")
    print("=" * 58)


# ============================================
# MAIN
# ============================================

def main():
    print("MARKET MIND V2 — ANALYSIS ENGINE")

    if not mt5.initialize(path=MT5_PATH, timeout=60000):
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

        print(f"Completed candles retrieved: {len(candles)}")

        result = analyze_market_structure(candles)
        display_analysis(result)

    except Exception as exc:
        print("Market structure analysis failed:", repr(exc))
        raise

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()