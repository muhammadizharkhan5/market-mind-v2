from datetime import datetime, timezone
import math
import time

import MetaTrader5 as mt5


# ============================================
# MARKET MIND V2
# Phase 2: Live Market Data Engine
# ============================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"

POLL_INTERVAL_SECONDS = 1
NO_NEW_TICK_WARNING_SECONDS = 30


# ============================================
# MT5 CONNECTION
# ============================================

def connect_mt5():
    """Initialize MT5 and select the configured trading symbol."""

    if not mt5.initialize(path=MT5_PATH, timeout=60000):
        print("MT5 CONNECTION FAILED:", mt5.last_error())
        return False

    account = mt5.account_info()

    if account is None:
        print("No MT5 account information available.")
        mt5.shutdown()
        return False

    if not mt5.symbol_select(SYMBOL, True):
        print("Could not select symbol:", SYMBOL)
        mt5.shutdown()
        return False

    print("Connection: SUCCESS")
    print("Symbol:", SYMBOL)
    print("Server:", account.server)
    print("Login:", account.login)

    return True


# ============================================
# LIVE QUOTE RETRIEVAL
# ============================================

def get_live_quote():
    """
    Return a validated live quote as a dictionary.

    Returns None when a valid quote is unavailable.

    This function can also be imported by other Market Mind
    modules without running the continuous reporting loop.
    """

    tick = mt5.symbol_info_tick(SYMBOL)

    if tick is None:
        return None

    try:
        bid = float(tick.bid)
        ask = float(tick.ask)
        tick_msc = int(tick.time_msc)
    except (TypeError, ValueError, OverflowError):
        return None

    if (
        not math.isfinite(bid)
        or not math.isfinite(ask)
        or bid <= 0
        or ask <= 0
        or ask < bid
        or tick_msc <= 0
    ):
        return None

    info = mt5.symbol_info(SYMBOL)

    if info is None:
        return None

    digits = int(info.digits)

    if digits < 0 or digits > 10:
        return None

    broker_tick_time = datetime.fromtimestamp(
        tick_msc / 1000,
        tz=timezone.utc,
    )

    return {
        "symbol": SYMBOL,
        "bid": bid,
        "ask": ask,
        "spread": ask - bid,
        "time_msc": tick_msc,
        "time": broker_tick_time,
        "digits": digits,
    }


# ============================================
# REPORTING
# ============================================

def display_live_report(
    quote,
    computer_utc,
    observed_age=None,
    error_message=None,
):
    """Print one live report, including when quote data is unavailable."""

    print("\n" + "=" * 58)
    print("MARKET MIND V2 — LIVE MARKET DATA")
    print("=" * 58)

    print("Symbol:         ", SYMBOL)
    print("Computer UTC:   ", computer_utc.isoformat())

    if quote is None:
        print("Bid:             UNAVAILABLE")
        print("Ask:             UNAVAILABLE")
        print("Spread:          UNAVAILABLE")
        print("Broker tick UTC: UNAVAILABLE")
        print("Quote status:    NO VALID QUOTE")

        if error_message:
            print("Details:        ", error_message)
        else:
            print("MT5 last error: ", mt5.last_error())

        print("=" * 58)
        return

    digits = quote["digits"]

    print(f"Bid:             {quote['bid']:.{digits}f}")
    print(f"Ask:             {quote['ask']:.{digits}f}")
    print(f"Spread:          {quote['spread']:.{digits}f}")
    print("Broker tick UTC: ", quote["time"].isoformat())

    # This age uses the computer clock and broker timestamp.
    # Clock differences can make this value inaccurate.
    broker_timestamp_age = (
        computer_utc - quote["time"]
    ).total_seconds()

    print(
        "Broker timestamp age:",
        f"{broker_timestamp_age:.1f} seconds (clock-dependent)",
    )

    if observed_age is not None:
        print(
            "Time since new tick observed:",
            f"{observed_age:.1f} seconds",
        )

        if observed_age >= NO_NEW_TICK_WARNING_SECONDS:
            print("Quote status:    WARNING — tick unchanged")
            print(
                "Interpretation: No new tick timestamp has been "
                "observed. A quiet market may also cause this."
            )
        else:
            print("Quote status:    RECENT TICK CHANGE OBSERVED")
    else:
        print("Quote status:    TICK AGE NOT YET ESTABLISHED")

    print(
        "Note: An unchanged tick timestamp does not by itself "
        "prove the connection has failed."
    )
    print("=" * 58)


# ============================================
# CONTINUOUS LIVE MONITOR
# ============================================

def main():
    print("=" * 58)
    print("MARKET MIND V2 — LIVE ENGINE")
    print("=" * 58)

    if not connect_mt5():
        return

    last_tick_msc = None
    last_change_monotonic = None

    try:
        while True:
            now_monotonic = time.monotonic()
            computer_utc = datetime.now(timezone.utc)

            quote = get_live_quote()

            if quote is None:
                display_live_report(
                    quote=None,
                    computer_utc=computer_utc,
                )

                # Do not treat a failed read as a new tick.
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            tick_msc = quote["time_msc"]

            if tick_msc != last_tick_msc:
                last_tick_msc = tick_msc
                last_change_monotonic = now_monotonic

            observed_age = (
                now_monotonic - last_change_monotonic
                if last_change_monotonic is not None
                else None
            )

            display_live_report(
                quote=quote,
                computer_utc=computer_utc,
                observed_age=observed_age,
            )

            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nLive engine stopped by user.")

    except Exception as exc:
        print("\nLIVE ENGINE ERROR:", repr(exc))
        raise

    finally:
        mt5.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    main()