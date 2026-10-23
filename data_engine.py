from datetime import datetime, timezone

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Phase 1: Historical Market Data Validation
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"
BARS_TO_FETCH = 100

TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


def fetch_candles(timeframe_name, timeframe):
    """Fetch completed candles and validate their OHLC data."""

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        timeframe,
        1,  # Start at the most recently completed candle
        BARS_TO_FETCH,
    )

    if rates is None:
        print(f"ERROR: {timeframe_name}: {mt5.last_error()}")
        return False

    if len(rates) == 0:
        print(f"ERROR: No candles returned for {timeframe_name}.")
        return False

    previous_timestamp = None
    seen_timestamps = set()
    valid_count = 0
    errors = []

    for rate in rates:
        timestamp = int(rate["time"])

        open_price = float(rate["open"])
        high_price = float(rate["high"])
        low_price = float(rate["low"])
        close_price = float(rate["close"])

        volume = int(rate["tick_volume"])

        if timestamp in seen_timestamps:
            errors.append("Duplicate candle timestamp")
            continue

        seen_timestamps.add(timestamp)

        if previous_timestamp is not None:
            if timestamp <= previous_timestamp:
                errors.append("Candles are not in chronological order")

        previous_timestamp = timestamp

        prices = [open_price, high_price, low_price, close_price]

        if not all(
            price > 0 and price < float("inf")
            for price in prices
        ):
            errors.append("Invalid OHLC price")
            continue

        if high_price < max(open_price, close_price, low_price):
            errors.append("High price violates OHLC rules")
            continue

        if low_price > min(open_price, close_price, high_price):
            errors.append("Low price violates OHLC rules")
            continue

        if volume < 0:
            errors.append("Invalid tick volume")
            continue

        valid_count += 1

    if errors:
        print(f"FAILED: {timeframe_name}")
        for error in sorted(set(errors)):
            print("  -", error)
        return False

    oldest = datetime.fromtimestamp(
        int(rates[0]["time"]), tz=timezone.utc
    )
    latest = datetime.fromtimestamp(
        int(rates[-1]["time"]), tz=timezone.utc
    )

    last = rates[-1]

    print(f"\nTIMEFRAME: {timeframe_name}")
    print("-" * 42)
    print(f"Candles received: {len(rates)}")
    print(f"Valid candles:    {valid_count}")
    print(f"Oldest candle:    {oldest.isoformat()}")
    print(f"Latest candle:    {latest.isoformat()}")
    print(f"Open:             {float(last['open']):.3f}")
    print(f"High:             {float(last['high']):.3f}")
    print(f"Low:              {float(last['low']):.3f}")
    print(f"Close:            {float(last['close']):.3f}")
    print("Validation:       PASSED")

    return True


def main():
    print("=" * 50)
    print("MARKET MIND V2 — HISTORICAL DATA ENGINE")
    print("=" * 50)

    if not mt5.initialize(path=MT5_PATH, timeout=60000):
        print("MT5 CONNECTION FAILED:", mt5.last_error())
        return

    try:
        account = mt5.account_info()

        if account is None:
            print("No MT5 account available.")
            return

        if not mt5.symbol_select(SYMBOL, True):
            print(f"Could not select {SYMBOL}.")
            return

        print("Connection: SUCCESS")
        print("Symbol:", SYMBOL)
        print("Server:", account.server)

        results = {}

        for name, timeframe in TIMEFRAMES.items():
            results[name] = fetch_candles(name, timeframe)

        print("\n" + "=" * 50)
        print("DATA ENGINE SUMMARY")

        for name, passed in results.items():
            status = "PASSED" if passed else "FAILED"
            print(f"{name}: {status}")

        if all(results.values()):
            print("\nALL FOUR TIMEFRAMES PASSED.")
        else:
            print("\nDATA VALIDATION NEEDS ATTENTION.")

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()