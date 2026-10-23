# ============================================================
# MARKET MIND V2
# PART 1 OF 4 — CORE ENGINE AND MARKET ANALYSIS
# Run command after all parts: python dashboard.py
# ============================================================

import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle


# -------------------- CONFIGURATION ---------------------------

APP_NAME = "MARKET MIND V2"
SYMBOL = "XAUUSD"

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

BARS_TO_FETCH = 250
CHART_BARS = 100

SWING_STRENGTH = 3
ATR_PERIOD = 14

# Risk settings are analytical defaults, not guarantees.
ATR_SL_MULTIPLIER = 1.5
MIN_RISK_REWARD = 1.0
MAX_TAKE_PROFITS = 5

POLL_INTERVAL_MS = 1000
REFRESH_INTERVAL_MS = 3000

BG = "#0B1220"
PANEL = "#131F32"
PANEL_ALT = "#17263B"
BORDER = "#2B3B52"
TEXT = "#E8EEF8"
MUTED = "#93A4BC"
CYAN = "#39D5E8"
GREEN = "#28D69A"
RED = "#FF647C"
GOLD = "#F6C96B"
PURPLE = "#B49AFF"


# -------------------- MT5 CONNECTION -------------------------

def connect_mt5():
    """Connect to the running MetaTrader 5 terminal."""

    if mt5.terminal_info() is not None:
        return True, "Connected to MT5"

    initialized = mt5.initialize(
        path=MT5_PATH,
        timeout=60000
    )

    if not initialized:
        error = mt5.last_error()
        return False, f"MT5 initialization failed: {error}"

    if not mt5.symbol_select(SYMBOL, True):
        error = mt5.last_error()
        return False, f"Could not select {SYMBOL}: {error}"

    if mt5.account_info() is None:
        error = mt5.last_error()
        return False, f"MT5 account unavailable: {error}"

    return True, "Connected to MT5"


def ensure_mt5_connection():
    """Reconnect if the terminal connection has been lost."""

    if mt5.terminal_info() is not None:
        return True

    mt5.shutdown()
    time.sleep(0.3)

    initialized = mt5.initialize(
        path=MT5_PATH,
        timeout=60000
    )

    if initialized:
        mt5.symbol_select(SYMBOL, True)

    return initialized


# -------------------- LIVE QUOTE -----------------------------

def get_live_quote():
    """Return current bid, ask, spread and quote age."""

    if not ensure_mt5_connection():
        return None

    tick = mt5.symbol_info_tick(SYMBOL)

    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        return None

    tick_time = getattr(tick, "time_msc", 0)

    if tick_time:
        tick_timestamp = tick_time / 1000.0
    else:
        tick_timestamp = float(tick.time)

    now = time.time()
    age = max(0.0, now - tick_timestamp)

    return {
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "spread": float(tick.ask - tick.bid),
        "age": age,
        "tick_time": datetime.fromtimestamp(
            tick_timestamp, timezone.utc
        ),
    }


# -------------------- HISTORICAL DATA ------------------------

def get_market_data(timeframe_name):
    """
    Fetch completed candles only.
    Position zero, the forming candle, is excluded.
    """

    if timeframe_name not in TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe: {timeframe_name}"
        )

    if not ensure_mt5_connection():
        raise RuntimeError(
            f"MT5 unavailable: {mt5.last_error()}"
        )

    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError(
            f"Unable to select {SYMBOL}: {mt5.last_error()}"
        )

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        TIMEFRAMES[timeframe_name],
        1,
        BARS_TO_FETCH
    )

    if rates is None or len(rates) < 40:
        raise RuntimeError(
            f"Insufficient {timeframe_name} data: "
            f"{mt5.last_error()}"
        )

    df = pd.DataFrame(rates)

    df["time"] = pd.to_datetime(
        df["time"], unit="s", utc=True
    )

    numeric_columns = [
        "open", "high", "low", "close", "tick_volume"
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column], errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    return df


# -------------------- INDICATORS -----------------------------

def calculate_atr(df, period=ATR_PERIOD):
    """Calculate Average True Range."""

    previous_close = df["close"].shift(1)

    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1
    )

    true_range = ranges.max(axis=1)

    return true_range.rolling(
        period, min_periods=period
    ).mean()


def enrich_market_data(df):
    """Add ATR and basic candle statistics."""

    df = df.copy()

    df["atr"] = calculate_atr(df)

    df["body"] = (
        df["close"] - df["open"]
    ).abs()

    df["range"] = (
        df["high"] - df["low"]
    ).replace(0, np.nan)

    df["bullish"] = df["close"] > df["open"]
    df["bearish"] = df["close"] < df["open"]

    return df


# -------------------- SWING DETECTION ------------------------

def find_swings(df, strength=SWING_STRENGTH):
    """
    Identify confirmed historical swing highs and lows.
    A swing is confirmed only after the required candles
    to its right have closed.
    """

    highs = []
    lows = []

    high_values = df["high"].to_numpy()
    low_values = df["low"].to_numpy()

    for i in range(
        strength, len(df) - strength
    ):
        left_highs = high_values[i-strength:i]
        right_highs = high_values[i+1:i+strength+1]

        left_lows = low_values[i-strength:i]
        right_lows = low_values[i+1:i+strength+1]

        if (
            high_values[i] > left_highs.max()
            and high_values[i] >= right_highs.max()
        ):
            highs.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": float(high_values[i]),
            })

        if (
            low_values[i] < left_lows.min()
            and low_values[i] <= right_lows.min()
        ):
            lows.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": float(low_values[i]),
            })

    return highs, lows


# -------------------- FAIR VALUE GAPS -------------------------

def find_fvgs(df):
    """
    Detect three-candle fair value gap candidates.
    These are geometric gaps, not guaranteed trade setups.
    """

    gaps = []

    for i in range(2, len(df)):
        first = df.iloc[i - 2]
        middle = df.iloc[i - 1]
        current = df.iloc[i]

        middle_range = float(
            middle["high"] - middle["low"]
        )

        body_ratio = (
            float(middle["body"]) / middle_range
            if middle_range > 0 else 0.0
        )

        # Bullish gap: first candle's high is below
        # the third candle's low.
        if float(current["low"]) > float(first["high"]):
            lower = float(first["high"])
            upper = float(current["low"])

            gaps.append({
                "type": "BULLISH",
                "lower": lower,
                "upper": upper,
                "formed_index": i,
                "formed_time": current["time"],
                "body_ratio": body_ratio,
            })

        # Bearish gap: first candle's low is above
        # the third candle's high.
        if float(current["high"]) < float(first["low"]):
            lower = float(current["high"])
            upper = float(first["low"])

            gaps.append({
                "type": "BEARISH",
                "lower": lower,
                "upper": upper,
                "formed_index": i,
                "formed_time": current["time"],
                "body_ratio": body_ratio,
            })

    # Determine whether later completed candles have
    # crossed the entire gap.
    for gap in gaps:
        later = df.iloc[gap["formed_index"] + 1:]

        if later.empty:
            gap["status"] = "UNMITIGATED"
            continue

        if gap["type"] == "BULLISH":
            crossed = (
                later["low"] <= gap["lower"]
            ).any()
        else:
            crossed = (
                later["high"] >= gap["upper"]
            ).any()

        gap["status"] = (
            "FULLY CROSSED" if crossed
            else "UNMITIGATED"
        )

    return gaps


# -------------------- LIQUIDITY SWEEPS -----------------------

def detect_liquidity(df, swing_highs, swing_lows):
    """Identify recent sweep candidates and key swing levels."""

    last = df.iloc[-1]

    recent = df.iloc[-4:-1] if len(df) >= 4 else df.iloc[:-1]

    previous_high = (
        float(recent["high"].max())
        if not recent.empty else None
    )

    previous_low = (
        float(recent["low"].min())
        if not recent.empty else None
    )

    swept_high = bool(
        previous_high is not None
        and float(last["high"]) > previous_high
        and float(last["close"]) < previous_high
    )

    swept_low = bool(
        previous_low is not None
        and float(last["low"]) < previous_low
        and float(last["close"]) > previous_low
    )

    return {
        "buy_side_levels": sorted(
            [x["price"] for x in swing_highs],
            reverse=True
        )[:8],

        "sell_side_levels": sorted(
            [x["price"] for x in swing_lows]
        )[:8],

        "swept_high": swept_high,
        "swept_low": swept_low,
        "previous_high": previous_high,
        "previous_low": previous_low,
    }


# -------------------- MARKET STRUCTURE -----------------------

def analyze_structure(df, swing_highs, swing_lows):
    """Assess structure using completed-candle closes."""

    last_close = float(df.iloc[-1]["close"])

    recent_highs = swing_highs[-5:]
    recent_lows = swing_lows[-5:]

    last_high = (
        recent_highs[-1]["price"]
        if recent_highs else None
    )

    last_low = (
        recent_lows[-1]["price"]
        if recent_lows else None
    )

    previous_high = (
        recent_highs[-2]["price"]
        if len(recent_highs) >= 2 else None
    )

    previous_low = (
        recent_lows[-2]["price"]
        if len(recent_lows) >= 2 else None
    )

    bullish_break = bool(
        last_high is not None and last_close > last_high
    )

    bearish_break = bool(
        last_low is not None and last_close < last_low
    )

    if bullish_break:
        structure = "BULLISH BOS"
    elif bearish_break:
        structure = "BEARISH BOS"
    elif (
        last_high is not None
        and previous_high is not None
        and last_low is not None
        and previous_low is not None
        and last_high > previous_high
        and last_low > previous_low
    ):
        structure = "BULLISH STRUCTURE"
    elif (
        last_high is not None
        and previous_high is not None
        and last_low is not None
        and previous_low is not None
        and last_high < previous_high
        and last_low < previous_low
    ):
        structure = "BEARISH STRUCTURE"
    else:
        structure = "RANGING / UNCONFIRMED"

    if "BULLISH" in structure:
        bias = "BULLISH"
    elif "BEARISH" in structure:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "structure": structure,
        "last_swing_high": last_high,
        "last_swing_low": last_low,
    }


# -------------------- SIGNAL AND RISK ------------------------

def build_trade_analysis(
    df, swing_highs, swing_lows, fvgs, liquidity, structure
):
    """
    Produce an explainable analytical candidate.
    This is not a validated profitability model.
    """

    df = enrich_market_data(df)

    last = df.iloc[-1]
    close = float(last["close"])
    atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0

    result = {
        "signal": "NO TRADE",
        "bias": structure["bias"],
        "entry": None,
        "sl": None,
        "targets": [],
        "rr": [],
        "confidence": 0,
        "reasons": [],
        "warnings": [],
    }

    if atr <= 0 or not np.isfinite(atr):
        result["warnings"].append(
            "ATR is unavailable; risk levels cannot be calculated."
        )
        return result

    # Require both structural direction and at least
    # one additional piece of supporting evidence.
    bullish_fvg = any(
        g["type"] == "BULLISH"
        and g["status"] == "UNMITIGATED"
        and g["lower"] <= close
        for g in fvgs[-12:]
    )

    bearish_fvg = any(
        g["type"] == "BEARISH"
        and g["status"] == "UNMITIGATED"
        and g["upper"] >= close
        for g in fvgs[-12:]
    )

    bullish_sweep = liquidity["swept_low"]
    bearish_sweep = liquidity["swept_high"]

    bullish_momentum = (
        close > float(df.iloc[-2]["close"])
        and close > float(last["open"])
    )

    bearish_momentum = (
        close < float(df.iloc[-2]["close"])
        and close < float(last["open"])
    )

    buy_score = (
        2 * (structure["bias"] == "BULLISH")
        + 2 * bullish_sweep
        + bullish_fvg
        + bullish_momentum
    )

    sell_score = (
        2 * (structure["bias"] == "BEARISH")
        + 2 * bearish_sweep
        + bearish_fvg
        + bearish_momentum
    )

    if buy_score >= 3 and buy_score > sell_score:
        direction = "BUY"
        score = int(buy_score)

    elif sell_score >= 3 and sell_score > buy_score:
        direction = "SELL"
        score = int(sell_score)

    else:
        result["reasons"].append(
            "Insufficient agreement between structure, "
            "liquidity and momentum."
        )
        result["warnings"].append(
            "No entry is recommended by this rule set."
        )
        return result

    entry = close
    stop_distance = ATR_SL_MULTIPLIER * atr

    if direction == "BUY":
        sl = entry - stop_distance
    else:
        sl = entry + stop_distance

    # Use prior confirmed swing levels as candidate targets.
    if direction == "BUY":
        candidates = [
            x["price"] for x in swing_highs
            if x["price"] > entry
        ]
        candidates = sorted(set(candidates))
    else:
        candidates = [
            x["price"] for x in swing_lows
            if x["price"] < entry
        ]
        candidates = sorted(set(candidates), reverse=True)

    # ATR-based fallback levels are estimates, not guaranteed
    # liquidity targets.
    targets = []

    for level in candidates:
        reward = (
            level - entry
            if direction == "BUY"
            else entry - level
        )

        risk = abs(entry - sl)

        if reward > 0 and reward / risk >= MIN_RISK_REWARD:
            if not any(
                abs(level - existing) < atr * 0.2
                for existing in targets
            ):
                targets.append(float(level))

        if len(targets) >= MAX_TAKE_PROFITS:
            break

    for multiplier in (1, 2, 3, 4, 5):
        if len(targets) >= MAX_TAKE_PROFITS:
            break

        fallback = (
            entry + multiplier * atr
            if direction == "BUY"
            else entry - multiplier * atr
        )

        if direction == "BUY":
            is_valid = fallback > entry
        else:
            is_valid = fallback < entry

        if is_valid and not any(
            abs(fallback - existing) < atr * 0.2
            for existing in targets
        ):
            targets.append(float(fallback))

    targets.sort(reverse=(direction == "SELL"))
    targets = targets[:MAX_TAKE_PROFITS]

    risk = abs(entry - sl)
    rr_values = [
        round(
            (
                (tp - entry) if direction == "BUY"
                else (entry - tp)
            ) / risk,
            2
        )
        for tp in targets
    ]

    result.update({
        "signal": direction,
        "entry": entry,
        "sl": sl,
        "targets": targets,
        "rr": rr_values,
        "confidence": min(95, 50 + score * 8),
        "reasons": [
            f"Market structure: {structure['structure']}",
            (
                "Liquidity sweep detected."
                if bullish_sweep or bearish_sweep
                else "No recent sweep confirmation."
            ),
            (
                "Supporting FVG found."
                if bullish_fvg or bearish_fvg
                else "No qualifying unmitigated FVG confirmation."
            ),
            (
                "Last completed candle supports the direction."
                if bullish_momentum or bearish_momentum
                else "Momentum confirmation is limited."
            ),
        ],
        "warnings": [
            "Illustrative rule-based signal; not a proven strategy.",
            "Validate spread, execution costs and stop distance.",
            "Targets are analytical levels, not guaranteed outcomes.",
        ],
    })

    return result


# ============================================================
# END OF PART 1
# Paste PART 2 directly below this line.
# Do not run the file yet.
# ============================================================
# ============================================================
# PART 2 OF 4 — DASHBOARD UI AND SIGNAL PANELS
# Paste directly below Part 1.
# ============================================================

class MarketMindDashboard:

    def __init__(self, root):
        self.root = root
        self.root.title("MARKET MIND V2 | XAUUSD Intelligence")
        self.root.geometry("1600x950")
        self.root.minsize(1150, 750)
        self.root.configure(bg=BG)

        self.current_timeframe = "M5"
        self.market_data = None
        self.swing_highs = []
        self.swing_lows = []
        self.fvgs = []
        self.liquidity = {}
        self.structure = {}
        self.analysis = {}
        self.quote = None

        self.is_refreshing = False
        self.last_data_update = None
        self.last_error = None

        self._configure_styles()
        self._build_interface()

        # Connect and begin refresh cycles.
        self.connection_status.set("CONNECTING TO MT5...")
        self.root.after(200, self.refresh_all)
        self.root.after(POLL_INTERVAL_MS, self.refresh_quote)
        self.root.after(
            REFRESH_INTERVAL_MS, self.refresh_market_data
        )

    # -------------------- STYLES -----------------------------

    def _configure_styles(self):
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "MM.TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=CYAN,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=7,
        )

        style.map(
            "MM.TCombobox",
            fieldbackground=[("readonly", PANEL_ALT)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_ALT)],
            selectforeground=[("readonly", TEXT)],
        )

        style.configure(
            "MM.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=29,
            borderwidth=0,
            font=("Segoe UI", 10),
        )

        style.configure(
            "MM.Treeview.Heading",
            background=PANEL_ALT,
            foreground=CYAN,
            font=("Segoe UI", 10, "bold"),
            padding=8,
            relief="flat",
        )

        style.map(
            "MM.Treeview",
            background=[("selected", "#23415C")],
            foreground=[("selected", TEXT)],
        )

    # -------------------- UI HELPERS -------------------------

    def make_panel(
        self, parent, title, subtitle=None, padding=12
    ):
        outer = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

        header = tk.Frame(outer, bg=PANEL)
        header.pack(
            fill="x", padx=padding, pady=(padding, 5)
        )

        tk.Label(
            header,
            text=title.upper(),
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(side="left")

        if subtitle:
            tk.Label(
                header,
                text=subtitle,
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9),
                anchor="e",
            ).pack(side="right")

        content = tk.Frame(outer, bg=PANEL)
        content.pack(
            fill="both",
            expand=True,
            padx=padding,
            pady=(3, padding),
        )

        return outer, content

    def make_metric_card(
        self, parent, title, variable, accent=CYAN
    ):
        card = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

        tk.Frame(
            card, bg=accent, height=3
        ).pack(fill="x")

        tk.Label(
            card,
            text=title.upper(),
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 4))

        tk.Label(
            card,
            textvariable=variable,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 17, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 12))

        return card

    def make_value_row(
        self, parent, label, variable, color=TEXT
    ):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=4)

        tk.Label(
            row,
            text=label,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left")

        tk.Label(
            row,
            textvariable=variable,
            bg=PANEL,
            fg=color,
            font=("Segoe UI", 9, "bold"),
            anchor="e",
            justify="right",
        ).pack(side="right", fill="x", expand=True)

    # -------------------- BUILD INTERFACE --------------------

    def _build_interface(self):
        # Main scrolling is handled by the operating window;
        # keep the dashboard sections responsive.
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(fill="both", expand=True)

        # Header
        header = tk.Frame(self.main, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 10))

        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")

        tk.Label(
            brand,
            text="MARKET MIND",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")

        tk.Label(
            brand,
            text="V2  /  XAUUSD MARKET INTELLIGENCE",
            bg=BG,
            fg=CYAN,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(2, 0))

        status_box = tk.Frame(header, bg=BG)
        status_box.pack(side="right", anchor="e")

        self.connection_status = tk.StringVar(
            value="WAITING FOR MT5"
        )

        self.clock_status = tk.StringVar(
            value="UTC --:--:--"
        )

        tk.Label(
            status_box,
            textvariable=self.connection_status,
            bg=BG,
            fg=GOLD,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="e")

        tk.Label(
            status_box,
            textvariable=self.clock_status,
            bg=BG,
            fg=MUTED,
            font=("Consolas", 9),
        ).pack(anchor="e", pady=(3, 0))

        # Metric cards
        self.bid_var = tk.StringVar(value="--")
        self.ask_var = tk.StringVar(value="--")
        self.spread_var = tk.StringVar(value="--")
        self.close_var = tk.StringVar(value="--")
        self.bias_var = tk.StringVar(value="WAITING")
        self.age_var = tk.StringVar(value="--")

        metric_frame = tk.Frame(self.main, bg=BG)
        metric_frame.pack(fill="x", padx=18, pady=5)

        metrics = [
            ("BID PRICE", self.bid_var, CYAN),
            ("ASK PRICE", self.ask_var, "#79B8FF"),
            ("SPREAD", self.spread_var, GOLD),
            ("CANDLE CLOSE", self.close_var, PURPLE),
            ("MARKET BIAS", self.bias_var, GREEN),
            ("QUOTE AGE", self.age_var, RED),
        ]

        for index, (title, variable, accent) in enumerate(metrics):
            metric_frame.grid_columnconfigure(
                index, weight=1, uniform="metric"
            )

            card = self.make_metric_card(
                metric_frame, title, variable, accent
            )

            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 5, 0),
                pady=2,
            )

        # Toolbar and timeframe controls
        toolbar = tk.Frame(
            self.main,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        toolbar.pack(fill="x", padx=18, pady=(9, 10))

        tk.Label(
            toolbar,
            text="CHART TIMEFRAME",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(12, 8), pady=10)

        self.timeframe_var = tk.StringVar(value="M5")

        self.timeframe_selector = ttk.Combobox(
            toolbar,
            textvariable=self.timeframe_var,
            values=list(TIMEFRAMES.keys()),
            state="readonly",
            width=7,
            style="MM.TCombobox",
        )

        self.timeframe_selector.pack(
            side="left", padx=4, pady=7
        )

        self.timeframe_selector.bind(
            "<<ComboboxSelected>>",
            self.on_timeframe_changed,
        )

        self.refresh_button = tk.Button(
            toolbar,
            text="REFRESH NOW",
            command=self.refresh_all,
            bg="#18384C",
            fg=CYAN,
            activebackground="#24516A",
            activeforeground="white",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=14,
            pady=7,
        )
        self.refresh_button.pack(
            side="left", padx=8, pady=7
        )

        self.data_status = tk.StringVar(
            value="Waiting for first data refresh..."
        )

        tk.Label(
            toolbar,
            textvariable=self.data_status,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=12)

        # Main content
        content = tk.Frame(self.main, bg=BG)
        content.pack(
            fill="both",
            expand=True,
            padx=18,
            pady=(0, 12),
        )

        content.grid_columnconfigure(0, weight=7, uniform="main")
        content.grid_columnconfigure(1, weight=4, uniform="main")
        content.grid_rowconfigure(0, weight=5, uniform="rows")
        content.grid_rowconfigure(1, weight=3, uniform="rows")

        # Chart area
        chart_panel, chart_body = self.make_panel(
            content,
            "Completed-Candle Chart",
            "XAUUSD",
            padding=10,
        )

        chart_panel.grid(
            row=0, column=0, sticky="nsew",
            padx=(0, 7), pady=(0, 7)
        )

        self.figure = Figure(
            figsize=(9, 5),
            dpi=100,
            facecolor=PANEL,
            constrained_layout=True,
        )

        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(PANEL)

        self.canvas = FigureCanvasTkAgg(
            self.figure, master=chart_body
        )

        self.canvas.get_tk_widget().pack(
            fill="both", expand=True
        )

        # Right-side analysis panels
        right = tk.Frame(content, bg=BG)
        right.grid(
            row=0, column=1, sticky="nsew",
            padx=(7, 0), pady=(0, 7)
        )

        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1, uniform="right")
        right.grid_rowconfigure(1, weight=1, uniform="right")

        structure_panel, structure_body = self.make_panel(
            right, "Market Structure"
        )
        structure_panel.grid(
            row=0, column=0, sticky="nsew", pady=(0, 6)
        )

        self.structure_bias_var = tk.StringVar(value="--")
        self.structure_event_var = tk.StringVar(value="--")
        self.swing_high_var = tk.StringVar(value="--")
        self.swing_low_var = tk.StringVar(value="--")

        self.make_value_row(
            structure_body, "Directional bias",
            self.structure_bias_var
        )
        self.make_value_row(
            structure_body, "Structure",
            self.structure_event_var
        )
        self.make_value_row(
            structure_body, "Latest swing high",
            self.swing_high_var
        )
        self.make_value_row(
            structure_body, "Latest swing low",
            self.swing_low_var
        )

        liquidity_panel, liquidity_body = self.make_panel(
            right, "Liquidity Analysis"
        )
        liquidity_panel.grid(
            row=1, column=0, sticky="nsew", pady=(6, 0)
        )

        self.bsl_var = tk.StringVar(value="--")
        self.ssl_var = tk.StringVar(value="--")
        self.sweep_var = tk.StringVar(value="No sweep confirmed")

        self.make_value_row(
            liquidity_body, "Buy-side levels",
            self.bsl_var
        )
        self.make_value_row(
            liquidity_body, "Sell-side levels",
            self.ssl_var
        )
        self.make_value_row(
            liquidity_body, "Sweep status",
            self.sweep_var
        )

        # Bottom: trade analysis and FVG information
        signal_panel, signal_body = self.make_panel(
            content, "Trade Setup & Risk"
        )
        signal_panel.grid(
            row=1, column=0, sticky="nsew",
            padx=(0, 7), pady=(7, 0)
        )

        self.signal_var = tk.StringVar(value="WAITING FOR ANALYSIS")
        self.entry_var = tk.StringVar(value="--")
        self.sl_var = tk.StringVar(value="--")
        self.rr_var = tk.StringVar(value="--")
        self.confidence_var = tk.StringVar(value="--")

        signal_top = tk.Frame(signal_body, bg=PANEL)
        signal_top.pack(fill="x", pady=(0, 7))

        self.signal_label = tk.Label(
            signal_top,
            textvariable=self.signal_var,
            bg=PANEL,
            fg=GOLD,
            font=("Segoe UI", 17, "bold"),
            anchor="w",
        )
        self.signal_label.pack(side="left")

        tk.Label(
            signal_top,
            text="RULE-BASED ANALYSIS • NOT FINANCIAL ADVICE",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        trade_metrics = tk.Frame(signal_body, bg=PANEL)
        trade_metrics.pack(fill="x")

        for i in range(4):
            trade_metrics.grid_columnconfigure(
                i, weight=1, uniform="trade"
            )

        trade_fields = [
            ("ENTRY", self.entry_var),
            ("STOP LOSS", self.sl_var),
            ("TP1 R:R", self.rr_var),
            ("RULE SCORE", self.confidence_var),
        ]

        for i, (title, variable) in enumerate(trade_fields):
            cell = tk.Frame(
                trade_metrics,
                bg=PANEL_ALT,
                highlightbackground=BORDER,
                highlightthickness=1,
            )
            cell.grid(
                row=0, column=i, sticky="nsew",
                padx=3, pady=3
            )

            tk.Label(
                cell,
                text=title,
                bg=PANEL_ALT,
                fg=MUTED,
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w", padx=8, pady=(7, 3))

            tk.Label(
                cell,
                textvariable=variable,
                bg=PANEL_ALT,
                fg=TEXT,
                font=("Segoe UI", 12, "bold"),
            ).pack(anchor="w", padx=8, pady=(0, 7))

        self.target_summary = tk.StringVar(
            value="TP targets will appear when analysis is available."
        )

        tk.Label(
            signal_body,
            textvariable=self.target_summary,
            bg=PANEL,
            fg=GREEN,
            font=("Consolas", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=750,
        ).pack(fill="x", padx=3, pady=(8, 3))

        self.signal_reason = tk.StringVar(
            value="Waiting for completed-candle analysis."
        )

        tk.Label(
            signal_body,
            textvariable=self.signal_reason,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=750,
        ).pack(fill="x", padx=3, pady=(3, 5))

        # Bottom-right FVG panel
        fvg_panel, fvg_body = self.make_panel(
            content, "Fair Value Gaps"
        )

        fvg_panel.grid(
            row=1, column=1, sticky="nsew",
            padx=(7, 0), pady=(7, 0)
        )

        self.fvg_summary = tk.StringVar(
            value="Waiting for FVG analysis..."
        )

        tk.Label(
            fvg_body,
            textvariable=self.fvg_summary,
            bg=PANEL,
            fg=TEXT,
            font=("Consolas", 9),
            justify="left",
            anchor="nw",
            wraplength=380,
        ).pack(fill="both", expand=True)

        # Footer
        self.footer_status = tk.StringVar(
            value="Market Mind V2 | Analysis only"
        )

        footer = tk.Label(
            self.main,
            textvariable=self.footer_status,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        )
        footer.pack(fill="x", padx=20, pady=(0, 7))

    # -------------------- TIMEFRAME HANDLER ------------------

    def on_timeframe_changed(self, event=None):
        selected = self.timeframe_var.get()

        if selected not in TIMEFRAMES:
            return

        self.current_timeframe = selected

        self.data_status.set(
            f"Loading {selected} completed candles..."
        )

        # Recalculate the selected timeframe from MT5.
        self.refresh_market_data()

    # ========================================================
    # END OF PART 2
    # Paste PART 3 directly below this line.
    # Do not run the file yet.
    # ========================================================
    # ============================================================
# PART 3 OF 4 — CHART, REFRESH ENGINE AND ANALYSIS DISPLAY
# Paste directly below Part 2.
# ============================================================

    # -------------------- REFRESH ALL ------------------------

    def refresh_all(self):
        """Refresh the quote and completed-candle analysis."""

        self.refresh_quote()
        self.refresh_market_data()

    # -------------------- LIVE QUOTE REFRESH -----------------

    def refresh_quote(self):
        """Update live price cards without blocking the GUI."""

        try:
            quote = get_live_quote()
            self.quote = quote

            if quote is None:
                self.connection_status.set(
                    "MT5 DISCONNECTED / QUOTE UNAVAILABLE"
                )
                self.bid_var.set("--")
                self.ask_var.set("--")
                self.spread_var.set("--")
                self.age_var.set("--")

            else:
                self.connection_status.set("● MT5 CONNECTED")

                self.bid_var.set(
                    f"{quote['bid']:.2f}"
                )
                self.ask_var.set(
                    f"{quote['ask']:.2f}"
                )
                self.spread_var.set(
                    f"{quote['spread']:.2f}"
                )
                self.age_var.set(
                    f"{quote['age']:.1f}s"
                )

                if quote["age"] > 10:
                    self.connection_status.set(
                        "⚠ MT5 CONNECTED / STALE QUOTE"
                    )

            now_utc = datetime.now(timezone.utc)
            self.clock_status.set(
                now_utc.strftime("UTC %Y-%m-%d %H:%M:%S")
            )

        except Exception as exc:
            self.connection_status.set("QUOTE ERROR")
            self.last_error = str(exc)

        finally:
            if self.root.winfo_exists():
                self.root.after(
                    POLL_INTERVAL_MS, self.refresh_quote
                )

    # -------------------- MARKET DATA REFRESH ----------------

    def refresh_market_data(self):
        """
        Refresh completed candles and recalculate all
        analytical panels for the selected timeframe.
        """

        if self.is_refreshing:
            return

        self.is_refreshing = True
        timeframe = self.current_timeframe

        try:
            self.data_status.set(
                f"Loading {timeframe} data..."
            )

            df = get_market_data(timeframe)
            df = enrich_market_data(df)

            swing_highs, swing_lows = find_swings(df)

            fvgs = find_fvgs(df)

            liquidity = detect_liquidity(
                df, swing_highs, swing_lows
            )

            structure = analyze_structure(
                df, swing_highs, swing_lows
            )

            analysis = build_trade_analysis(
                df,
                swing_highs,
                swing_lows,
                fvgs,
                liquidity,
                structure,
            )

            # Save the completed calculation results.
            self.market_data = df
            self.swing_highs = swing_highs
            self.swing_lows = swing_lows
            self.fvgs = fvgs
            self.liquidity = liquidity
            self.structure = structure
            self.analysis = analysis
            self.last_data_update = datetime.now(timezone.utc)
            self.last_error = None

            self.update_metric_cards()
            self.update_structure_panel()
            self.update_liquidity_panel()
            self.update_trade_panel()
            self.update_fvg_panel()
            self.draw_chart()

            latest = df.iloc[-1]["time"]

            self.data_status.set(
                f"{timeframe} | {len(df)} completed candles"
            )

            self.footer_status.set(
                f"Last completed candle: "
                f"{latest.strftime('%Y-%m-%d %H:%M UTC')}  |  "
                f"Updated: "
                f"{self.last_data_update.strftime('%H:%M:%S UTC')}  |  "
                f"Analysis only — no orders submitted"
            )

        except Exception as exc:
            self.last_error = str(exc)

            self.data_status.set(
                f"DATA ERROR: {str(exc)[:100]}"
            )

            self.footer_status.set(
                "Unable to refresh market analysis. "
                "Check MT5, symbol availability and console output."
            )

            print(
                f"[Market Mind] {timeframe} refresh failed: {exc}"
            )
            traceback.print_exc()

        finally:
            self.is_refreshing = False

            if self.root.winfo_exists():
                self.root.after(
                    REFRESH_INTERVAL_MS,
                    self.refresh_market_data
                )

    # -------------------- METRIC CARDS -----------------------

    def update_metric_cards(self):
        if self.market_data is None or self.market_data.empty:
            return

        last = self.market_data.iloc[-1]

        self.close_var.set(f"{last['close']:.2f}")

        bias = self.structure.get("bias", "NEUTRAL")
        self.bias_var.set(bias)

    # -------------------- STRUCTURE PANEL ---------------------

    def update_structure_panel(self):
        structure = self.structure

        self.structure_bias_var.set(
            structure.get("bias", "NEUTRAL")
        )

        self.structure_event_var.set(
            structure.get("structure", "UNCONFIRMED")
        )

        high = structure.get("last_swing_high")
        low = structure.get("last_swing_low")

        self.swing_high_var.set(
            f"{high:.2f}" if high is not None else "--"
        )

        self.swing_low_var.set(
            f"{low:.2f}" if low is not None else "--"
        )

    # -------------------- LIQUIDITY PANEL ---------------------

    def update_liquidity_panel(self):
        liquidity = self.liquidity

        bsl = liquidity.get("buy_side_levels", [])
        ssl = liquidity.get("sell_side_levels", [])

        self.bsl_var.set(
            ", ".join(f"{p:.2f}" for p in bsl[:3])
            if bsl else "No confirmed levels"
        )

        self.ssl_var.set(
            ", ".join(f"{p:.2f}" for p in ssl[:3])
            if ssl else "No confirmed levels"
        )

        if liquidity.get("swept_high"):
            status = "BUY-SIDE SWEEP CANDIDATE"
        elif liquidity.get("swept_low"):
            status = "SELL-SIDE SWEEP CANDIDATE"
        else:
            status = "NO RECENT SWEEP"

        self.sweep_var.set(status)

    # -------------------- TRADE SETUP PANEL -------------------

    def update_trade_panel(self):
        analysis = self.analysis

        signal = analysis.get("signal", "NO TRADE")

        self.signal_var.set(signal)

        if signal == "BUY":
            self.signal_label.configure(fg=GREEN)
        elif signal == "SELL":
            self.signal_label.configure(fg=RED)
        else:
            self.signal_label.configure(fg=GOLD)

        entry = analysis.get("entry")
        sl = analysis.get("sl")
        targets = analysis.get("targets", [])
        rr_values = analysis.get("rr", [])

        self.entry_var.set(
            f"{entry:.2f}" if entry is not None else "--"
        )

        self.sl_var.set(
            f"{sl:.2f}" if sl is not None else "--"
        )

        self.rr_var.set(
            f"1:{rr_values[0]:.2f}"
            if rr_values else "--"
        )

        self.confidence_var.set(
            f"{analysis.get('confidence', 0)}/100"
            if signal != "NO TRADE" else "N/A"
        )

        if targets and entry is not None:
            lines = []

            for i, target in enumerate(targets, start=1):
                rr = (
                    rr_values[i - 1]
                    if i - 1 < len(rr_values) else 0
                )

                lines.append(
                    f"TP{i}: {target:.2f} (R:R 1:{rr:.2f})"
                )

            self.target_summary.set("   |   ".join(lines))
        else:
            self.target_summary.set(
                "No justified target set available."
            )

        reasons = analysis.get("reasons", [])
        warnings = analysis.get("warnings", [])

        explanation = " | ".join(reasons[:4])

        if warnings:
            explanation += " | " + " ".join(warnings[:2])

        self.signal_reason.set(
            explanation or "Waiting for more evidence."
        )

    # -------------------- FVG PANEL ---------------------------

    def update_fvg_panel(self):
        if self.market_data is None:
            self.fvg_summary.set("No market data available.")
            return

        if not self.fvgs:
            self.fvg_summary.set(
                "No fair value gaps detected in the fetched candles."
            )
            return

        recent_gaps = self.fvgs[-6:][::-1]
        lines = []

        for gap in recent_gaps:
            lines.append(
                f"{gap['type']}  "
                f"{gap['lower']:.2f} - {gap['upper']:.2f}\n"
                f"Status: {gap['status']} | "
                f"Body/range: {gap['body_ratio']:.0%}"
            )

        self.fvg_summary.set("\n\n".join(lines))

    # -------------------- CANDLESTICK CHART -------------------

    def draw_chart(self):
        """
        Draw completed candles, swing levels, liquidity,
        FVG zones, entry, SL and take-profit levels.
        """

        if self.market_data is None:
            return

        df = self.market_data.tail(CHART_BARS).reset_index(
            drop=True
        )

        if df.empty:
            return

        self.ax.clear()
        self.ax.set_facecolor(PANEL)

        # Chart styling
        self.ax.tick_params(
            axis="x", colors=MUTED, labelsize=8
        )
        self.ax.tick_params(
            axis="y", colors=MUTED, labelsize=9
        )

        self.ax.grid(
            True,
            color="#26364B",
            linewidth=0.6,
            alpha=0.8,
        )

        for spine in self.ax.spines.values():
            spine.set_color(BORDER)

        # Draw candlesticks
        for i, candle in df.iterrows():
            opening = float(candle["open"])
            closing = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])

            bullish = closing >= opening
            candle_color = GREEN if bullish else RED

            self.ax.vlines(
                i,
                low,
                high,
                color=candle_color,
                linewidth=1.0,
                zorder=3,
            )

            body_bottom = min(opening, closing)
            body_height = abs(closing - opening)

            if body_height == 0:
                body_height = max(
                    (high - low) * 0.025, 0.0001
                )

            candle_body = Rectangle(
                (i - 0.32, body_bottom),
                0.64,
                body_height,
                facecolor=candle_color,
                edgecolor=candle_color,
                linewidth=0.6,
                zorder=4,
            )

            self.ax.add_patch(candle_body)

        # Swing highs and lows within the visible range
        start_index = len(self.market_data) - len(df)

        for swing in self.swing_highs:
            local_index = swing["index"] - start_index

            if 0 <= local_index < len(df):
                self.ax.scatter(
                    local_index,
                    swing["price"],
                    marker="v",
                    color=RED,
                    s=25,
                    zorder=6,
                )

        for swing in self.swing_lows:
            local_index = swing["index"] - start_index

            if 0 <= local_index < len(df):
                self.ax.scatter(
                    local_index,
                    swing["price"],
                    marker="^",
                    color=GREEN,
                    s=25,
                    zorder=6,
                )

        # Fair value gap overlays
        for gap in self.fvgs:
            local_index = (
                gap["formed_index"] - start_index
            )

            if not (0 <= local_index < len(df)):
                continue

            zone_color = (
                GREEN if gap["type"] == "BULLISH"
                else RED
            )

            self.ax.axhspan(
                gap["lower"],
                gap["upper"],
                xmin=max(0, local_index / max(len(df), 1)),
                xmax=1,
                color=zone_color,
                alpha=0.09,
                zorder=1,
            )

        # Current completed candle close
        last_close = float(df.iloc[-1]["close"])

        self.ax.axhline(
            last_close,
            color=CYAN,
            linestyle="--",
            linewidth=1.0,
            alpha=0.9,
            label=f"Last close {last_close:.2f}",
        )

        # Trade plan levels
        analysis = self.analysis or {}

        entry = analysis.get("entry")
        sl = analysis.get("sl")
        targets = analysis.get("targets", [])

        if entry is not None:
            self.ax.axhline(
                entry,
                color=GOLD,
                linestyle="-.",
                linewidth=1.2,
                label=f"Entry {entry:.2f}",
            )

        if sl is not None:
            self.ax.axhline(
                sl,
                color=RED,
                linestyle="--",
                linewidth=1.2,
                label=f"SL {sl:.2f}",
            )

        for i, target in enumerate(targets, start=1):
            self.ax.axhline(
                target,
                color=GREEN,
                linestyle=":",
                linewidth=1.0,
                label=f"TP{i} {target:.2f}",
            )

        # Time labels
        count = len(df)
        tick_positions = np.linspace(
            0, count - 1, min(7, count), dtype=int
        )

        tick_labels = [
            df.iloc[i]["time"].strftime("%m-%d\n%H:%M")
            for i in tick_positions
        ]

        self.ax.set_xticks(tick_positions)
        self.ax.set_xticklabels(
            tick_labels, rotation=0, ha="center"
        )

        self.ax.set_ylabel(
            "Price", color=MUTED, fontsize=9
        )

        self.ax.set_title(
            f"XAUUSD  |  {self.current_timeframe}  |  "
            f"COMPLETED CANDLES",
            color=TEXT,
            fontsize=11,
            fontweight="bold",
            loc="left",
            pad=10,
        )

        self.ax.set_xlim(-1, count)

        # Add a little vertical breathing room.
        price_min = float(df["low"].min())
        price_max = float(df["high"].max())
        price_range = price_max - price_min

        if price_range > 0:
            margin = price_range * 0.08
            self.ax.set_ylim(
                price_min - margin,
                price_max + margin,
            )

        self.ax.margins(x=0.01)

        # Keep the legend compact.
        handles, labels = self.ax.get_legend_handles_labels()

        if handles:
            self.ax.legend(
                handles,
                labels,
                loc="upper left",
                fontsize=7,
                facecolor=PANEL_ALT,
                edgecolor=BORDER,
                labelcolor=TEXT,
                ncol=2,
            )

        self.canvas.draw_idle()


# ============================================================
# END OF PART 3
# Paste PART 4 below this line.
# Do not run the file yet.
# ============================================================
# ============================================================
# PART 4 OF 4 — APPLICATION STARTUP AND SHUTDOWN
# ============================================================

def main():
    print("=" * 65)
    print("MARKET MIND V2 — XAUUSD MARKET INTELLIGENCE")
    print("=" * 65)

    print(f"Python executable : {sys.executable}")
    print(f"MT5 terminal      : {MT5_PATH}")
    print(f"Symbol            : {SYMBOL}")
    print("Analysis candles  : Completed candles only")
    print("Trading mode      : Analysis only — no orders submitted")
    print("-" * 65)

    connected, message = connect_mt5()

    if connected:
        print(f"MT5 STATUS: {message}")

        account = mt5.account_info()
        terminal = mt5.terminal_info()

        if account is not None:
            print(f"Account login     : {account.login}")
            print(f"Broker server     : {account.server}")

        if terminal is not None:
            print(f"Terminal connected: {terminal.connected}")

        symbol_info = mt5.symbol_info(SYMBOL)

        if symbol_info is None:
            print(f"WARNING: {SYMBOL} is unavailable.")
        else:
            print(f"Symbol digits     : {symbol_info.digits}")
            print(f"Point size        : {symbol_info.point}")

        quote = get_live_quote()

        if quote is not None:
            print(f"Bid               : {quote['bid']}")
            print(f"Ask               : {quote['ask']}")
            print(f"Spread            : {quote['spread']}")
        else:
            print("WARNING: No valid live quote received.")

    else:
        print(f"MT5 STATUS: {message}")
        print(
            "The dashboard will open, but market data "
            "requires a valid MT5 connection."
        )

    print("-" * 65)
    print("Opening Market Mind V2 dashboard...")
    print("=" * 65)

    root = tk.Tk()

    try:
        app = MarketMindDashboard(root)

        def on_close():
            print("\nClosing Market Mind V2...")

            try:
                root.after_cancel
            except Exception:
                pass

            try:
                mt5.shutdown()
            except Exception as exc:
                print(f"MT5 shutdown warning: {exc}")

            root.destroy()
            print("Market Mind V2 closed.")

        root.protocol("WM_DELETE_WINDOW", on_close)

        root.mainloop()

    except KeyboardInterrupt:
        print("Dashboard interrupted by user.")

    except Exception as exc:
        print(f"Dashboard startup error: {exc}")
        traceback.print_exc()

        try:
            messagebox.showerror(
                "Market Mind V2",
                f"Dashboard error:\n{exc}"
            )
        except Exception:
            pass

    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()


# ============================================================
# END OF MARKET MIND V2 — PART 4 OF 4
# ============================================================
