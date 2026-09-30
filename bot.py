import os
import sys
import json
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import requests


# ============================================================
# XAU/USD SIGNAL BOT
# M5 + M15
# Telegram Alerts
# Liquidity Sweep + CHOCH
# Range Breakout
# S/R Rejection
# Double / Triple Tops & Bottoms
# Head & Shoulders
# Triangles
# Flags / Pennants
# Wedges
# Rectangle Breakout
# Early Exit
# ONE OPEN XAU/USD TRADE ONLY
# NO H1/H4 DIRECTION FILTER
# ============================================================


STATE_FILE = "state.json"
SYMBOL = "XAU/USD"

# XAU price unit used internally
PIP = 0.1


# ============================================================
# SETTINGS
# ============================================================

def env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except Exception:
        return default


def env_float(name, default):
    try:
        return float(env(name, str(default)))
    except Exception:
        return default


TD_KEY = env("TWELVE_DATA_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")

# أقل تقييم مسموح له بإرسال إشارة
MIN_SCORE = env_int("MIN_SCORE", 5)

# الحد الأقصى للصفقات اليومية
MAX_PER_DAY = env_int("MAX_PER_DAY", 5)

# بعد هذا العدد من الخسائر لا توجد إشارات جديدة
MAX_LOSSES = env_int("MAX_LOSSES", 3)

# أقصى مدة للصفقة قبل اعتبارها منتهية
EXPIRE_HOURS = env_float("EXPIRE_HOURS", 4)

# اختبار Telegram
TEST_MSG = env("TEST_MSG", "0") == "1"

# رسالة تشغيل مرة واحدة يومياً
STARTUP_MSG = env("STARTUP_MSG", "1") == "1"

# هامش ستوب Sweep بعد الذيل
SWEEP_SL_PRICE = env_float("SWEEP_SL_PRICE", 1.0)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_call(method, payload=None):

    if not TG_TOKEN:
        print("TELEGRAM ERROR: TELEGRAM_BOT_TOKEN is missing")
        return None

    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            json=payload or {},
            timeout=15
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "ok": False,
                "description": response.text[:300]
            }

        if not response.ok or not data.get("ok"):
            print(
                f"TELEGRAM {method} FAILED "
                f"[{response.status_code}]: {data}"
            )
            return data

        return data

    except Exception as e:
        print(f"TELEGRAM {method} EXCEPTION: {e}")
        return None


def telegram_check():

    data = telegram_call("getMe")

    return bool(
        data and
        data.get("ok")
    )


def tg(text):

    print(text)

    if not TG_TOKEN or not TG_CHAT:
        print(
            "TELEGRAM ERROR: "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
        )
        return False

    data = telegram_call(
        "sendMessage",
        {
            "chat_id": TG_CHAT,
            "text": text,
            "disable_web_page_preview": True
        }
    )

    return bool(
        data and
        data.get("ok")
    )


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "open": {},
        "days": {},
        "last_signals": {},
        "last_startup": ""
    }


def load_state():

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        default = default_state()

        if isinstance(state, dict):
            default.update(state)

        return default

    except Exception:

        return default_state()


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


def day_key():

    return datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")


def day_stats(state):

    day = state["days"].setdefault(
        day_key(),
        {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "neutral": 0
        }
    )

    return day


# ============================================================
# DATA
# ============================================================

def fetch(
    symbol=SYMBOL,
    interval="5min",
    outputsize=900
):

    if not TD_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is missing"
        )

    response = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TD_KEY,
            "format": "JSON",
            "timezone": "UTC"
        },
        timeout=20
    )

    data = response.json()

    if (
        response.status_code != 200
        or "values" not in data
    ):

        raise RuntimeError(
            f"Twelve Data: "
            f"{data.get('message', data)}"
        )

    df = pd.DataFrame(
        data["values"]
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:

        if column not in df.columns:
            df[column] = 0.0

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        utc=True
    ).dt.tz_convert(None)

    df = (
        df
        .sort_values("datetime")
        .drop_duplicates("datetime")
        .set_index("datetime")
    )

    return df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )


def resample(df, rule):

    result = (
        df[
            [
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        ]
        .resample(
            rule,
            label="left",
            closed="left"
        )
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }
        )
        .dropna()
    )

    return result


def clean_completed(df, minutes):

    if df.empty:
        return df

    cutoff = (
        pd.Timestamp.now(tz="UTC")
        .tz_convert(None)
        .floor(f"{minutes}min")
    )

    return df[
        df.index < cutoff
    ].copy()


# ============================================================
# INDICATORS
# ============================================================

def indicators(df):

    d = df.copy()

    d["ema20"] = (
        d.close
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    d["ema50"] = (
        d.close
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    delta = d.close.diff()

    gain = (
        delta
        .clip(lower=0)
        .rolling(14)
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .rolling(14)
        .mean()
    )

    rs = gain / loss.replace(
        0,
        np.nan
    )

    d["rsi"] = 100 - (
        100 / (1 + rs)
    )

    true_range = pd.concat(
        [
            d.high - d.low,
            (
                d.high -
                d.close.shift()
            ).abs(),
            (
                d.low -
                d.close.shift()
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    d["atr"] = (
        true_range
        .rolling(14)
        .mean()
    )

    d["body"] = (
        d.close -
        d.open
    ).abs()

    d["range"] = (
        d.high -
        d.low
    ).replace(
        0,
        np.nan
    )

    d["body_pct"] = (
        d.body /
        d.range
    )

    d["upper_wick"] = (
        d.high -
        d[["open", "close"]].max(axis=1)
    )

    d["lower_wick"] = (
        d[["open", "close"]].min(axis=1) -
        d.low
    )

    d["vol_ma"] = (
        d.volume
        .replace(0, np.nan)
        .rolling(20)
        .mean()
    )

    d["vol_ratio"] = (
        d.volume /
        d.vol_ma
    )

    return d


def next_open(
    df,
    completed_index
):

    position = df.index.get_indexer(
        [completed_index]
    )[0]

    if (
        position >= 0
        and position + 1 < len(df)
    ):

        return float(
            df.iloc[position + 1].open
        )

    return float(
        df.loc[
            completed_index,
            "close"
        ]
    )


# ============================================================
# HELPERS
# ============================================================

def fmt(value):

    return f"{float(value):.2f}"


def candle_strength(
    row,
    direction
):

    if row.range <= 0:
        return 0

    if direction == "BUY":

        return int(
            row.close > row.open
            and row.body_pct >= 0.55
            and row.close >= (
                row.low +
                0.70 * row.range
            )
        )

    return int(
        row.close < row.open
        and row.body_pct >= 0.55
        and row.close <= (
            row.low +
            0.30 * row.range
        )
    )


def nearest_level(
    levels,
    price,
    direction
):

    values = sorted(
        set(
            float(x)
            for x in levels
            if np.isfinite(x)
        )
    )

    if direction == "BUY":

        above = [
            x
            for x in values
            if x > price
        ]

        if above:
            return min(
                above,
                key=lambda x: x - price
            )

    else:

        below = [
            x
            for x in values
            if x < price
        ]

        if below:
            return max(
                below,
                key=lambda x: price - x
            )

    return None


def local_pivots(
    df,
    n=2
):

    highs = []
    lows = []

    if len(df) < (
        2 * n + 1
    ):
        return highs, lows

    for i in range(
        n,
        len(df) - n
    ):

        high = float(
            df.high.iloc[i]
        )

        low = float(
            df.low.iloc[i]
        )

        if high >= float(
            df.high.iloc[
                i - n:i + n + 1
            ].max()
        ):

            highs.append(
                (
                    df.index[i],
                    high
                )
            )

        if low <= float(
            df.low.iloc[
                i - n:i + n + 1
            ].min()
        ):

            lows.append(
                (
                    df.index[i],
                    low
                )
            )

    return highs, lows


def risk_ok(
    entry,
    sl,
    tp,
    atr
):

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if (
        risk <= 0
        or not np.isfinite(atr)
        or atr <= 0
    ):
        return False

    if risk < 0.25 * atr:
        return False

    if risk > 2.5 * atr:
        return False

    if reward / risk < 1.45:
        return False

    return True


def finalize_setup(
    setup,
    df
):

    entry = float(
        setup["entry"]
    )

    sl = float(
        setup["sl"]
    )

    tp = float(
        setup["tp"]
    )

    atr = float(
        setup.get(
            "atr",
            df.atr.iloc[-1]
        )
    )

    direction = setup["direction"]

    if direction == "BUY":

        if not (
            sl < entry < tp
        ):
            return None

    else:

        if not (
            tp < entry < sl
        ):
            return None

    if not risk_ok(
        entry,
        sl,
        tp,
        atr
    ):
        return None

    rr = (
        abs(tp - entry) /
        abs(entry - sl)
    )

    row = df.iloc[-1]

    score = int(
        setup.get("base", 5)
    )

    score += candle_strength(
        row,
        direction
    )

    score += int(
        rr >= 2.0
    )

    if np.isfinite(
        row.vol_ratio
    ):

        score += int(
            row.vol_ratio >= 1.15
        )

    score += int(
        abs(
            entry -
            float(row.ema20)
        ) <= 0.55 * atr
    )

    score = max(
        1,
        min(10, score)
    )

    if score < MIN_SCORE:
        return None

    setup.update(
        {
            "score": score,
            "rr": rr,
            "atr": atr,
            "signal_time": str(
                df.index[-1]
            )
        }
    )

    return setup


# ============================================================
# 1 — LIQUIDITY SWEEP + CHOCH
# ============================================================

def sweep_choch(df):

    if len(df) < 120:
        return []

    result = []

    end = len(df) - 1

    for sweep_index in range(
        max(35, end - 18),
        end + 1
    ):

        timestamp = df.index[
            sweep_index
        ]

        hour_start = timestamp.floor("h")

        previous_hour = df[
            (
                df.index >=
                hour_start -
                pd.Timedelta(hours=1)
            )
            &
            (
                df.index <
                hour_start
            )
        ]

        if len(previous_hour) < 6:
            continue

        previous_high = float(
            previous_hour.high.max()
        )

        previous_low = float(
            previous_hour.low.min()
        )

        candle = df.iloc[
            sweep_index
        ]

        atr = float(
            df.atr.iloc[
                sweep_index
            ]
        )

        if not np.isfinite(atr):
            continue

        # HIGH SWEEP
        swept_high = (
            float(candle.high)
            >
            previous_high +
            0.10 * atr
            and
            float(candle.close)
            <
            previous_high
            and
            float(candle.upper_wick)
            >=
            max(
                float(candle.lower_wick),
                float(candle.body) * 0.8
            )
        )

        # LOW SWEEP
        swept_low = (
            float(candle.low)
            <
            previous_low -
            0.10 * atr
            and
            float(candle.close)
            >
            previous_low
            and
            float(candle.lower_wick)
            >=
            max(
                float(candle.upper_wick),
                float(candle.body) * 0.8
            )
        )

        if not (
            swept_high
            or
            swept_low
        ):
            continue

        before_sweep = df.iloc[
            :sweep_index
        ]

        highs, lows = local_pivots(
            before_sweep,
            2
        )

        # HIGH SWEEP -> SELL
        if swept_high and lows:

            minor_low = lows[-1][1]

            for j in range(
                sweep_index + 1,
                min(
                    sweep_index + 6,
                    end + 1
                )
            ):

                current = df.iloc[j]

                if (
                    float(current.close)
                    <
                    minor_low
                ):

                    entry = next_open(
                        df,
                        df.index[j]
                    )

                    sl = (
                        float(candle.high)
                        +
                        SWEEP_SL_PRICE
                    )

                    target = nearest_level(
                        [
                            x
                            for _, x in lows
                        ],
                        entry,
                        "SELL"
                    )

                    if target is None:
                        target = (
                            entry -
                            2 *
                            abs(
                                sl -
                                entry
                            )
                        )

                    setup = {
                        "strategy":
                            "Liquidity Sweep + CHOCH",

                        "direction":
                            "SELL",

                        "entry":
                            entry,

                        "sl":
                            sl,

                        "tp":
                            target,

                        "base":
                            6,

                        "atr":
                            float(
                                df.atr.iloc[j]
                            ),

                        "reason":
                            (
                                "Previous-hour high "
                                f"swept at {fmt(previous_high)}, "
                                "then CHOCH below "
                                f"{fmt(minor_low)}"
                            )
                    }

                    setup = finalize_setup(
                        setup,
                        df.iloc[
                            :j + 1
                        ]
                    )

                    if setup:
                        result.append(
                            setup
                        )

                    break

        # LOW SWEEP -> BUY
        if swept_low and highs:

            minor_high = highs[-1][1]

            for j in range(
                sweep_index + 1,
                min(
                    sweep_index + 6,
                    end + 1
                )
            ):

                current = df.iloc[j]

                if (
                    float(current.close)
                    >
                    minor_high
                ):

                    entry = next_open(
                        df,
                        df.index[j]
                    )

                    sl = (
                        float(candle.low)
                        -
                        SWEEP_SL_PRICE
                    )

                    target = nearest_level(
                        [
                            x
                            for _, x in highs
                        ],
                        entry,
                        "BUY"
                    )

                    if target is None:
                        target = (
                            entry +
                            2 *
                            abs(
                                entry -
                                sl
                            )
                        )

                    setup = {
                        "strategy":
                            "Liquidity Sweep + CHOCH",

                        "direction":
                            "BUY",

                        "entry":
                            entry,

                        "sl":
                            sl,

                        "tp":
                            target,

                        "base":
                            6,

                        "atr":
                            float(
                                df.atr.iloc[j]
                            ),

                        "reason":
                            (
                                "Previous-hour low "
                                f"swept at {fmt(previous_low)}, "
                                "then CHOCH above "
                                f"{fmt(minor_high)}"
                            )
                    }

                    setup = finalize_setup(
                        setup,
                        df.iloc[
                            :j + 1
                        ]
                    )

                    if setup:
                        result.append(
                            setup
                        )

                    break

    return result


# ============================================================
# 2 — RANGE BREAKOUT
# ============================================================

def range_breakout(df):

    if len(df) < 80:
        return []

    result = []

    end = len(df) - 1

    for i in range(
        max(24, end - 10),
        end + 1
    ):

        window = df.iloc[
            i - 24:i
        ]

        if len(window) < 12:
            continue

        range_high = float(
            window.high.max()
        )

        range_low = float(
            window.low.min()
        )

        width = (
            range_high -
            range_low
        )

        atr = float(
            df.atr.iloc[i]
        )

        if (
            width <= 0
            or not np.isfinite(atr)
        ):
            continue

        if (
            width < 0.8 * atr
            or
            width > 4.0 * atr
        ):
            continue

        touches_high = int(
            (
                window.high >=
                range_high -
                0.20 * atr
            ).sum()
        )

        touches_low = int(
            (
                window.low <=
                range_low +
                0.20 * atr
            ).sum()
        )

        if (
            touches_high < 2
            or
            touches_low < 2
        ):
            continue

        candle = df.iloc[i]

        entry = next_open(
            df,
            df.index[i]
        )

        # BUY BREAKOUT
        if (
            float(candle.open)
            > range_high
            and
            float(candle.close)
            > range_high
        ):

            sl = (
                range_high +
                range_low
            ) / 2

            tp = (
                entry +
                2 *
                (entry - sl)
            )

            setup = {
                "strategy":
                    "Range Breakout",

                "direction":
                    "BUY",

                "entry":
                    entry,

                "sl":
                    sl,

                "tp":
                    tp,

                "base":
                    6,

                "atr":
                    atr,

                "reason":
                    (
                        "Range consolidation "
                        "broke with full body "
                        f"above {fmt(range_high)}"
                    )
            }

            setup = finalize_setup(
                setup,
                df.iloc[:i + 1]
            )

            if setup:
                result.append(setup)

        # SELL BREAKOUT
        elif (
            float(candle.open)
            < range_low
            and
            float(candle.close)
            < range_low
        ):

            sl = (
                range_high +
                range_low
            ) / 2

            tp = (
                entry -
                2 *
                (sl - entry)
            )

            setup = {
                "strategy":
                    "Range Breakout",

                "direction":
                    "SELL",

                "entry":
                    entry,

                "sl":
                    sl,

                "tp":
                    tp,

                "base":
                    6,

                "atr":
                    atr,

                "reason":
                    (
                        "Range consolidation "
                        "broke with full body "
                        f"below {fmt(range_low)}"
                    )
            }

            setup = finalize_setup(
                setup,
                df.iloc[:i + 1]
            )

            if setup:
                result.append(setup)

    return result


# ============================================================
# 3 — SUPPORT / RESISTANCE REJECTION
# ============================================================

def sr_setups(df):

    if len(df) < 80:
        return []

    result = []

    row = df.iloc[-1]

    atr = float(
        row.atr
    )

    if not np.isfinite(atr):
        return []

    highs, lows = local_pivots(
        df.iloc[:-3],
        2
    )

    levels = (
        [x for _, x in highs[-8:]]
        +
        [x for _, x in lows[-8:]]
    )

    for level in levels:

        distance = abs(
            float(row.close) -
            level
        )

        if distance > 0.35 * atr:
            continue

        # BUY rejection
        if (
            float(row.low)
            <= level
            <= float(row.close)
            and
            float(row.close)
            >
            float(row.open)
            and
            row.lower_wick
            >= row.body
        ):

            sl = (
                float(row.low)
                -
                0.15 * atr
            )

            tp = nearest_level(
                levels,
                float(row.close),
                "BUY"
            )

            if tp is None:
                tp = (
                    float(row.close)
                    +
                    2 *
                    (
                        float(row.close)
                        -
                        sl
                    )
                )

            setup = finalize_setup(
                {
                    "strategy":
                        "S/R Rejection",

                    "direction":
                        "BUY",

                    "entry":
                        next_open(
                            df,
                            df.index[-1]
                        ),

                    "sl":
                        sl,

                    "tp":
                        tp,

                    "base":
                        5,

                    "atr":
                        atr,

                    "reason":
                        (
                            "Bullish rejection "
                            f"at support {fmt(level)}"
                        )
                },
                df
            )

            if setup:
                result.append(
                    setup
                )

        # SELL rejection
        if (
            float(row.high)
            >= level
            >= float(row.close)
            and
            float(row.close)
            <
            float(row.open)
            and
            row.upper_wick
            >= row.body
        ):

            sl = (
                float(row.high)
                +
                0.15 * atr
            )

            tp = nearest_level(
                levels,
                float(row.close),
                "SELL"
            )

            if tp is None:
                tp = (
                    float(row.close)
                    -
                    2 *
                    (
                        sl -
                        float(row.close)
                    )
                )

            setup = finalize_setup(
                {
                    "strategy":
                        "S/R Rejection",

                    "direction":
                        "SELL",

                    "entry":
                        next_open(
                            df,
                            df.index[-1]
                        ),

                    "sl":
                        sl,

                    "tp":
                        tp,

                    "base":
                        5,

                    "atr":
                        atr,

                    "reason":
                        (
                            "Bearish rejection "
                            f"at resistance {fmt(level)}"
                        )
                },
                df
            )

            if setup:
                result.append(
                    setup
                )

    return result


# ============================================================
# 4 — CLASSIC PATTERNS
# ============================================================

def pattern_setups(df):

    if len(df) < 100:
        return []

    result = []

    highs, lows = local_pivots(
        df.iloc[:-2],
        2
    )

    highs = highs[-8:]
    lows = lows[-8:]

    price = float(
        df.close.iloc[-1]
    )

    atr = float(
        df.atr.iloc[-1]
    )

    if not np.isfinite(atr):
        return []

    entry = next_open(
        df,
        df.index[-1]
    )

    def add_pattern(
        name,
        direction,
        sl,
        reason,
        base=5
    ):

        if direction == "BUY":

            tp = (
                entry +
                2 *
                (entry - sl)
            )

        else:

            tp = (
                entry -
                2 *
                (sl - entry)
            )

        setup = finalize_setup(
            {
                "strategy":
                    name,

                "direction":
                    direction,

                "entry":
                    entry,

                "sl":
                    sl,

                "tp":
                    tp,

                "base":
                    base,

                "atr":
                    atr,

                "reason":
                    reason
            },
            df
        )

        if setup:
            result.append(
                setup
            )

    # --------------------------------------------------------
    # DOUBLE TOP
    # --------------------------------------------------------

    if len(highs) >= 2:

        first = highs[-2][1]
        second = highs[-1][1]

        if abs(
            first - second
        ) <= 0.35 * atr:

            between = df[
                (
                    df.index >
                    highs[-2][0]
                )
                &
                (
                    df.index <
                    highs[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.low.min()
                )

                if price < neckline:

                    add_pattern(
                        "Double Top",
                        "SELL",
                        max(
                            first,
                            second
                        ) + 0.20 * atr,
                        (
                            "Two similar swing highs "
                            "with neckline break"
                        )
                    )

    # --------------------------------------------------------
    # DOUBLE BOTTOM
    # --------------------------------------------------------

    if len(lows) >= 2:

        first = lows[-2][1]
        second = lows[-1][1]

        if abs(
            first - second
        ) <= 0.35 * atr:

            between = df[
                (
                    df.index >
                    lows[-2][0]
                )
                &
                (
                    df.index <
                    lows[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.high.max()
                )

                if price > neckline:

                    add_pattern(
                        "Double Bottom",
                        "BUY",
                        min(
                            first,
                            second
                        ) - 0.20 * atr,
                        (
                            "Two similar swing lows "
                            "with neckline break"
                        )
                    )

    # --------------------------------------------------------
    # TRIPLE TOP
    # --------------------------------------------------------

    if len(highs) >= 3:

        values = [
            x[1]
            for x in highs[-3:]
        ]

        if (
            max(values) -
            min(values)
            <=
            0.45 * atr
        ):

            between = df[
                (
                    df.index >
                    highs[-3][0]
                )
                &
                (
                    df.index <
                    highs[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.low.min()
                )

                if price < neckline:

                    add_pattern(
                        "Triple Top",
                        "SELL",
                        max(values) +
                        0.20 * atr,
                        (
                            "Three clustered "
                            "swing highs + "
                            "neckline break"
                        )
                    )

    # --------------------------------------------------------
    # TRIPLE BOTTOM
    # --------------------------------------------------------

    if len(lows) >= 3:

        values = [
            x[1]
            for x in lows[-3:]
        ]

        if (
            max(values) -
            min(values)
            <=
            0.45 * atr
        ):

            between = df[
                (
                    df.index >
                    lows[-3][0]
                )
                &
                (
                    df.index <
                    lows[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.high.max()
                )

                if price > neckline:

                    add_pattern(
                        "Triple Bottom",
                        "BUY",
                        min(values) -
                        0.20 * atr,
                        (
                            "Three clustered "
                            "swing lows + "
                            "neckline break"
                        )
                    )

    # --------------------------------------------------------
    # HEAD & SHOULDERS
    # --------------------------------------------------------

    if (
        len(highs) >= 3
        and
        len(lows) >= 2
    ):

        h1, h2, h3 = [
            x[1]
            for x in highs[-3:]
        ]

        if (
            h2 > h1 + 0.25 * atr
            and
            h2 > h3 + 0.25 * atr
            and
            abs(h1 - h3)
            <= 0.55 * atr
        ):

            between = df[
                (
                    df.index >
                    highs[-3][0]
                )
                &
                (
                    df.index <
                    highs[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.low.min()
                )

                if price < neckline:

                    add_pattern(
                        "Head & Shoulders",
                        "SELL",
                        h2 + 0.20 * atr,
                        "H&S neckline broken"
                    )

    # --------------------------------------------------------
    # INVERSE HEAD & SHOULDERS
    # --------------------------------------------------------

    if (
        len(lows) >= 3
        and
        len(highs) >= 2
    ):

        l1, l2, l3 = [
            x[1]
            for x in lows[-3:]
        ]

        if (
            l2 < l1 - 0.25 * atr
            and
            l2 < l3 - 0.25 * atr
            and
            abs(l1 - l3)
            <= 0.55 * atr
        ):

            between = df[
                (
                    df.index >
                    lows[-3][0]
                )
                &
                (
                    df.index <
                    lows[-1][0]
                )
            ]

            if len(between):

                neckline = float(
                    between.high.max()
                )

                if price > neckline:

                    add_pattern(
                        "Inverse H&S",
                        "BUY",
                        l2 - 0.20 * atr,
                        "Inverse H&S neckline broken"
                    )

    # --------------------------------------------------------
    # TRIANGLES
    # --------------------------------------------------------

    if (
        len(highs) >= 3
        and
        len(lows) >= 3
    ):

        high_values = np.array([
            x[1]
            for x in highs[-3:]
        ])

        low_values = np.array([
            x[1]
            for x in lows[-3:]
        ])

        high_slope = np.polyfit(
            np.arange(3),
            high_values,
            1
        )[0]

        low_slope = np.polyfit(
            np.arange(3),
            low_values,
            1
        )[0]

        spread = abs(
            high_values[-1] -
            low_values[-1]
        )

        # Ascending triangle
        if (
            abs(high_slope)
            < 0.15 * atr
            and
            low_slope
            > 0.08 * atr
            and
            spread < 2.5 * atr
            and
            price >
            max(high_values)
        ):

            add_pattern(
                "Ascending Triangle",
                "BUY",
                min(low_values) -
                0.20 * atr,
                "Ascending triangle broke upward"
            )

        # Descending triangle
        if (
            abs(low_slope)
            < 0.15 * atr
            and
            high_slope
            < -0.08 * atr
            and
            spread < 2.5 * atr
            and
            price <
            min(low_values)
        ):

            add_pattern(
                "Descending Triangle",
                "SELL",
                max(high_values) +
                0.20 * atr,
                "Descending triangle broke downward"
            )

        # Rising wedge
        if (
            high_slope > 0
            and
            low_slope > 0
            and
            low_slope >
            high_slope * 1.15
            and
            price <
            min(low_values)
        ):

            add_pattern(
                "Rising Wedge",
                "SELL",
                max(high_values) +
                0.20 * atr,
                "Rising wedge broke downward"
            )

        # Falling wedge
        if (
            high_slope < 0
            and
            low_slope < 0
            and
            abs(low_slope)
            <
            abs(high_slope) * 0.87
            and
            price >
            max(high_values)
        ):

            add_pattern(
                "Falling Wedge",
                "BUY",
                min(low_values) -
                0.20 * atr,
                "Falling wedge broke upward"
            )

    # --------------------------------------------------------
    # BULL / BEAR FLAG + PENNANT
    # --------------------------------------------------------

    if len(df) >= 25:

        impulse = df.iloc[
            -25:-10
        ]

        consolidation = df.iloc[
            -10:
        ]

        impulse_move = float(
            impulse.close.iloc[-1] -
            impulse.close.iloc[0]
        )

        impulse_atr = float(
            impulse.atr.mean()
        )

        consolidation_width = float(
            consolidation.high.max() -
            consolidation.low.min()
        )

        if (
            np.isfinite(impulse_atr)
            and
            abs(impulse_move)
            >
            2.0 * impulse_atr
            and
            consolidation_width
            <
            2.0 * impulse_atr
        ):

            if (
                impulse_move > 0
                and
                price >
                float(
                    consolidation.high.max()
                )
            ):

                add_pattern(
                    "Bull Flag / Pennant",
                    "BUY",
                    float(
                        consolidation.low.min()
                    ) -
                    0.15 * atr,
                    (
                        "Strong bullish impulse "
                        "+ tight consolidation "
                        "breakout"
                    )
                )

            elif (
                impulse_move < 0
                and
                price <
                float(
                    consolidation.low.min()
                )
            ):

                add_pattern(
                    "Bear Flag / Pennant",
                    "SELL",
                    float(
                        consolidation.high.max()
                    ) +
                    0.15 * atr,
                    (
                        "Strong bearish impulse "
                        "+ tight consolidation "
                        "breakout"
                    )
                )

    # --------------------------------------------------------
    # RECTANGLE BREAKOUT
    # --------------------------------------------------------

    box = df.iloc[
        -20:-2
    ]

    if len(box) >= 12:

        box_high = float(
            box.high.max()
        )

        box_low = float(
            box.low.min()
        )

        box_width = (
            box_high -
            box_low
        )

        if (
            box_width
            <
            2.5 * atr
        ):

            if price > box_high:

                add_pattern(
                    "Rectangle Breakout",
                    "BUY",
                    box_low,
                    "Horizontal consolidation broke upward"
                )

            elif price < box_low:

                add_pattern(
                    "Rectangle Breakout",
                    "SELL",
                    box_high,
                    "Horizontal consolidation broke downward"
                )

    return result


# ============================================================
# M5 SCANNER
# ============================================================

def scan_m5(df):

    data = indicators(df)

    setups = []

    setups += sweep_choch(data)
    setups += range_breakout(data)
    setups += sr_setups(data)
    setups += pattern_setups(data)

    return [
        x
        for x in setups
        if x
        and x.get("score", 0)
        >= MIN_SCORE
    ]


# ============================================================
# M15 SCANNER
# ============================================================

def scan_m15(
    m15,
    m5
):

    data = indicators(m15)

    setups = []

    setups += range_breakout(data)
    setups += sr_setups(data)
    setups += pattern_setups(data)

    # M5 confirms sweep + CHOCH
    sweep_setups = sweep_choch(
        indicators(m5)
    )

    for setup in sweep_setups:
        setup["tf"] = "M15"

    setups += sweep_setups

    for setup in setups:
        setup["tf"] = setup.get(
            "tf",
            "M15"
        )

    return [
        x
        for x in setups
        if x
        and x.get("score", 0)
        >= MIN_SCORE
    ]


# ============================================================
# EARLY EXIT
# ============================================================

def reversal_reasons(
    df,
    trade
):

    if len(df) < 60:
        return []

    row = df.iloc[-1]

    reasons = []

    direction = trade[
        "direction"
    ]

    body = float(
        row.body
    )

    atr = float(
        row.atr
    )

    if not np.isfinite(atr):
        return reasons

    highs, lows = local_pivots(
        df.iloc[:-2],
        2
    )

    if direction == "BUY":

        if (
            lows
            and
            float(row.close)
            <
            lows[-1][1]
        ):

            reasons.append(
                "M5 structure broke down"
            )

        if (
            row.close < row.open
            and
            body >= 0.75 * atr
            and
            row.close
            <=
            row.low +
            0.30 * row.range
        ):

            reasons.append(
                "strong bearish candle"
            )

        if (
            np.isfinite(row.rsi)
            and
            row.rsi < 43
        ):

            reasons.append(
                "RSI weakness"
            )

        if (
            row.close <
            row.ema20
        ):

            reasons.append(
                "price lost EMA20"
            )

    else:

        if (
            highs
            and
            float(row.close)
            >
            highs[-1][1]
        ):

            reasons.append(
                "M5 structure broke up"
            )

        if (
            row.close > row.open
            and
            body >= 0.75 * atr
            and
            row.close
            >=
            row.low +
            0.70 * row.range
        ):

            reasons.append(
                "strong bullish candle"
            )

        if (
            np.isfinite(row.rsi)
            and
            row.rsi > 57
        ):

            reasons.append(
                "RSI strength"
            )

        if (
            row.close >
            row.ema20
        ):

            reasons.append(
                "price regained EMA20"
            )

    return reasons


# ============================================================
# MONITOR OPEN TRADE
# ============================================================

def monitor_open(
    state,
    m5
):

    if not state["open"]:
        return False

    df = indicators(m5)

    live = float(
        m5.close.iloc[-1]
    )

    changed = False

    for symbol, trade in list(
        state["open"].items()
    ):

        if symbol != SYMBOL:
            continue

        direction = trade[
            "direction"
        ]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        last = m5.iloc[-1]

        hit = None
        exit_price = None

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        # If both happen inside same candle,
        # SL gets priority conservatively.

        if direction == "BUY":

            if float(last.low) <= sl:

                hit = "SL"
                exit_price = sl

            elif float(last.high) >= tp:

                hit = "TP"
                exit_price = tp

        else:

            if float(last.high) >= sl:

                hit = "SL"
                exit_price = sl

            elif float(last.low) <= tp:

                hit = "TP"
                exit_price = tp

        # ----------------------------------------------------
        # EXPIRY
        # ----------------------------------------------------

        opened = pd.to_datetime(
            trade["opened_at"]
        )

        age_hours = (
            pd.Timestamp.now() -
            opened
        ).total_seconds() / 3600

        if (
            hit is None
            and
            EXPIRE_HOURS > 0
            and
            age_hours >= EXPIRE_HOURS
        ):

            hit = "EXPIRY"
            exit_price = live

        # ----------------------------------------------------
        # EARLY EXIT
        # ----------------------------------------------------

        if (
            hit is None
            and
            not trade.get(
                "early_exit_sent"
            )
        ):

            reasons = reversal_reasons(
                df,
                trade
            )

            if direction == "BUY":

                r = (
                    live - entry
                ) / (
                    entry - sl
                )

            else:

                r = (
                    entry - live
                ) / (
                    sl - entry
                )

            required_reasons = (
                2
                if r > 0.25
                else 3
            )

            if len(reasons) >= required_reasons:

                tg(
                    "⚠️ EARLY EXIT\n"
                    f"{SYMBOL}\n"
                    f"{direction}\n"
                    f"Entry: {fmt(entry)}\n"
                    f"Current: {fmt(live)}\n"
                    f"R: {r:.2f}\n"
                    "Reasons: "
                    +
                    ", ".join(reasons)
                    +
                    "\n"
                    "البوت يعتبر الصفقة مغلقة "
                    "داخلياً بسبب انعكاس قوي."
                )

                hit = "EARLY_EXIT"
                exit_price = live

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

        if hit:

            if direction == "BUY":

                pnl = (
                    exit_price -
                    entry
                )

            else:

                pnl = (
                    entry -
                    exit_price
                )

            stats = day_stats(
                state
            )

            if (
                hit == "TP"
                or
                (
                    hit == "EARLY_EXIT"
                    and
                    pnl > 0
                )
            ):

                stats["wins"] += 1
                result = "WIN"

            elif (
                hit == "SL"
                or
                (
                    hit == "EARLY_EXIT"
                    and
                    pnl <= 0
                )
            ):

                stats["losses"] += 1
                result = "LOSS"

            else:

                stats["neutral"] += 1
                result = "NEUTRAL"

            if result == "WIN":
                emoji = "🟢"
            elif result == "LOSS":
                emoji = "🔴"
            else:
                emoji = "⚪"

            tg(
                f"{emoji} {result}\n"
                f"{SYMBOL}\n"
                f"{direction} | "
                f"{trade.get('tf', 'M5')}\n"
                f"Exit: {hit}\n"
                f"Entry: {fmt(entry)}\n"
                f"Exit price: {fmt(exit_price)}\n"
                f"Move: {pnl:+.2f}"
            )

            del state["open"][symbol]

            changed = True

        # ----------------------------------------------------
        # BREAK EVEN ALERT
        # ----------------------------------------------------

        elif not trade.get(
            "be_alerted"
        ):

            risk = abs(
                entry - sl
            )

            favorable = (
                live - entry
                if direction == "BUY"
                else
                entry - live
            )

            if favorable >= risk:

                trade["be_alerted"] = True

                tg(
                    "🟡 BREAK-EVEN ALERT\n"
                    f"{SYMBOL}\n"
                    f"{direction}\n"
                    f"Price: {fmt(live)}\n"
                    "الصفقة حققت +1R. "
                    "إذا الصفقة منفذة يدوياً، "
                    "انقل الستوب للدخول."
                )

                changed = True

    return changed


# ============================================================
# BEST SETUP
# ============================================================

def best_setup(
    setups
):

    if not setups:
        return None

    return sorted(
        setups,
        key=lambda x: (
            x.get("score", 0),
            x.get("rr", 0),
            x.get("signal_time", "")
        ),
        reverse=True
    )[0]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_text(
    setup
):

    emoji = (
        "🟢"
        if setup["direction"] == "BUY"
        else
        "🔴"
    )

    if setup["score"] >= 9:
        grade = "EXCELLENT"

    elif setup["score"] >= 7:
        grade = "GOOD"

    else:
        grade = "MEDIUM"

    return (
        f"{emoji} XAU/USD SIGNAL\n"
        f"{setup['direction']} | "
        f"{setup.get('tf', 'M5')} | "
        f"{grade} "
        f"{setup['score']}/10\n"
        f"Strategy: "
        f"{setup['strategy']}\n"
        f"Entry: "
        f"{fmt(setup['entry'])}\n"
        f"SL: "
        f"{fmt(setup['sl'])}\n"
        f"TP: "
        f"{fmt(setup['tp'])}\n"
        f"RR: "
        f"1:{setup['rr']:.2f}\n"
        f"Reason: "
        f"{setup['reason']}\n"
        f"Time: "
        f"{setup['signal_time']}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    # ========================================================
    # TELEGRAM TEST FIRST
    # ========================================================

    if TEST_MSG:

        telegram_ok = telegram_check()

        if telegram_ok:

            tg(
                "🧪 TEST OK\n"
                "XAU/USD bot Telegram connection "
                "is working."
            )

        else:

            tg(
                "❌ TEST FAILED\n"
                "Telegram credentials or chat ID "
                "need checking."
            )

        # Telegram test does NOT depend on Twelve Data
        if not TD_KEY:
            return

    # ========================================================
    # FETCH M5
    # ========================================================

    try:

        raw = fetch(
            SYMBOL,
            "5min",
            900
        )

    except Exception as error:

        tg(
            "❌ DATA ERROR — XAU/USD\n"
            f"{error}"
        )

        return

    m5 = clean_completed(
        raw,
        5
    )

    if len(m5) < 150:

        tg(
            "❌ DATA ERROR\n"
            "Not enough completed M5 candles."
        )

        return

    m5 = indicators(
        m5
    )

    # ========================================================
    # BUILD M15
    # ========================================================

    m15_all = resample(
        raw,
        "15min"
    )

    m15 = clean_completed(
        m15_all,
        15
    )

    if len(m15) < 100:

        tg(
            "❌ DATA ERROR\n"
            "Not enough completed M15 candles."
        )

        return

    m15 = indicators(
        m15
    )

    # ========================================================
    # STARTUP MESSAGE
    # ========================================================

    today = day_key()

    if (
        STARTUP_MSG
        and
        state.get("last_startup")
        != today
    ):

        telegram_ok = bool(
            TG_TOKEN
            and
            TG_CHAT
            and
            telegram_check()
        )

        tg(
            "🟢 BOT RUNNING — XAU/USD\n"
            "M5 + M15: ACTIVE\n"
            f"Telegram: "
            f"{'OK' if telegram_ok else 'ERROR'}\n"
            "Data: OK\n"
            f"UTC: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        )

        state[
            "last_startup"
        ] = today

    # ========================================================
    # MONITOR CURRENT TRADE
    # ========================================================

    monitor_open(
        state,
        m5
    )

    save_state(
        state
    )

    # ========================================================
    # ONE OPEN TRADE ONLY
    # ========================================================

    if state["open"]:

        return

    # ========================================================
    # DAILY LIMITS
    # ========================================================

    stats = day_stats(
        state
    )

    if (
        stats["trades"]
        >=
        MAX_PER_DAY
    ):

        save_state(
            state
        )

        return

    if (
        stats["losses"]
        >=
        MAX_LOSSES
    ):

        save_state(
            state
        )

        return

    # ========================================================
    # SCAN BOTH TIMEFRAMES
    # ========================================================

    setups = []

    setups += scan_m5(
        m5
    )

    setups += scan_m15(
        m15,
        m5
    )

    if not setups:

        save_state(
            state
        )

        return

    # ========================================================
    # BEST QUALITY SETUP
    # ========================================================

    setup = best_setup(
        setups
    )

    if not setup:
        return

    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    signal_key = (
        f"{SYMBOL}|"
        f"{setup.get('tf', 'M5')}|"
        f"{setup['strategy']}|"
        f"{setup['direction']}|"
        f"{setup['signal_time']}"
    )

    if state[
        "last_signals"
    ].get(signal_key):

        return

    # ========================================================
    # DON'T SEND OLD SIGNALS
    # ========================================================

    signal_time = pd.to_datetime(
        setup["signal_time"]
    )

    signal_age = (
        pd.Timestamp.now() -
        signal_time
    ).total_seconds()

    if signal_age > 20 * 60:

        return

    # ========================================================
    # SEND SIGNAL
    # ========================================================

    tg(
        signal_text(
            setup
        )
    )

    # ========================================================
    # SAVE TRADE
    # ========================================================

    stats["trades"] += 1

    state[
        "last_signals"
    ][signal_key] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    state["open"][SYMBOL] = {

        "direction":
            setup["direction"],

        "entry":
            setup["entry"],

        "sl":
            setup["sl"],

        "tp":
            setup["tp"],

        "tf":
            setup.get(
                "tf",
                "M5"
            ),

        "strategy":
            setup["strategy"],

        "opened_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "be_alerted":
            False,

        "early_exit_sent":
            False,

        "score":
            setup["score"]
    }

    save_state(
        state
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print(
            "FATAL:",
            repr(error)
        )

        try:

            tg(
                "🚨 BOT FATAL ERROR\n"
                f"{type(error).__name__}: "
                f"{error}"
            )

        except Exception:
            pass

        sys.exit(1)
