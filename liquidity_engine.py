from datetime import datetime, timezone
import math

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Phase 4B: Liquidity Engine
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
TIMEFRAME_NAME = "M5"

BARS_TO_FETCH = 100
SWING_STRENGTH = 3
MAX_LEVELS_TO_DISPLAY = 5


def fetch_completed_candles():
    """Fetch and validate completed candles."""

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        TIMEFRAME,
        1,
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

        if not all(math.isfinite(p) and p > 0 for p in prices):
            print("Invalid candle price detected.")
            return None

        if (
            candle["high"] < max(candle["open"], candle["close"])
            or candle["low"] > min(candle["open"], candle["close"])
            or candle["high"] < candle["low"]
            or candle["volume"] < 0
        ):
            print("Invalid OHLC data detected.")
            return None

        candles.append(candle)

    candles.sort(key=lambda candle: candle["time"])

    timestamps = [candle["time"] for candle in candles]

    if len(set(timestamps)) != len(timestamps):
        print("Duplicate candle timestamps detected.")
        return None

    return candles


def find_swings(candles, strength=SWING_STRENGTH):
    """Identify swing highs and lows with candles on both sides."""

    swing_highs = []
    swing_lows = []

    for i in range(strength, len(candles) - strength):
        current = candles[i]
        surrounding = (
            candles[i - strength:i]
            + candles[i + 1:i + strength + 1]
        )

        if all(
            current["high"] > candle["high"]
            for candle in surrounding
        ):
            swing_highs.append({
                "index": i,
                "time": current["time"],
                "price": current["high"],
            })

        if all(
            current["low"] < candle["low"]
            for candle in surrounding
        ):
            swing_lows.append({
                "index": i,
                "time": current["time"],
                "price": current["low"],
            })

    return swing_highs, swing_lows


def detect_liquidity(candles):
    """Identify potential BSL, SSL and candle-based sweep candidates."""

    swing_highs, swing_lows = find_swings(candles)

    if len(candles) < 2:
        return None

    last = candles[-1]

    # Keep only recent swing levels for reporting.
    recent_highs = swing_highs[-MAX_LEVELS_TO_DISPLAY:]
    recent_lows = swing_lows[-MAX_LEVELS_TO_DISPLAY:]

    # Compare the latest completed candle with prior swing levels.
    prior_highs = [
        swing for swing in swing_highs
        if swing["index"] < len(candles) - 1
    ]
    prior_lows = [
        swing for swing in swing_lows
        if swing["index"] < len(candles) - 1
    ]

    buy_sweeps = []
    sell_sweeps = []

    for swing in prior_highs:
        # Price trades above a prior swing high, then closes below it.
        if last["high"] > swing["price"] and last["close"] < swing["price"]:
            buy_sweeps.append({
                "level": swing["price"],
                "time": swing["time"],
            })

    for swing in prior_lows:
        # Price trades below a prior swing low, then closes above it.
        if last["low"] < swing["price"] and last["close"] > swing["price"]:
            sell_sweeps.append({
                "level": swing["price"],
                "time": swing["time"],
            })

    return {
        "last_candle": last,
        "swing_highs": recent_highs,
        "swing_lows": recent_lows,
        "buy_sweeps": buy_sweeps,
        "sell_sweeps": sell_sweeps,
    }


def display_liquidity(result):
    """Print the liquidity analysis report."""

    if result is None:
        print("Liquidity analysis unavailable.")
        return

    last = result["last_candle"]

    candle_time = datetime.fromtimestamp(
        last["time"],
        tz=timezone.utc,
    )

    info = mt5.symbol_info(SYMBOL)
    digits = info.digits if info is not None else 2

    print("\n" + "=" * 52)
    print("MARKET MIND V2 — LIQUIDITY ANALYSIS")
    print("=" * 52)
    print("Symbol:", SYMBOL)
    print("Timeframe:", TIMEFRAME_NAME)
    print("Last completed candle UTC:", candle_time.isoformat())
    print(f"Last close: {last['close']:.{digits}f}")

    print("\nBUY-SIDE LIQUIDITY (potential levels above price)")
    for swing in reversed(result["swing_highs"]):
        print(f"  BSL candidate: {swing['price']:.{digits}f}")

    if not result["swing_highs"]:
        print("  No swing-high levels detected.")

    print("\nSELL-SIDE LIQUIDITY (potential levels below price)")
    for swing in reversed(result["swing_lows"]):
        print(f"  SSL candidate: {swing['price']:.{digits}f}")

    if not result["swing_lows"]:
        print("  No swing-low levels detected.")

    print("\nPOTENTIAL SWEEP CANDIDATES")

    if result["buy_sweeps"]:
        for sweep in result["buy_sweeps"]:
            print(
                f"  Buy-side sweep candidate: "
                f"{sweep['level']:.{digits}f}"
            )
    else:
        print("  No buy-side sweep detected on the latest candle.")

    if result["sell_sweeps"]:
        for sweep in result["sell_sweeps"]:
            print(
                f"  Sell-side sweep candidate: "
                f"{sweep['level']:.{digits}f}"
            )
    else:
        print("  No sell-side sweep detected on the latest candle.")

    print("\nNote: These are rule-based candidates, not trade signals.")
    print("=" * 52)


def main():
    print("MARKET MIND V2 — LIQUIDITY ENGINE")

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

        print("Completed candles retrieved:", len(candles))

        result = detect_liquidity(candles)
        display_liquidity(result)

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()