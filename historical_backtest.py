from __future__ import annotations

import math
import traceback
from datetime import datetime, timezone

import MetaTrader5 as mt5
import matplotlib.pyplot as plt

import analysis_enigne as structure_engine
import confluence_engine
import fvg_engine
import liquidity_engine
import order_block_engine


# ============================================================
# MARKET MIND V2 — HISTORICAL BACKTEST
# Historical simulation only. No live orders are placed.
# ============================================================

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"

TIMEFRAMES = {
    "M5": (mt5.TIMEFRAME_M5, 300),
    "H1": (mt5.TIMEFRAME_H1, 3600),
    "H4": (mt5.TIMEFRAME_H4, 14400),
}

HISTORY_BARS = 2500
HTF_HISTORY_BARS = 1200
WARMUP_BARS = 150
ATR_PERIOD = 14
MAX_HOLD_BARS = 24

STOP_ATR_MULTIPLE = 1.0
TARGET_R_MULTIPLE = 1.5
MINIMUM_SCORE_GAP = 20.0

# Estimated round-trip cost in XAUUSD price units.
# Replace these estimates with broker-calibrated costs when available.
COST_SCENARIOS = (0.0, 0.25, 0.50, 1.00)
PRIMARY_COST = 0.50

OUT_OF_SAMPLE_FRACTION = 0.30
PROGRESS_EVERY_BARS = 250
RECENT_TRADES_TO_DISPLAY = 10


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_time(timestamp):
    return datetime.fromtimestamp(
        int(timestamp), timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def normalize_bias(value):
    text = str(value or "").strip().upper()

    if "BULL" in text:
        return "BULLISH"
    if "BEAR" in text:
        return "BEARISH"

    return "NEUTRAL"


def fetch_history(timeframe, count):
    """Fetch completed candles only; exclude the forming candle."""
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 1, count)

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"MT5 history request failed: {mt5.last_error()}"
        )

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

        prices = (
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
        )

        if not all(math.isfinite(p) and p > 0 for p in prices):
            raise RuntimeError("Invalid OHLC values in historical data.")

        if (
            candle["high"] < max(candle["open"], candle["close"])
            or candle["low"] > min(candle["open"], candle["close"])
            or candle["high"] < candle["low"]
        ):
            raise RuntimeError("Invalid candle OHLC relationships.")

        candles.append(candle)

    candles.sort(key=lambda c: c["time"])

    timestamps = [c["time"] for c in candles]

    if len(timestamps) != len(set(timestamps)):
        raise RuntimeError("Duplicate historical candle timestamps.")

    if any(
        timestamps[i] >= timestamps[i + 1]
        for i in range(len(timestamps) - 1)
    ):
        raise RuntimeError("Historical candles are not chronological.")

    return candles


def calculate_atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return None

    recent = candles[-(period + 1):]
    true_ranges = []

    for i in range(1, len(recent)):
        current = recent[i]
        previous = recent[i - 1]

        true_ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        ))

    atr = sum(true_ranges[-period:]) / period

    if not math.isfinite(atr) or atr <= 0:
        return None

    return atr


# ============================================================
# M5 ANALYSIS
# ============================================================

def analyze_m5_prefix(candles):
    """
    Every engine receives only candles available at the signal time.
    Existing analysis engine files are not modified.
    """
    structure = structure_engine.analyze_market_structure(candles)

    if not isinstance(structure, dict):
        raise ValueError("Market structure engine returned invalid data.")

    liquidity = liquidity_engine.detect_liquidity(candles)

    if not isinstance(liquidity, dict):
        raise ValueError("Liquidity engine returned invalid data.")

    fvgs = fvg_engine.detect_fvgs(candles)
    order_blocks = order_block_engine.detect_order_blocks(candles)

    confluence = confluence_engine.build_confluence(
        candles=candles,
        structure=structure,
        liquidity=liquidity,
        fvgs=fvgs if fvgs is not None else [],
        order_blocks=order_blocks if order_blocks is not None else [],
        current_price=candles[-1]["close"],
    )

    if not isinstance(confluence, dict):
        raise ValueError("Confluence engine returned invalid data.")

    return structure, confluence


def classify_setup(confluence, structure):
    """
    Reject weak directional LEAN assessments.
    Require exact strong confluence, matching structure, and score gap.
    """
    try:
        bullish = float(confluence["bullish_score"])
        bearish = float(confluence["bearish_score"])
    except (KeyError, TypeError, ValueError):
        return None, "Missing score fields"

    if not (math.isfinite(bullish) and math.isfinite(bearish)):
        return None, "Invalid scores"

    bias = normalize_bias(structure.get("bias"))
    assessment = str(
        confluence.get("assessment", "")
    ).strip().upper()

    difference = bullish - bearish

    if (
        bias == "BULLISH"
        and assessment == "BULLISH CONFLUENCE"
        and difference >= MINIMUM_SCORE_GAP
    ):
        return "BUY", "Strong bullish confluence"

    if (
        bias == "BEARISH"
        and assessment == "BEARISH CONFLUENCE"
        and -difference >= MINIMUM_SCORE_GAP
    ):
        return "SELL", "Strong bearish confluence"

    return None, "Entry conditions not satisfied"


# ============================================================
# HIGHER-TIMEFRAME STRUCTURE
# ============================================================

def get_htf_bias(signal_close_time, candles, timeframe_name, cache):
    """
    Only use H1/H4 candles whose closing times are no later than
    the M5 signal candle's closing time.
    """
    _, seconds = TIMEFRAMES[timeframe_name]

    available = [
        candle for candle in candles
        if candle["time"] + seconds <= signal_close_time
    ]

    if len(available) < WARMUP_BARS:
        return "NEUTRAL"

    # Limit computation while preserving a recent structure window.
    available = available[-500:]
    cache_key = (timeframe_name, available[-1]["time"])

    if cache_key not in cache:
        result = structure_engine.analyze_market_structure(available)

        if isinstance(result, dict):
            cache[cache_key] = normalize_bias(result.get("bias"))
        else:
            cache[cache_key] = "NEUTRAL"

    return cache[cache_key]


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(candles, signal_index, direction, atr):
    """
    Signal: completed candle close.
    Entry: next candle open.
    Stop: 1 ATR.
    Target: 1.5R.
    Maximum holding period: 24 M5 candles.

    If stop and target are both touched in one candle, stop wins.
    Stop gaps use the opening price when worse than the stop level.
    OHLC data cannot establish the actual intrabar price sequence.
    """
    entry_index = signal_index + 1

    if entry_index >= len(candles):
        return None

    entry = candles[entry_index]["open"]
    risk_distance = atr * STOP_ATR_MULTIPLE

    if not math.isfinite(risk_distance) or risk_distance <= 0:
        return None

    reward_distance = risk_distance * TARGET_R_MULTIPLE

    if direction == "BUY":
        stop = entry - risk_distance
        target = entry + reward_distance
    else:
        stop = entry + risk_distance
        target = entry - reward_distance

    last_index = min(
        len(candles) - 1,
        entry_index + MAX_HOLD_BARS - 1,
    )

    exit_index = last_index
    exit_price = candles[last_index]["close"]
    exit_reason = "TIME EXIT"

    for i in range(entry_index, last_index + 1):
        candle = candles[i]

        if direction == "BUY":
            if candle["open"] <= stop:
                exit_index = i
                exit_price = candle["open"]
                exit_reason = "STOP GAP"
                break

            if candle["open"] >= target:
                exit_index = i
                exit_price = target
                exit_reason = "TARGET GAP"
                break

            stop_hit = candle["low"] <= stop
            target_hit = candle["high"] >= target

        else:
            if candle["open"] >= stop:
                exit_index = i
                exit_price = candle["open"]
                exit_reason = "STOP GAP"
                break

            if candle["open"] <= target:
                exit_index = i
                exit_price = target
                exit_reason = "TARGET GAP"
                break

            stop_hit = candle["high"] >= stop
            target_hit = candle["low"] <= target

        if stop_hit and target_hit:
            exit_index = i
            exit_price = stop
            exit_reason = "STOP — BOTH LEVELS TOUCHED"
            break

        if stop_hit:
            exit_index = i
            exit_price = stop
            exit_reason = "STOP HIT"
            break

        if target_hit:
            exit_index = i
            exit_price = target
            exit_reason = "TARGET HIT"
            break

    gross_profit = (
        exit_price - entry
        if direction == "BUY"
        else entry - exit_price
    )

    return {
        "signal_index": signal_index,
        "entry_index": entry_index,
        "exit_index": exit_index,
        "signal_time": candles[signal_index]["time"],
        "entry_time": candles[entry_index]["time"],
        "exit_time": candles[exit_index]["time"],
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "gross_profit": gross_profit,
        "risk_distance": risk_distance,
        "gross_r": gross_profit / risk_distance,
    }


# ============================================================
# PORTFOLIO CONSTRUCTION
# ============================================================

def build_portfolio(candles, signals, require_mtf=False):
    """
    Simulate one open position at a time.
    Baseline and MTF-filtered portfolios are calculated separately.
    """
    trades = []
    next_allowed_index = WARMUP_BARS

    for signal in signals:
        index = signal["signal_index"]

        if index < next_allowed_index:
            continue

        if require_mtf and not signal["mtf_confirmed"]:
            continue

        trade = simulate_trade(
            candles,
            index,
            signal["direction"],
            signal["atr"],
        )

        if trade is None:
            continue

        trade["h1_bias"] = signal["h1_bias"]
        trade["h4_bias"] = signal["h4_bias"]
        trade["mtf_confirmed"] = signal["mtf_confirmed"]

        trades.append(trade)
        next_allowed_index = trade["exit_index"] + 1

    return trades


# ============================================================
# METRICS AND COST SENSITIVITY
# ============================================================

def net_r_for_cost(trade, cost):
    return (
        trade["gross_profit"] - cost
    ) / trade["risk_distance"]


def calculate_metrics(trades, cost):
    if not trades:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "gross_r": 0.0,
            "net_r": 0.0,
            "average_net_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }

    net_values = [net_r_for_cost(t, cost) for t in trades]

    wins = sum(value > 0 for value in net_values)
    losses = sum(value < 0 for value in net_values)
    breakeven = len(net_values) - wins - losses

    winning_sum = sum(value for value in net_values if value > 0)
    losing_sum = abs(sum(value for value in net_values if value < 0))

    if losing_sum > 0:
        profit_factor = winning_sum / losing_sum
    elif winning_sum > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    return {
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wins / len(trades) * 100.0,
        "gross_r": sum(t["gross_r"] for t in trades),
        "net_r": sum(net_values),
        "average_net_r": sum(net_values) / len(net_values),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
    }


def print_metrics(label, trades, cost):
    m = calculate_metrics(trades, cost)

    print(f"\n{label}")
    print("-" * 68)
    print(f"Trades:                 {m['count']}")
    print(f"Net wins:               {m['wins']}")
    print(f"Net losses:             {m['losses']}")
    print(f"Breakeven:              {m['breakeven']}")
    print(f"Win rate:               {m['win_rate']:.2f}%")
    print(f"Total gross R:          {m['gross_r']:.2f}R")
    print(f"Total net R:            {m['net_r']:.2f}R")
    print(f"Average net R/trade:    {m['average_net_r']:.3f}R")
    print(f"Profit factor (net):    {m['profit_factor']:.3f}")
    print(f"Maximum drawdown:       {m['max_drawdown']:.2f}R")

    return m


def print_cost_sensitivity(trades):
    print("\nCOST SENSITIVITY — SAME TRADE ENTRIES AND EXITS")
    print("-" * 68)
    print("Costs are estimates in XAUUSD price units per round trip.")
    print(
        f"{'Cost':>10} {'Net R':>12} {'Avg net R':>12} "
        f"{'PF':>10} {'Win rate':>12}"
    )

    for cost in COST_SCENARIOS:
        m = calculate_metrics(trades, cost)
        print(
            f"{cost:>10.2f} "
            f"{m['net_r']:>12.2f} "
            f"{m['average_net_r']:>12.3f} "
            f"{m['profit_factor']:>10.3f} "
            f"{m['win_rate']:>11.2f}%"
        )


def print_exit_reasons(trades):
    targets = sum(t["exit_reason"].startswith("TARGET") for t in trades)
    stops = sum(t["exit_reason"].startswith("STOP") for t in trades)
    timed = sum(t["exit_reason"] == "TIME EXIT" for t in trades)

    print("\nEXIT REASONS")
    print("-" * 48)
    print(f"Target exits:           {targets}")
    print(f"Stop exits:             {stops}")
    print(f"Time exits:             {timed}")


def print_recent_trades(trades, cost):
    print("\nRECENT TRADES")
    print("-" * 90)

    for trade in trades[-RECENT_TRADES_TO_DISPLAY:]:
        net_r = net_r_for_cost(trade, cost)

        outcome = (
            "NET WIN" if net_r > 0
            else "NET LOSS" if net_r < 0
            else "BREAKEVEN"
        )

        print(
            f"{utc_time(trade['signal_time'])} | "
            f"{trade['direction']:<4} | "
            f"Entry {trade['entry']:.2f} | "
            f"Exit {trade['exit']:.2f} | "
            f"{net_r:+.2f}R | {trade['exit_reason']} | {outcome}"
        )


def print_train_test(trades, split_timestamp, cost, label):
    earlier = [
        t for t in trades if t["signal_time"] < split_timestamp
    ]
    later = [
        t for t in trades if t["signal_time"] >= split_timestamp
    ]

    print(f"\n{label} — CHRONOLOGICAL HOLDOUT")
    print("-" * 68)
    print("Split timestamp:", utc_time(split_timestamp))
    print_metrics("EARLIER PERIOD", earlier, cost)
    print_metrics("LATER HOLDOUT PERIOD", later, cost)


# ============================================================
# CHARTS
# ============================================================

def plot_results(baseline_trades, mtf_trades):
    """
    Plot cumulative net R for both strategies at the primary cost.
    The visual is a trade-sequence comparison, not account equity.
    """
    plt.figure(figsize=(12, 6))

    for trades, label in (
        (baseline_trades, "Strict M5 confluence"),
        (mtf_trades, "M5 + H1/H4 structure confirmation"),
    ):
        if not trades:
            continue

        equity = []
        total = 0.0

        for trade in trades:
            total += net_r_for_cost(trade, PRIMARY_COST)
            equity.append(total)

        plt.plot(range(1, len(equity) + 1), equity, label=label)

    plt.axhline(0, linewidth=1)
    plt.title("Market Mind V2 — Historical Backtest")
    plt.xlabel("Completed trades")
    plt.ylabel("Cumulative net R")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()


# ============================================================
# MAIN
# ============================================================

def main():
    mt5_ready = False
    m5 = []
    baseline_trades = []
    mtf_trades = []
    skipped = 0

    print("=" * 68)
    print("MARKET MIND V2 — HISTORICAL BACKTEST")
    print("=" * 68)

    try:
        if not mt5.initialize(path=MT5_PATH, timeout=60000):
            raise RuntimeError(
                f"MT5 initialization failed: {mt5.last_error()}"
            )

        mt5_ready = True

        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(f"Could not select {SYMBOL}")

        m5 = fetch_history(TIMEFRAMES["M5"][0], HISTORY_BARS)
        h1 = fetch_history(TIMEFRAMES["H1"][0], HTF_HISTORY_BARS)
        h4 = fetch_history(TIMEFRAMES["H4"][0], HTF_HISTORY_BARS)

        if len(m5) <= WARMUP_BARS + MAX_HOLD_BARS:
            raise RuntimeError("Not enough M5 history for this test.")

        print(f"Symbol: {SYMBOL}")
        print(f"M5 candles loaded: {len(m5)}")
        print(f"H1 candles loaded: {len(h1)}")
        print(f"H4 candles loaded: {len(h4)}")
        print("First M5 candle:", utc_time(m5[0]["time"]))
        print("Last M5 candle: ", utc_time(m5[-1]["time"]))
        print(f"Primary estimated cost: {PRIMARY_COST:.2f}")
        print("\nGenerating chronological signals...")

        htf_cache = {}
        signals = []
        final_signal_index = len(m5) - MAX_HOLD_BARS

        for i in range(WARMUP_BARS, final_signal_index):
            atr = calculate_atr(m5[:i + 1])

            if atr is None:
                continue

            try:
                structure, confluence = analyze_m5_prefix(m5[:i + 1])
                direction, _ = classify_setup(confluence, structure)

            except Exception as exc:
                skipped += 1

                if skipped <= 5:
                    print(
                        f"Analysis error at candle {i}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                continue

            if direction is None:
                if (
                    i - WARMUP_BARS + 1
                ) % PROGRESS_EVERY_BARS == 0:
                    print(f"Scanned candle {i}/{final_signal_index}")
                continue

            signal_close_time = (
                m5[i]["time"] + TIMEFRAMES["M5"][1]
            )

            h1_bias = get_htf_bias(
                signal_close_time, h1, "H1", htf_cache
            )
            h4_bias = get_htf_bias(
                signal_close_time, h4, "H4", htf_cache
            )

            required_bias = "BULLISH" if direction == "BUY" else "BEARISH"

            mtf_confirmed = (
                h1_bias == required_bias
                and h4_bias == required_bias
            )

            signals.append({
                "signal_index": i,
                "direction": direction,
                "atr": atr,
                "mtf_confirmed": mtf_confirmed,
                "h1_bias": h1_bias,
                "h4_bias": h4_bias,
            })

            if len(signals) % 25 == 0:
                print(
                    f"Qualifying signals: {len(signals)} "
                    f"| Candle {i}/{final_signal_index}",
                    flush=True,
                )

        # Independent, non-overlapping portfolios.
        baseline_trades = build_portfolio(m5, signals, False)
        mtf_trades = build_portfolio(m5, signals, True)

        split_index = int(
            len(m5) * (1.0 - OUT_OF_SAMPLE_FRACTION)
        )
        split_timestamp = m5[split_index]["time"]

        print("\n" + "=" * 68)
        print("BACKTEST RESULTS")
        print("=" * 68)
        print(f"Historical candles:     {len(m5)}")
        print(f"Qualifying M5 signals:  {len(signals)}")
        print(
            "MTF-confirmed signals: ",
            sum(s["mtf_confirmed"] for s in signals),
        )
        print(f"Analysis errors/skips:  {skipped}")
        print(
            "Cost scenarios:        ",
            ", ".join(f"{cost:.2f}" for cost in COST_SCENARIOS),
            )

        print_metrics(
            "BASELINE — STRICT M5 CONFLUENCE",
            baseline_trades,
            PRIMARY_COST,
        )
        print_exit_reasons(baseline_trades)
        print_cost_sensitivity(baseline_trades)
        print_recent_trades(baseline_trades, PRIMARY_COST)
        print_train_test(
            baseline_trades,
            split_timestamp,
            PRIMARY_COST,
            "BASELINE STRATEGY",
        )

        print_metrics(
            "FILTERED — M5 + H1 AND H4 STRUCTURE AGREEMENT",
            mtf_trades,
            PRIMARY_COST,
        )
        print_exit_reasons(mtf_trades)
        print_cost_sensitivity(mtf_trades)
        print_recent_trades(mtf_trades, PRIMARY_COST)
        print_train_test(
            mtf_trades,
            split_timestamp,
            PRIMARY_COST,
            "MTF-FILTERED STRATEGY",
        )

        print("\nIMPORTANT LIMITATIONS")
        print("- This script does not place live or demo orders.")
        print("- Costs are estimates, not verified broker execution costs.")
        print("- H1/H4 candles are used only after their close time.")
        print("- Same-candle stop/target collisions assume stop first.")
        print("- OHLC data cannot reproduce actual intrabar tick execution.")
        print("- The later 30% is a historical holdout, not proof of robustness.")
        print("- Imported engine internals still need a full look-ahead audit.")
        print("- Scores are heuristic evidence scores, not probabilities.")
        print("=" * 68)

    except KeyboardInterrupt:
        print("\nBacktest interrupted. Results may be incomplete.")

    finally:
        if mt5_ready:
            mt5.shutdown()

        print("\nMT5 connection closed.")

    # Display results after the MT5 connection is closed.
    if m5 and (baseline_trades or mtf_trades):
        plot_results(baseline_trades, mtf_trades)

    # Required: display the backtest chart.
    plt.show()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nBACKTEST ERROR: {exc}")
        traceback.print_exc()