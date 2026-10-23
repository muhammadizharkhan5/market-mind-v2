from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone

import MetaTrader5 as mt5

import analysis_enigne as structure_engine
import fvg_engine
import liquidity_engine
import order_block_engine
import confluence_engine
import mtf_confluence_engine

# ============================================================
# MARKET MIND V2 — MULTI-TIMEFRAME ANALYSIS
# ============================================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"

HISTORICAL_BARS = 100
ANALYSIS_BARS = 150

LIVE_REFRESH_SECONDS = 1
STALE_AFTER_SECONDS = 30
LIVE_STATUS_INTERVAL_SECONDS = 60

TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


# ============================================================
# CONNECTION
# ============================================================

def initialize_mt5():
    print("=" * 64)
    print("MARKET MIND V2 — INITIALIZING MT5")
    print("=" * 64)

    if not mt5.initialize(path=MT5_PATH, timeout=60000):
        raise RuntimeError(
            f"MT5 initialization failed: {mt5.last_error()}"
        )

    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError(
            f"Could not select {SYMBOL}: {mt5.last_error()}"
        )

    account = mt5.account_info()
    if account is None:
        raise RuntimeError(
            f"Could not read MT5 account: {mt5.last_error()}"
        )

    print("Connection: SUCCESS")
    print(f"Symbol:     {SYMBOL}")
    print(f"Login:      {account.login}")
    print(f"Server:     {account.server}")
    print()


# ============================================================
# CANDLE DATA
# ============================================================

def fetch_completed_candles(timeframe, count):
    """Fetch completed candles only; position 0 is excluded."""

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        timeframe,
        1,
        count,
    )

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No candles returned for timeframe {timeframe}. "
            f"MT5 error: {mt5.last_error()}"
        )

    candles = []

    for rate in rates:
        fields = rate.dtype.names or ()

        candles.append({
            "time": int(rate["time"]),
            "open": float(rate["open"]),
            "high": float(rate["high"]),
            "low": float(rate["low"]),
            "close": float(rate["close"]),
            "volume": int(
                rate["tick_volume"]
                if "tick_volume" in fields
                else 0
            ),
        })

    candles.sort(key=lambda candle: candle["time"])
    return candles


def validate_candles(candles, timeframe_name):
    if not candles:
        raise ValueError(f"{timeframe_name}: no candles available")

    previous_time = None

    for index, candle in enumerate(candles):
        for key in ("time", "open", "high", "low", "close", "volume"):
            if key not in candle:
                raise ValueError(
                    f"{timeframe_name}: candle {index} missing {key}"
                )

        o = candle["open"]
        h = candle["high"]
        low = candle["low"]
        close = candle["close"]

        values = (o, h, low, close)

        if not all(
            isinstance(value, (int, float))
            and value == value
            and abs(value) != float("inf")
            for value in values
        ):
            raise ValueError(
                f"{timeframe_name}: invalid OHLC at candle {index}"
            )

        if h < max(o, close) or low > min(o, close) or h < low:
            raise ValueError(
                f"{timeframe_name}: inconsistent OHLC at candle {index}"
            )

        if previous_time is not None and candle["time"] <= previous_time:
            raise ValueError(
                f"{timeframe_name}: candles are not chronological"
            )

        previous_time = candle["time"]

    return True


def format_utc(timestamp):
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat(timespec="seconds")


def normalize_bias(value):
    """Map engine bias values into BULLISH, BEARISH, or NEUTRAL."""

    text = str(value or "").strip().upper()

    if "BULL" in text:
        return "BULLISH"

    if "BEAR" in text:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# HISTORICAL VALIDATION
# ============================================================

def report_historical_validation():
    print("=" * 64)
    print("HISTORICAL DATA VALIDATION")
    print("=" * 64)

    results = {}

    for name, timeframe in TIMEFRAMES.items():
        print(f"\nTIMEFRAME: {name}")
        print("-" * 42)

        candles = fetch_completed_candles(
            timeframe,
            HISTORICAL_BARS,
        )
        validate_candles(candles, name)

        results[name] = candles

        print(f"Candles received: {len(candles)}")
        print(f"Valid candles:    {len(candles)}")
        print(f"Oldest candle:    {format_utc(candles[0]['time'])}")
        print(f"Latest candle:    {format_utc(candles[-1]['time'])}")
        print("Validation:       PASSED")

    print("\nAll timeframe validations passed.")
    return results


# ============================================================
# MULTI-TIMEFRAME STRUCTURE
# ============================================================

def analyze_timeframe_structure(name, timeframe):
    candles = fetch_completed_candles(
        timeframe,
        ANALYSIS_BARS,
    )
    validate_candles(candles, name)

    structure = structure_engine.analyze_market_structure(candles)

    return {
        "timeframe": name,
        "candles": candles,
        "structure": structure,
        "bias": normalize_bias(structure.get("bias")),
        "event": structure.get("latest_event"),
        "close": candles[-1]["close"],
        "candle_time": candles[-1]["time"],
    }


def report_multi_timeframe(results):
    print("\n" + "=" * 64)
    print("6. MULTI-TIMEFRAME STRUCTURE ALIGNMENT")
    print("=" * 64)

    for name in ("H4", "H1", "M15", "M5"):
        item = results[name]
        event = item["event"]

        if isinstance(event, dict):
            event_text = (
                f"{event.get('direction', '?')} "
                f"{event.get('type', '?')}"
            )
        elif event:
            event_text = str(event)
        else:
            event_text = "No recent event reported"

        print(
            f"{name:>3} | "
            f"Bias: {item['bias']:<8} | "
            f"Close: {item['close']:.2f} | "
            f"Latest event: {event_text}"
        )

    biases = [results[name]["bias"] for name in ("H4", "H1", "M15", "M5")]
    bullish_count = biases.count("BULLISH")
    bearish_count = biases.count("BEARISH")
    neutral_count = biases.count("NEUTRAL")

    print("\nALIGNMENT SUMMARY")
    print("-" * 42)
    print(f"Bullish timeframes: {bullish_count}/4")
    print(f"Bearish timeframes: {bearish_count}/4")
    print(f"Neutral timeframes: {neutral_count}/4")

    if bullish_count == 4:
        alignment = "FULLY BULLISH ALIGNMENT"
        explanation = "All four timeframe structure biases are bullish."
    elif bearish_count == 4:
        alignment = "FULLY BEARISH ALIGNMENT"
        explanation = "All four timeframe structure biases are bearish."
    elif bullish_count > 0 and bearish_count == 0:
        alignment = "BULLISH, WITH NEUTRAL TIMEFRAMES"
        explanation = "No bearish bias was reported, but alignment is incomplete."
    elif bearish_count > 0 and bullish_count == 0:
        alignment = "BEARISH, WITH NEUTRAL TIMEFRAMES"
        explanation = "No bullish bias was reported, but alignment is incomplete."
    elif bullish_count > 0 and bearish_count > 0:
        alignment = "MIXED / CONFLICTING"
        explanation = "At least one timeframe disagrees with another."
    else:
        alignment = "NO CLEAR DIRECTION"
        explanation = "The structure engine reports neutral bias across timeframes."

    print(f"Alignment: {alignment}")
    print(explanation)

    # Context only at this stage. Do not add these counts to the
    # existing confluence score: that could double-count structure.
    print(
        "\nNote: Multi-timeframe alignment is contextual evidence. "
        "It is not added to the M5 score yet."
    )

    return {
        "alignment": alignment,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "biases": {
            name: results[name]["bias"]
            for name in ("M5", "M15", "H1", "H4")
        },
    }


# ============================================================
# COORDINATED ANALYSIS
# ============================================================

def print_section(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def report_market_analysis():
    print_section("COORDINATED XAUUSD MARKET ANALYSIS")

    # Analyze all four timeframes from completed candles.
    timeframe_results = {}

    for name, timeframe in TIMEFRAMES.items():
        timeframe_results[name] = analyze_timeframe_structure(
            name,
            timeframe,
        )

    # The existing detailed confluence analysis remains on M5.
    m5 = timeframe_results["M5"]
    candles = m5["candles"]
    structure = m5["structure"]
    analysis_price = candles[-1]["close"]

    print(f"Symbol:                  {SYMBOL}")
    print("Primary analysis TF:     M5")
    print(f"Completed M5 candles:    {len(candles)}")
    print(f"Latest completed candle: {format_utc(candles[-1]['time'])}")
    print(f"Analysis candle close:   {analysis_price:.2f}")

    # Run remaining engines once on the same M5 dataset.
    liquidity = liquidity_engine.detect_liquidity(candles)
    fvgs = fvg_engine.detect_fvgs(candles)
    order_blocks = order_block_engine.detect_order_blocks(candles)

    print_section("1. M5 MARKET STRUCTURE")
    print(f"Bias:             {structure.get('bias', 'UNKNOWN')}")
    print(f"Current structure:{' '}{structure.get('structure', 'UNKNOWN')}")
    print(f"Structure events: {len(structure.get('events', []))}")
    print(f"Latest event:     {structure.get('latest_event')}")

    print_section("2. M5 LIQUIDITY ANALYSIS")
    print(f"Swing highs:      {len(liquidity.get('swing_highs', []))}")
    print(f"Swing lows:       {len(liquidity.get('swing_lows', []))}")
    print(f"Buy-side sweeps:  {len(liquidity.get('buy_sweeps', []))}")
    print(f"Sell-side sweeps: {len(liquidity.get('sell_sweeps', []))}")

    print_section("3. M5 FAIR VALUE GAPS")
    print(f"FVG candidates: {len(fvgs)}")

    for index, fvg in enumerate(fvgs[:5], start=1):
        print(
            f"{index}. {fvg.get('type', 'UNKNOWN')} | "
            f"{fvg.get('lower', '?')} - {fvg.get('upper', '?')}"
        )

    if len(fvgs) > 5:
        print(f"... plus {len(fvgs) - 5} additional candidates")

    print_section("4. M5 ORDER BLOCKS")
    print(f"OB candidates: {len(order_blocks)}")

    for index, block in enumerate(order_blocks[:5], start=1):
        print(
            f"{index}. {block.get('type', 'UNKNOWN')} | "
            f"{block.get('lower', '?')} - {block.get('upper', '?')}"
        )

    if len(order_blocks) > 5:
        print(f"... plus {len(order_blocks) - 5} additional candidates")

    print_section("5. M5 COMBINED CONFLUENCE")

    try:
        confluence = confluence_engine.build_confluence(
            candles=candles,
            structure=structure,
            liquidity=liquidity,
            fvgs=fvgs,
            order_blocks=order_blocks,
            current_price=analysis_price,
        )
        confluence_engine.display_confluence(confluence)

    except Exception:
        print("Confluence analysis failed:")
        traceback.print_exc()

    # Multi-timeframe report is separate from the M5 score.
    alignment = report_multi_timeframe(timeframe_results)
    if "confluence" in locals():
        mtf_assessment = mtf_confluence_engine.evaluate_mtf_confluence(
            m5_confluence=confluence,
            timeframe_biases=alignment["biases"],
        )
        mtf_confluence_engine.display_mtf_confluence(mtf_assessment)

    print_section("ANALYSIS SUMMARY")
    print(f"M5 confluence: {confluence.get('assessment', 'UNAVAILABLE') if 'confluence' in locals() else 'UNAVAILABLE'}")
    print(f"MTF alignment: {alignment['alignment']}")
    print("Scores are heuristic, not probabilities or guaranteed signals.")
    print("Market Mind does not place trades.")


# ============================================================
# LIVE QUOTE MONITOR — QUIET STALE STATUS
# ============================================================

def live_feed():
    print_section("LIVE XAUUSD QUOTE MONITOR")
    print("Press Ctrl+C to stop Market Mind.")
    print("Stale quotes are reported on status changes and periodically.")

    last_tick_identity = None
    last_tick_change_monotonic = time.monotonic()
    last_print_monotonic = 0.0
    previous_status = None

    while True:
        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None:
            status = f"QUOTE ERROR: {mt5.last_error()}"
            now = time.monotonic()

            if status != previous_status or (
                now - last_print_monotonic >= LIVE_STATUS_INTERVAL_SECONDS
            ):
                print(status, flush=True)
                previous_status = status
                last_print_monotonic = now

            time.sleep(LIVE_REFRESH_SECONDS)
            continue

        tick_time_msc = getattr(tick, "time_msc", 0)

        if tick_time_msc:
            tick_identity = int(tick_time_msc)
            tick_timestamp = tick_identity / 1000.0
        else:
            tick_identity = int(getattr(tick, "time", 0))
            tick_timestamp = float(tick_identity)

        now_utc = datetime.now(timezone.utc)
        quote_age = max(0.0, now_utc.timestamp() - tick_timestamp)

        if tick_identity != last_tick_identity:
            last_tick_identity = tick_identity
            last_tick_change_monotonic = time.monotonic()

        no_change_seconds = (
            time.monotonic() - last_tick_change_monotonic
        )

        stale = (
            quote_age > STALE_AFTER_SECONDS
            or no_change_seconds > STALE_AFTER_SECONDS
        )

        status = (
            "STALE / LAST-KNOWN QUOTE — MARKET MAY BE CLOSED"
            if stale
            else "QUOTE UPDATING"
        )

        now = time.monotonic()
        should_print = (
            status != previous_status
            or now - last_print_monotonic >= LIVE_STATUS_INTERVAL_SECONDS
        )

        if should_print:
            broker_time = (
                format_utc(int(tick_timestamp))
                if tick_timestamp > 0
                else "Unavailable"
            )

            print(
                f"{now_utc.strftime('%H:%M:%S')} UTC | {status} | "
                f"Bid: {float(tick.bid):.2f} | "
                f"Ask: {float(tick.ask):.2f} | "
                f"Spread: {float(tick.ask - tick.bid):.2f} | "
                f"Broker tick: {broker_time} | "
                f"Tick age: {quote_age:.0f}s | "
                f"No tick change: {no_change_seconds:.0f}s",
                flush=True,
            )

            previous_status = status
            last_print_monotonic = now

        time.sleep(LIVE_REFRESH_SECONDS)


# ============================================================
# MAIN
# ============================================================

def main():
    try:
        initialize_mt5()
        report_historical_validation()
        report_market_analysis()
        live_feed()

    except KeyboardInterrupt:
        print("\nMarket Mind stopped by user.")

    except Exception as exc:
        print(f"\nMARKET MIND ERROR: {exc}")
        traceback.print_exc()

    finally:
        mt5.shutdown()
        print("\nMT5 connection closed.")


if __name__ == "__main__":
    main()

    