"""
XAU/USD Responsive Order Flow Engine Bot
M5 + M15
Matches TradingView "Order Flow Engine - Responsive v8"

Logic:
- Delta approximation
- Volume strength
- Candle close position
- Range expansion
- Score 0-100
- BUY/SELL threshold
- No H1/H4 directional filter
- One open trade only
- TP1 / TP2 / ATR SL
- Early exit on strong opposite signal
- Telegram diagnostics
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests


# =========================================================
# ENV
# =========================================================

def env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


TD_KEY = env("TWELVE_DATA_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")

SYMBOLS = [
    s.strip()
    for s in env("SYMBOLS", "XAU/USD").split(",")
    if s.strip()
]

# Same threshold as TradingView indicator
MIN_SCORE = float(env("MIN_SCORE", "75"))

# Same TP values as indicator
TP1_USD = float(env("TP1_USD", "10.0"))
TP2_USD = float(env("TP2_USD", "15.0"))

# Same ATR SL as indicator
SL_ATR_MULT = float(env("SL_ATR_MULT", "1.2"))

# Only M5 + M15 for now
FRAMES = [
    f.strip()
    for f in env("FRAMES", "5min,15min").split(",")
    if f.strip() in ("5min", "15min")
]

TFN = {
    "5min": "M5",
    "15min": "M15"
}

MAX_PER_DAY = int(env("MAX_PER_DAY", "5"))
MAX_LOSSES = int(env("MAX_LOSSES", "3"))

# 0 = no cooldown
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "0"))

# Expiry fallback
EXPIRE_HOURS = float(env("EXPIRE_HOURS", "4"))

# Telegram startup/test
TEST_MSG = env("TEST_MSG", "0") == "1"

# Send one startup message per day
STARTUP_MSG = env("STARTUP_MSG", "1") == "1"

# Conservative same-direction protection
PIPS = {
    "XAU/USD": 0.1,
    "GBP/JPY": 0.01,
    "USD/JPY": 0.01,
    "EUR/USD": 0.0001,
    "GBP/USD": 0.0001,
    "BTC/USD": 1.0,
    "NDX": 1.0
}

STATE_FILE = "state.json"

SENT = []
LAST_ERR = {"msg": ""}


# =========================================================
# TELEGRAM
# =========================================================

def telegram_request(method, payload=None):
    if not TG_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN غير موجود")
        return None

    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"

    try:
        r = requests.post(
            url,
            json=payload or {},
            timeout=20
        )

        print(
            f"Telegram {method}: "
            f"HTTP {r.status_code} | {r.text[:500]}"
        )

        if not r.ok:
            return None

        data = r.json()

        if not data.get("ok"):
            print("Telegram API ERROR:", data)
            return None

        return data

    except Exception as e:
        print("Telegram request failed:", repr(e))
        return None


def telegram_check():
    if not TG_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN غير موجود")
        return False

    result = telegram_request("getMe")

    if result:
        bot = result.get("result", {})
        print(
            "✅ Telegram bot OK:",
            bot.get("username", bot.get("first_name", "unknown"))
        )
        return True

    print("❌ Telegram token غير صالح")
    return False


def tg(text):
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)

    SENT.append(text)

    if not TG_TOKEN:
        print("⚠️ Telegram token missing")
        return False

    if not TG_CHAT:
        print("⚠️ TELEGRAM_CHAT_ID غير موجود")
        return False

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": TG_CHAT,
            "text": text
        }
    )

    return result is not None


# =========================================================
# DATA PREPARATION
# =========================================================

def prep(df):
    d = df.copy()

    # EMA فقط للقراءة الداخلية، وليس فلتر اتجاه
    d["EMA50"] = d["Close"].ewm(
        span=50,
        adjust=False
    ).mean()

    # RSI للمعلومات فقط
    ch = d["Close"].diff()

    up = ch.clip(lower=0).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    dn = (-ch.clip(upper=0)).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    d["RSI"] = 100 - 100 / (1 + up / dn)

    # ATR
    tr = pd.concat(
        [
            d["High"] - d["Low"],
            (d["High"] - d["Close"].shift()).abs(),
            (d["Low"] - d["Close"].shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    d["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # Candle range
    d["Range"] = d["High"] - d["Low"]

    # Body
    d["Body"] = (d["Close"] - d["Open"]).abs()

    # Volume
    d["AvgVolume"] = d["Volume"].rolling(20).mean()

    return d


# =========================================================
# ORDER FLOW APPROXIMATION
# Same logic as TradingView indicator
# =========================================================

def order_flow_score(df):
    d = prep(df)

    if len(d) < 30:
        return None

    c = d.iloc[-1]

    open_price = float(c["Open"])
    close_price = float(c["Close"])
    high = float(c["High"])
    low = float(c["Low"])
    volume = float(c.get("Volume", 0) or 0)

    candle_range = high - low
    body_range = abs(close_price - open_price)

    avg_vol = float(c["AvgVolume"]) if pd.notna(c["AvgVolume"]) else 0.0

    atr = float(c["ATR"]) if pd.notna(c["ATR"]) else 0.0

    if candle_range <= 0:
        return None

    # -----------------------------------------------------
    # Same BUY/SELL volume approximation as indicator
    # -----------------------------------------------------

    if close_price > open_price:
        buy_vol = volume * (
            0.5 + body_range / (2 * candle_range)
        )
        sell_vol = volume - buy_vol

    elif close_price < open_price:
        sell_vol = volume * (
            0.5 + body_range / (2 * candle_range)
        )
        buy_vol = volume - sell_vol

    else:
        buy_vol = volume * 0.5
        sell_vol = volume * 0.5

    delta = buy_vol - sell_vol

    delta_percent = (
        delta / volume * 100
        if volume > 0
        else 0.0
    )

    vol_ratio = (
        volume / avg_vol
        if avg_vol > 0
        else 0.0
    )

    avg_range = (
        float(d["Range"].rolling(20).mean().iloc[-1])
        if len(d) >= 20
        else candle_range
    )

    range_ratio = (
        candle_range / avg_range
        if avg_range > 0
        else 0.0
    )

    # -----------------------------------------------------
    # Bull score
    # Same formula as TradingView
    # -----------------------------------------------------

    bull_score = 0.0

    if delta_percent > 0:
        bull_score += min(
            40.0,
            delta_percent * 0.6
        )

    bull_score += min(
        30.0,
        vol_ratio * 15.0
    )

    close_pos_bull = (
        (close_price - low) / candle_range
        if candle_range > 0
        else 0.0
    )

    bull_score += close_pos_bull * 20.0

    bull_score += min(
        10.0,
        range_ratio * 5.0
    )

    bull_score = min(
        100.0,
        round(bull_score)
    )

    # -----------------------------------------------------
    # Bear score
    # Same formula as TradingView
    # -----------------------------------------------------

    bear_score = 0.0

    if delta_percent < 0:
        bear_score += min(
            40.0,
            abs(delta_percent) * 0.6
        )

    bear_score += min(
        30.0,
        vol_ratio * 15.0
    )

    close_pos_bear = (
        (high - close_price) / candle_range
        if candle_range > 0
        else 0.0
    )

    bear_score += close_pos_bear * 20.0

    bear_score += min(
        10.0,
        range_ratio * 5.0
    )

    bear_score = min(
        100.0,
        round(bear_score)
    )

    direction = 0

    if close_price > open_price and bull_score >= MIN_SCORE:
        direction = 1

    elif close_price < open_price and bear_score >= MIN_SCORE:
        direction = -1

    return {
        "bull": float(bull_score),
        "bear": float(bear_score),
        "delta": float(delta),
        "delta_pct": float(delta_percent),
        "vol_ratio": float(vol_ratio),
        "range_ratio": float(range_ratio),
        "atr": float(atr),
        "range": float(candle_range),
        "body": float(body_range),
        "direction": direction,
        "open": open_price,
        "close": close_price,
        "high": high,
        "low": low,
        "time": d.index[-1].isoformat()
    }


# =========================================================
# CANDLE CONFIRMATION
# =========================================================

def candle_confirmation(df, direction):
    if len(df) < 3:
        return False

    c = df.iloc[-1]

    op = float(c["Open"])
    cl = float(c["Close"])
    hi = float(c["High"])
    lo = float(c["Low"])

    rng = hi - lo

    if rng <= 0:
        return False

    body = abs(cl - op)

    body_ratio = body / rng

    if direction == 1:
        close_position = (cl - lo) / rng

        return (
            cl > op
            and close_position >= 0.60
            and body_ratio >= 0.35
        )

    if direction == -1:
        close_position = (hi - cl) / rng

        return (
            cl < op
            and close_position >= 0.60
            and body_ratio >= 0.35
        )

    return False


# =========================================================
# FRAME PREPARATION
# =========================================================

def fetch(symbol):
    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            timeout=30,
            params={
                "symbol": symbol,
                "interval": "5min",
                "outputsize": 5000,
                "timezone": "UTC",
                "apikey": TD_KEY
            }
        )

        j = response.json()

    except Exception as e:
        LAST_ERR["msg"] = str(e)[:300]
        print("Fetch error:", LAST_ERR["msg"])
        return None

    if "values" not in j:
        LAST_ERR["msg"] = str(
            j.get("message", j)
        )[:300]

        print(
            symbol,
            "ERROR:",
            LAST_ERR["msg"]
        )

        return None

    df = pd.DataFrame(j["values"])

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )

    for c in [
        "open",
        "high",
        "low",
        "close"
    ]:
        df[c] = df[c].astype(float)

    # Twelve Data volume can vary by instrument
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce"
        ).fillna(0.0)
    else:
        df["volume"] = 0.0

    df = (
        df.sort_values("datetime")
        .set_index("datetime")
    )

    df.columns = [
        c.capitalize()
        for c in df.columns
    ]

    return df[
        [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]
    ]


def resample_frame(df, rule):
    if rule == "5min":
        return df.copy()

    return (
        df.resample(rule)
        .agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        })
        .dropna()
    )


def make_frames(df):
    frames = {}

    for tf in FRAMES:
        r = resample_frame(df, tf)

        # Ignore currently forming candle
        if len(r) > 2:
            r = r.iloc[:-1]

        frames[tf] = r.iloc[-500:]

    return frames


# =========================================================
# SIGNAL BUILD
# =========================================================

def build_signal(df, tf, symbol):
    score = order_flow_score(df)

    if not score:
        return None

    direction = score["direction"]

    if direction == 0:
        return None

    # Additional confirmation only.
    # Does NOT add an external trend filter.
    if not candle_confirmation(
        df,
        direction
    ):
        return None

    entry = float(score["close"])
    atr = float(score["atr"])

    if atr <= 0:
        return None

    # Same SL logic as indicator
    if direction == 1:
        sl = score["low"] - atr * SL_ATR_MULT

        tp1 = entry + TP1_USD
        tp2 = entry + TP2_USD

    else:
        sl = score["high"] + atr * SL_ATR_MULT

        tp1 = entry - TP1_USD
        tp2 = entry - TP2_USD

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    return {
        "sym": symbol,
        "tf": tf,
        "dir": direction,

        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,

        "risk": risk,

        "bull": score["bull"],
        "bear": score["bear"],

        "delta": score["delta"],
        "delta_pct": score["delta_pct"],

        "vol_ratio": score["vol_ratio"],
        "range_ratio": score["range_ratio"],

        "atr": atr,

        "candle_time": score["time"]
    }


# =========================================================
# GRADE
# =========================================================

def grade(score):
    if score >= 90:
        return "ممتازة ⭐⭐⭐"

    if score >= 82:
        return "جيدة جداً ⭐⭐"

    if score >= 75:
        return "مقبولة ⭐"

    return "ضعيفة ⚠️"


# =========================================================
# STATE
# =========================================================

def load():
    try:
        with open(
            STATE_FILE,
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return {
            "open": [],
            "days": {},
            "last": {},
            "direction": {},
            "startup": {}
        }


def save(st):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            st,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# TRADE MANAGEMENT
# =========================================================

def check_open(st, m5s, now, day):
    still = []

    for trade in st["open"]:

        sym = trade["sym"]

        df = m5s.get(sym)

        if df is None or len(df) < 2:
            still.append(trade)
            continue

        t0 = pd.Timestamp(
            trade["time"]
        )

        result = None
        result_price = None

        for idx, c in df[
            df.index > t0
        ].iterrows():

            if trade["dir"] == 1:

                sl_hit = (
                    c["Low"] <= trade["sl"]
                )

                tp1_hit = (
                    c["High"] >= trade["tp1"]
                )

            else:

                sl_hit = (
                    c["High"] >= trade["sl"]
                )

                tp1_hit = (
                    c["Low"] <= trade["tp1"]
                )

            # Conservative:
            # SL first if both happen
            if sl_hit:
                result = "SL"
                result_price = trade["sl"]
                break

            if tp1_hit:
                result = "TP1"
                result_price = trade["tp1"]
                break

        live = float(
            df["Close"].iloc[-1]
        )

        risk = (
            abs(
                trade["entry"]
                - trade["sl"]
            )
            or 1e-9
        )

        R = (
            trade["dir"]
            * (live - trade["entry"])
            / risk
        )

        # -------------------------------------------------
        # EXPIRY
        # -------------------------------------------------

        if result is None:

            age_hours = (
                now - t0
            ).total_seconds() / 3600

            if age_hours >= EXPIRE_HOURS:

                result = "EXPIRED"
                result_price = live

        # -------------------------------------------------
        # TP1
        # -------------------------------------------------

        if result == "TP1":

            day["wins"] += 1
            day["r"] += trade.get(
                "rr",
                1.0
            )

            side = (
                "شراء 🟢"
                if trade["dir"] == 1
                else "بيع 🔴"
            )

            tg(
                f"✅ TP1 HIT\n"
                f"{sym} | {side} | {TFN.get(trade['tf'], trade['tf'])}\n"
                f"الدخول: {trade['entry']:.2f}\n"
                f"TP1: {trade['tp1']:.2f}\n"
                f"TP2: {trade['tp2']:.2f}\n"
                f"النتيجة: +{trade.get('rr', 1.0):.2f}R\n\n"
                f"البوت يعتبر الصفقة منتهية ويرجع يبحث عن إشارة جديدة."
            )

            continue

        # -------------------------------------------------
        # SL
        # -------------------------------------------------

        if result == "SL":

            day["losses"] += 1
            day["r"] -= 1.0

            side = (
                "شراء 🟢"
                if trade["dir"] == 1
                else "بيع 🔴"
            )

            tg(
                f"❌ STOP LOSS\n"
                f"{sym} | {side} | {TFN.get(trade['tf'], trade['tf'])}\n"
                f"الدخول: {trade['entry']:.2f}\n"
                f"الستوب: {trade['sl']:.2f}\n"
                f"النتيجة: -1R\n\n"
                f"البوت رجع يبحث عن إشارة جديدة."
            )

            continue

        # -------------------------------------------------
        # EXPIRED
        # -------------------------------------------------

        if result == "EXPIRED":

            tg(
                f"⌛ انتهت مدة الصفقة\n"
                f"{sym} | {TFN.get(trade['tf'], trade['tf'])}\n"
                f"الدخول: {trade['entry']:.2f}\n"
                f"السعر الحالي: {live:.2f}\n"
                f"النتيجة التقريبية: {R:+.2f}R\n\n"
                f"تم إلغاء متابعة الصفقة والعودة للبحث."
            )

            continue

        # -------------------------------------------------
        # EARLY EXIT
        # -------------------------------------------------

        opposite = (
            "bull"
            if trade["dir"] == -1
            else "bear"
        )

        score = order_flow_score(
            df.iloc[:-1]
        )

        strong_opposite = False

        if score:

            opposite_score = (
                score[opposite]
            )

            if (
                opposite_score >= 85
                and (
                    (
                        trade["dir"] == 1
                        and score["close"] < score["open"]
                    )
                    or
                    (
                        trade["dir"] == -1
                        and score["close"] > score["open"]
                    )
                )
            ):
                strong_opposite = True

        if (
            strong_opposite
            and R > 0.10
        ):

            day["early"] += 1

            if R > 0:
                day["wins"] += 1
            elif R < 0:
                day["losses"] += 1

            day["r"] += R

            side = (
                "شراء 🟢"
                if trade["dir"] == 1
                else "بيع 🔴"
            )

            tg(
                f"🚨 EARLY EXIT\n"
                f"{sym} | {side} | {TFN.get(trade['tf'], trade['tf'])}\n"
                f"الدخول: {trade['entry']:.2f}\n"
                f"السعر الحالي: {live:.2f}\n"
                f"النتيجة: {R:+.2f}R\n\n"
                f"ظهرت شمعة معاكسة قوية "
                f"وسكور معاكس ≥ 85.\n"
                f"يفضّل إغلاق الصفقة وعدم انتظار انعكاس أكبر."
            )

            continue

        # -------------------------------------------------
        # KEEP OPEN
        # -------------------------------------------------

        still.append(trade)

    st["open"] = still


# =========================================================
# FORMAT MESSAGE
# =========================================================

def signal_message(s):
    side = (
        "BUY 🟢"
        if s["dir"] == 1
        else "SELL 🔴"
    )

    score = (
        s["bull"]
        if s["dir"] == 1
        else s["bear"]
    )

    gl = grade(score)

    pip = PIPS.get(
        s["sym"],
        0.1
    )

    risk_pips = (
        s["risk"] / pip
    )

    tp1_pips = (
        abs(
            s["tp1"]
            - s["entry"]
        ) / pip
    )

    tp2_pips = (
        abs(
            s["tp2"]
            - s["entry"]
        ) / pip
    )

    return (
        f"⚡ {side} — {s['sym']}\n\n"

        f"⭐ التقييم: {gl}\n"
        f"Score: {score:.0f}/100\n"
        f"الفريم: {TFN.get(s['tf'], s['tf'])}\n\n"

        f"💰 الدخول: {s['entry']:.2f}\n"
        f"🛑 SL: {s['sl']:.2f} "
        f"({risk_pips:.0f} pip)\n"

        f"🎯 TP1: {s['tp1']:.2f} "
        f"({tp1_pips:.0f} pip)\n"

        f"🎯 TP2: {s['tp2']:.2f} "
        f"({tp2_pips:.0f} pip)\n\n"

        f"📊 Order Flow\n"
        f"Bull: {s['bull']:.0f}%\n"
        f"Bear: {s['bear']:.0f}%\n"
        f"Delta: {s['delta']:.0f}\n"
        f"Delta %: {s['delta_pct']:+.1f}%\n"
        f"Volume: {s['vol_ratio']:.2f}x\n"
        f"Range: {s['range_ratio']:.2f}x\n"
        f"ATR: {s['atr']:.2f}\n\n"

        f"📌 المنطق:\n"
        f"Delta + Volume + مكان الإغلاق + توسع الرينج\n"
        f"بدون فلتر اتجاه H1/H4.\n\n"

        f"⚠️ تنبيه: البوت يرسل إشارة ويراقبها، "
        f"ولا ينفذ أمر عند الوسيط."
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # -----------------------------------------------------
    # Telegram test MUST happen before Twelve Data check
    # -----------------------------------------------------

    if TEST_MSG:

        telegram_check()

        tg(
            "✅ البوت شغال.\n"
            "Telegram connection test OK.\n"
            "Order Flow Engine: M5 + M15\n"
            f"Threshold: {MIN_SCORE:.0f}%"
        )

        return

    # -----------------------------------------------------
    # Check Telegram
    # -----------------------------------------------------

    if TG_TOKEN and TG_CHAT:
        telegram_check()
    else:
        print(
            "⚠️ Telegram credentials ناقصة"
        )

    # -----------------------------------------------------
    # Twelve Data
    # -----------------------------------------------------

    if not TD_KEY:

        print(
            "❌ TWELVE_DATA_API_KEY غير موجود"
        )

        tg(
            "❌ البوت اشتغل لكن "
            "TWELVE_DATA_API_KEY غير موجود."
        )

        return

    # -----------------------------------------------------
    # Time
    # -----------------------------------------------------

    now = pd.Timestamp(
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    # -----------------------------------------------------
    # State
    # -----------------------------------------------------

    st = load()

    if not isinstance(
        st.get("open"),
        list
    ):
        st["open"] = []

    if not isinstance(
        st.get("days"),
        dict
    ):
        st["days"] = {}

    if not isinstance(
        st.get("last"),
        dict
    ):
        st["last"] = {}

    if not isinstance(
        st.get("direction"),
        dict
    ):
        st["direction"] = {}

    if not isinstance(
        st.get("startup"),
        dict
    ):
        st["startup"] = {}

    day = st["days"].setdefault(
        today,
        {}
    )

    for key, default in {
        "sent": 0,
        "wins": 0,
        "losses": 0,
        "early": 0,
        "r": 0.0,
        "summary": False
    }.items():

        day.setdefault(
            key,
            default
        )

    # -----------------------------------------------------
    # Fetch data
    # -----------------------------------------------------

    m5s = {}

    for sym in SYMBOLS:

        df = fetch(sym)

        if (
            df is not None
            and len(df) >= 300
        ):
            m5s[sym] = df

        time.sleep(1.0)

    if not m5s:

        tg(
            "⚠️ البوت شغال لكن ما قدر "
            "يجيب بيانات Twelve Data.\n\n"
            f"الخطأ:\n{LAST_ERR['msg']}"
        )

        save(st)

        return

    # -----------------------------------------------------
    # Build M5 / M15
    # -----------------------------------------------------

    frames = {
        sym: make_frames(df)
        for sym, df in m5s.items()
    }

    # -----------------------------------------------------
    # Manage existing trades FIRST
    # -----------------------------------------------------

    check_open(
        st,
        m5s,
        now,
        day
    )

    # -----------------------------------------------------
    # Startup message once per day
    # -----------------------------------------------------

    if (
        STARTUP_MSG
        and st["startup"].get(today) != "sent"
    ):

        sym0 = next(
            iter(m5s)
        )

        px = float(
            m5s[sym0]["Close"].iloc[-1]
        )

        tg(
            f"🟢 البوت شغال\n"
            f"{sym0}\n"
            f"السعر: {px:.2f}\n"
            f"الفريمات: M5 + M15\n"
            f"Order Flow Threshold: {MIN_SCORE:.0f}%\n"
            f"الصفقات المفتوحة: {len(st['open'])}"
        )

        st["startup"][today] = "sent"

    # -----------------------------------------------------
    # Do not enter if existing trade
    # -----------------------------------------------------

    open_symbols = {
        t["sym"]
        for t in st["open"]
    }

    if open_symbols:

        print(
            "Open trade:",
            open_symbols
        )

        save(st)
        return

    # -----------------------------------------------------
    # Daily limits
    # -----------------------------------------------------

    if (
        MAX_LOSSES
        and day["losses"] >= MAX_LOSSES
    ):

        print(
            "Daily loss limit reached."
        )

        save(st)
        return

    if (
        MAX_PER_DAY
        and day["sent"] >= MAX_PER_DAY
    ):

        print(
            "Daily signal limit reached."
        )

        save(st)
        return

    # -----------------------------------------------------
    # Find signals
    # -----------------------------------------------------

    candidates = []

    for sym, frame_set in frames.items():

        # Same instrument cooldown
        last_time = st["last"].get(sym)

        if last_time:

            elapsed = (
                now
                - pd.Timestamp(last_time)
            ).total_seconds() / 60

            if elapsed < COOLDOWN_MIN:
                continue

        for tf in FRAMES:

            df = frame_set.get(tf)

            if df is None or len(df) < 80:
                continue

            signal = build_signal(
                df,
                tf,
                sym
            )

            if not signal:
                continue

            # ------------------------------------------------
            # Same direction protection like lastDirection
            # ------------------------------------------------

            old_direction = int(
                st["direction"].get(
                    sym,
                    0
                )
            )

            if (
                old_direction
                == signal["dir"]
            ):
                continue

            signal["signal_key"] = (
                f"{sym}|"
                f"{tf}|"
                f"{signal['dir']}|"
                f"{signal['candle_time']}"
            )

            candidates.append(
                signal
            )

    # -----------------------------------------------------
    # No signal
    # -----------------------------------------------------

    if not candidates:

        print(
            "No valid Order Flow signal."
        )

        save(st)
        return

    # -----------------------------------------------------
    # Choose strongest signal
    # -----------------------------------------------------

    candidates.sort(
        key=lambda x: (
            max(
                x["bull"],
                x["bear"]
            ),
            1 if x["tf"] == "15min" else 0
        ),
        reverse=True
    )

    signal = candidates[0]

    # -----------------------------------------------------
    # Send signal
    # -----------------------------------------------------

    tg(
        signal_message(signal)
    )

    # -----------------------------------------------------
    # Save trade
    # -----------------------------------------------------

    score = (
        signal["bull"]
        if signal["dir"] == 1
        else signal["bear"]
    )

    rr = (
        abs(
            signal["tp1"]
            - signal["entry"]
        )
        / max(
            signal["risk"],
            1e-9
        )
    )

    trade = {
        "sym": signal["sym"],
        "tf": signal["tf"],
        "dir": signal["dir"],

        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],

        "rr": rr,

        "score": score,

        "time": signal["candle_time"],

        "be": False
    }

    st["open"].append(
        trade
    )

    st["last"][
        signal["sym"]
    ] = now.isoformat()

    st["direction"][
        signal["sym"]
    ] = signal["dir"]

    day["sent"] += 1

    # -----------------------------------------------------
    # Clean old state
    # -----------------------------------------------------

    for key in list(
        st["days"].keys()
    ):

        try:

            if (
                now
                - pd.Timestamp(
                    key
                )
            ).days > 14:

                del st["days"][key]

        except Exception:
            pass

    # -----------------------------------------------------
    # Daily summary
    # -----------------------------------------------------

    if (
        now.hour >= 21
        and not day["summary"]
    ):

        tg(
            f"📊 ملخص {today}\n\n"
            f"الإشارات: {day['sent']}\n"
            f"رابحة: {day['wins']}\n"
            f"خاسرة: {day['losses']}\n"
            f"Early Exit: {day['early']}\n"
            f"صافي R: {day['r']:+.2f}"
        )

        day["summary"] = True

    save(st)

    print(
        "✅ Run completed successfully."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
