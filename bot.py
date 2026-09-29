"""XAU/USD GitHub Actions Bot
M5 + M15 | Telegram | Quality Grades | One Open Trade | Early Exit
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests


# =========================================================
# SETTINGS
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

# الحد الأدنى العام للإشارة
MIN_SCORE = float(env("MIN_SCORE", "6"))

# الهدف الأساسي
RR = float(env("RR", "1.5"))

# أقصى عدد صفقات فعلية باليوم
MAX_PER_DAY = int(env("MAX_PER_DAY", "5"))

# إذا وصل عدد الخسائر لهذا الرقم، يتوقف عن فتح صفقات جديدة
MAX_LOSSES = int(env("MAX_LOSSES", "3"))

# لا يوجد كولداون إجباري
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "0"))

# انتهاء الصفقة إذا بقيت بدون TP/SL
EXPIRE_HOURS = float(env("EXPIRE_HOURS", "4"))

# رسالة حالة البوت
HEARTBEAT_HOURS = float(env("HEARTBEAT_HOURS", "1"))

# ستوب Sweep
SWEEP_SL_POINTS = float(env("SWEEP_SL_POINTS", "10"))

# درجات الجودة
EXCELLENT_SCORE = float(env("EXCELLENT_SCORE", "9"))
GOOD_SCORE = float(env("GOOD_SCORE", "7"))

# الحد الأدنى لقوة الانعكاس للخروج المبكر
EARLY_EXIT_SCORE = int(env("EARLY_EXIT_SCORE", "4"))

PIPS = {
    "XAU/USD": 0.1,
    "GBP/JPY": 0.01,
    "USD/JPY": 0.01,
    "EUR/USD": 0.0001,
    "GBP/USD": 0.0001,
    "BTC/USD": 1.0,
    "NDX": 1.0,
}

STATE_FILE = "state.json"

SENT = []

LAST_ERR = {
    "msg": ""
}


# =========================================================
# INDICATORS
# =========================================================

def prep(df):
    d = df.copy()

    d["EMA50"] = d["Close"].ewm(
        span=50,
        adjust=False
    ).mean()

    d["EMA200"] = d["Close"].ewm(
        span=200,
        adjust=False
    ).mean()

    ch = d["Close"].diff()

    up = ch.clip(
        lower=0
    ).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    dn = (-ch.clip(
        upper=0
    )).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    dn = dn.replace(0, 1e-9)

    d["RSI"] = 100 - 100 / (1 + up / dn)

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

    return d


def swings(d, n=3):
    h = d["High"].values
    l = d["Low"].values

    H = []
    L = []

    for i in range(n, len(d) - n):

        if h[i] == h[i - n:i + n + 1].max():
            H.append(
                (
                    i,
                    float(h[i])
                )
            )

        if l[i] == l[i - n:i + n + 1].min():
            L.append(
                (
                    i,
                    float(l[i])
                )
            )

    return H, L


def candle_info(d):

    if len(d) < 3:
        return False, False, 0.0, 1e-9

    c = d.iloc[-1]
    p = d.iloc[-2]

    body = abs(
        c["Close"] - c["Open"]
    )

    rng = max(
        c["High"] - c["Low"],
        1e-9
    )

    upper_wick = (
        c["High"]
        - max(c["Close"], c["Open"])
    )

    lower_wick = (
        min(c["Close"], c["Open"])
        - c["Low"]
    )

    bull = (
        (
            c["Close"] > c["Open"]
            and (
                body >= 0.55 * rng
                or lower_wick >= 2 * body
            )
        )
        or
        (
            c["Close"] > c["Open"]
            and p["Close"] < p["Open"]
            and c["Close"] >= p["Open"]
        )
    )

    bear = (
        (
            c["Close"] < c["Open"]
            and (
                body >= 0.55 * rng
                or upper_wick >= 2 * body
            )
        )
        or
        (
            c["Close"] < c["Open"]
            and p["Close"] > p["Open"]
            and c["Close"] <= p["Open"]
        )
    )

    return (
        bool(bull),
        bool(bear),
        body,
        rng
    )


# =========================================================
# PREVIOUS HOUR
# =========================================================

def previous_hour_range(df):

    if len(df) < 20:
        return None

    last_time = df.index[-1]

    hour_end = last_time.floor("h")
    hour_start = hour_end - pd.Timedelta(hours=1)

    x = df[
        (df.index >= hour_start)
        & (df.index < hour_end)
    ]

    if len(x) < 4:
        return None

    return (
        float(x["High"].max()),
        float(x["Low"].min())
    )


# =========================================================
# CHOCH AFTER SWEEP
# =========================================================

def minor_choch_after_sweep(
    d,
    sweep_index,
    direction
):
    """
    direction:
    -1 = بعد Sweep قمة نبحث عن CHOCH هابط
    +1 = بعد Sweep قاع نبحث عن CHOCH صاعد
    """

    if sweep_index < 6:
        return None

    if sweep_index >= len(d) - 1:
        return None

    start = max(
        0,
        sweep_index - 8
    )

    before = d.iloc[
        start:sweep_index
    ]

    if len(before) < 4:
        return None

    H, L = swings(
        before,
        2
    )

    if direction == -1:

        if not L:
            return None

        level = float(L[-1][1])

        for k in range(
            sweep_index + 1,
            len(d)
        ):

            if float(
                d["Close"].iloc[k]
            ) < level:

                return (
                    k,
                    level
                )

    else:

        if not H:
            return None

        level = float(H[-1][1])

        for k in range(
            sweep_index + 1,
            len(d)
        ):

            if float(
                d["Close"].iloc[k]
            ) > level:

                return (
                    k,
                    level
                )

    return None


# =========================================================
# LIQUIDITY SWEEP + CHOCH
# =========================================================

def liquidity_sweep(
    d,
    pip
):

    if len(d) < 40:
        return []

    result = []

    previous = previous_hour_range(d)

    if not previous:
        return result

    previous_high, previous_low = previous

    start = max(
        10,
        len(d) - 12
    )

    for i in range(
        start,
        len(d)
    ):

        c = d.iloc[i]

        rng = max(
            float(c["High"] - c["Low"]),
            1e-9
        )

        # -----------------------------
        # SELL: sweep previous hour high
        # -----------------------------

        upper_wick = (
            c["High"]
            - max(
                c["Open"],
                c["Close"]
            )
        )

        if (
            c["High"] > previous_high
            and c["Close"] < previous_high
            and upper_wick >= 0.30 * rng
        ):

            choch = minor_choch_after_sweep(
                d,
                i,
                -1
            )

            if (
                choch
                and choch[0] == len(d) - 1
            ):

                sl = (
                    float(c["High"])
                    + SWEEP_SL_POINTS * pip
                )

                tp = float(
                    d["Low"].iloc[
                        max(0, i):
                    ].min()
                )

                if tp >= float(c["Close"]):

                    tp = float(
                        d["Low"].iloc[-10:].min()
                    )

                result.append(
                    {
                        "name":
                            "Liquidity Sweep + CHOCH",

                        "dir":
                            -1,

                        "sl":
                            sl,

                        "tp_raw":
                            tp,

                        "base":
                            6,

                        "why":
                            (
                                "سحب سيولة فوق قمة "
                                "الساعة السابقة ثم "
                                f"CHOCH هابط تحت {choch[1]:.2f}"
                            )
                    }
                )

                break

        # -----------------------------
        # BUY: sweep previous hour low
        # -----------------------------

        lower_wick = (
            min(
                c["Open"],
                c["Close"]
            )
            - c["Low"]
        )

        if (
            c["Low"] < previous_low
            and c["Close"] > previous_low
            and lower_wick >= 0.30 * rng
        ):

            choch = minor_choch_after_sweep(
                d,
                i,
                1
            )

            if (
                choch
                and choch[0] == len(d) - 1
            ):

                sl = (
                    float(c["Low"])
                    - SWEEP_SL_POINTS * pip
                )

                tp = float(
                    d["High"].iloc[
                        max(0, i):
                    ].max()
                )

                if tp <= float(c["Close"]):

                    tp = float(
                        d["High"].iloc[-10:].max()
                    )

                result.append(
                    {
                        "name":
                            "Liquidity Sweep + CHOCH",

                        "dir":
                            1,

                        "sl":
                            sl,

                        "tp_raw":
                            tp,

                        "base":
                            6,

                        "why":
                            (
                                "سحب سيولة تحت قاع "
                                "الساعة السابقة ثم "
                                f"CHOCH صاعد فوق {choch[1]:.2f}"
                            )
                    }
                )

                break

    return result


# =========================================================
# RANGE BREAKOUT
# =========================================================

def range_breakout(d):

    if len(d) < 40:
        return []

    tf = d.attrs.get(
        "tf",
        "5min"
    )

    # ساعة كاملة على الأقل
    W = 12 if tf == "5min" else 4

    if len(d) <= W + 2:
        return []

    range_df = d.iloc[
        -(W + 1):-1
    ]

    rh = float(
        range_df["High"].max()
    )

    rl = float(
        range_df["Low"].min()
    )

    size = rh - rl

    atr = float(
        d["ATR"].iloc[-1]
    )

    if size <= 0:
        return []

    if size > 4.0 * atr:
        return []

    tolerance = 0.12 * atr

    touches_high = (
        range_df["High"]
        >= rh - tolerance
    ).sum()

    touches_low = (
        range_df["Low"]
        <= rl + tolerance
    ).sum()

    c = d.iloc[-1]

    body = abs(
        c["Close"] - c["Open"]
    )

    rng = max(
        c["High"] - c["Low"],
        1e-9
    )

    result = []

    midpoint = (
        rh + rl
    ) / 2

    # BUY
    if (
        touches_high >= 2
        and c["Close"] > rh
        and c["Open"] > rh
        and body >= 0.55 * rng
    ):

        risk = abs(
            float(c["Close"])
            - midpoint
        )

        result.append(
            {
                "name":
                    "Range Breakout",

                "dir":
                    1,

                "sl":
                    midpoint,

                "tp_raw":
                    float(c["Close"])
                    + 2 * risk,

                "base":
                    5,

                "why":
                    (
                        f"إغلاق كامل فوق رينج "
                        f"ساعة [{rl:.2f}-{rh:.2f}]"
                    )
            }
        )

    # SELL
    if (
        touches_low >= 2
        and c["Close"] < rl
        and c["Open"] < rl
        and body >= 0.55 * rng
    ):

        risk = abs(
            float(c["Close"])
            - midpoint
        )

        result.append(
            {
                "name":
                    "Range Breakout",

                "dir":
                    -1,

                "sl":
                    midpoint,

                "tp_raw":
                    float(c["Close"])
                    - 2 * risk,

                "base":
                    5,

                "why":
                    (
                        f"إغلاق كامل تحت رينج "
                        f"ساعة [{rl:.2f}-{rh:.2f}]"
                    )
            }
        )

    return result


# =========================================================
# PATTERNS
# =========================================================

def pattern_setups(d):

    result = []

    if len(d) < 40:
        return result

    atr = float(
        d["ATR"].iloc[-1]
    )

    close = float(
        d["Close"].iloc[-1]
    )

    previous_close = float(
        d["Close"].iloc[-2]
    )

    H, L = swings(
        d,
        2
    )

    def add(
        name,
        direction,
        sl,
        why,
        base=4
    ):

        result.append(
            {
                "name": name,
                "dir": direction,
                "sl": float(sl),
                "tp_raw": None,
                "base": base,
                "why": why
            }
        )

    # =====================================================
    # DOUBLE TOP
    # =====================================================

    if len(H) >= 2:

        i1, p1 = H[-2]
        i2, p2 = H[-1]

        if (
            i2 - i1 >= 6
            and abs(p1 - p2) <= 0.35 * atr
            and len(d) - 1 - i2 <= 25
        ):

            neckline = float(
                d["Low"].iloc[
                    i1:i2 + 1
                ].min()
            )

            if (
                close < neckline
                and previous_close >= neckline
            ):

                add(
                    "Double Top",
                    -1,
                    max(p1, p2) + 0.25 * atr,
                    f"Double Top + كسر العنق {neckline:.2f}",
                    5
                )

    # =====================================================
    # DOUBLE BOTTOM
    # =====================================================

    if len(L) >= 2:

        i1, p1 = L[-2]
        i2, p2 = L[-1]

        if (
            i2 - i1 >= 6
            and abs(p1 - p2) <= 0.35 * atr
            and len(d) - 1 - i2 <= 25
        ):

            neckline = float(
                d["High"].iloc[
                    i1:i2 + 1
                ].max()
            )

            if (
                close > neckline
                and previous_close <= neckline
            ):

                add(
                    "Double Bottom",
                    1,
                    min(p1, p2) - 0.25 * atr,
                    f"Double Bottom + كسر العنق {neckline:.2f}",
                    5
                )

    # =====================================================
    # HEAD & SHOULDERS
    # =====================================================

    if len(H) >= 3:

        (i1, p1), \
        (i2, p2), \
        (i3, p3) = H[-3:]

        if (
            p2 > max(p1, p3) + 0.45 * atr
            and abs(p1 - p3) <= 0.8 * atr
            and i3 - i1 <= 80
        ):

            neck1 = float(
                d["Low"].iloc[
                    i1:i2 + 1
                ].min()
            )

            neck2 = float(
                d["Low"].iloc[
                    i2:i3 + 1
                ].min()
            )

            neckline = (
                neck1 + neck2
            ) / 2

            if (
                close < neckline
                and previous_close >= neckline
            ):

                add(
                    "Head & Shoulders",
                    -1,
                    max(p1, p3) + 0.25 * atr,
                    f"رأس وكتفين + كسر العنق {neckline:.2f}",
                    5
                )

    # =====================================================
    # INVERSE H&S
    # =====================================================

    if len(L) >= 3:

        (i1, p1), \
        (i2, p2), \
        (i3, p3) = L[-3:]

        if (
            p2 < min(p1, p3) - 0.45 * atr
            and abs(p1 - p3) <= 0.8 * atr
            and i3 - i1 <= 80
        ):

            neck1 = float(
                d["High"].iloc[
                    i1:i2 + 1
                ].max()
            )

            neck2 = float(
                d["High"].iloc[
                    i2:i3 + 1
                ].max()
            )

            neckline = (
                neck1 + neck2
            ) / 2

            if (
                close > neckline
                and previous_close <= neckline
            ):

                add(
                    "Inverse Head & Shoulders",
                    1,
                    min(p1, p3) - 0.25 * atr,
                    f"رأس وكتفين مقلوب + كسر العنق {neckline:.2f}",
                    5
                )

    # =====================================================
    # TRIPLE TOP
    # =====================================================

    if len(H) >= 3:

        last_three = H[-3:]

        prices = [
            x[1]
            for x in last_three
        ]

        if (
            max(prices)
            - min(prices)
            <= 0.45 * atr
            and H[-1][0] - H[-3][0] >= 10
        ):

            neckline = float(
                d["Low"].iloc[
                    H[-3][0]:H[-1][0] + 1
                ].min()
            )

            if (
                close < neckline
                and previous_close >= neckline
            ):

                add(
                    "Triple Top",
                    -1,
                    max(prices) + 0.25 * atr,
                    f"Triple Top + كسر {neckline:.2f}",
                    5
                )

    # =====================================================
    # TRIPLE BOTTOM
    # =====================================================

    if len(L) >= 3:

        last_three = L[-3:]

        prices = [
            x[1]
            for x in last_three
        ]

        if (
            max(prices)
            - min(prices)
            <= 0.45 * atr
            and L[-1][0] - L[-3][0] >= 10
        ):

            neckline = float(
                d["High"].iloc[
                    L[-3][0]:L[-1][0] + 1
                ].max()
            )

            if (
                close > neckline
                and previous_close <= neckline
            ):

                add(
                    "Triple Bottom",
                    1,
                    min(prices) - 0.25 * atr,
                    f"Triple Bottom + كسر {neckline:.2f}",
                    5
                )

    # =====================================================
    # TRIANGLE / WEDGE BREAKOUT
    # =====================================================

    if (
        len(H) >= 2
        and len(L) >= 2
    ):

        h1, h2 = H[-2], H[-1]
        l1, l2 = L[-2], L[-1]

        hs = (
            h2[1] - h1[1]
        ) / max(
            h2[0] - h1[0],
            1
        )

        ls = (
            l2[1] - l1[1]
        ) / max(
            l2[0] - l1[0],
            1
        )

        x = len(d) - 1

        upper = (
            h2[1]
            + hs * (x - h2[0])
        )

        lower = (
            l2[1]
            + ls * (x - l2[0])
        )

        width = upper - lower

        if (
            width > 0
            and width < 3.0 * atr
        ):

            if (
                close > upper
                and previous_close <= upper
            ):

                add(
                    "Triangle / Wedge Breakout",
                    1,
                    lower,
                    "كسر الحد العلوي للنموذج",
                    4
                )

            elif (
                close < lower
                and previous_close >= lower
            ):

                add(
                    "Triangle / Wedge Breakout",
                    -1,
                    upper,
                    "كسر الحد السفلي للنموذج",
                    4
                )

    return result


# =========================================================
# SUPPORT / RESISTANCE REJECTION
# =========================================================

def technical_setups(d):

    result = []

    if len(d) < 40:
        return result

    atr = float(
        d["ATR"].iloc[-1]
    )

    close = float(
        d["Close"].iloc[-1]
    )

    bull, bear, body, rng = candle_info(d)

    H, L = swings(
        d,
        3
    )

    if len(H) >= 2:

        resistance = H[-1][1]

        if (
            bear
            and abs(close - resistance)
            <= 0.5 * atr
        ):

            result.append(
                {
                    "name":
                        "مقاومة + رفض",

                    "dir":
                        -1,

                    "sl":
                        resistance + 0.4 * atr,

                    "tp_raw":
                        None,

                    "base":
                        4,

                    "why":
                        (
                            f"رفض واضح قرب مقاومة "
                            f"{resistance:.2f}"
                        )
                }
            )

    if len(L) >= 2:

        support = L[-1][1]

        if (
            bull
            and abs(close - support)
            <= 0.5 * atr
        ):

            result.append(
                {
                    "name":
                        "دعم + رفض",

                    "dir":
                        1,

                    "sl":
                        support - 0.4 * atr,

                    "tp_raw":
                        None,

                    "base":
                        4,

                    "why":
                        (
                            f"رفض واضح قرب دعم "
                            f"{support:.2f}"
                        )
                }
            )

    return result


# =========================================================
# SCANNER
# =========================================================

def scan(
    df,
    timeframe,
    pip
):

    d = prep(
        df.copy()
    )

    d.attrs["tf"] = timeframe

    if len(d) < 80:
        return [], {}

    price = float(
        d["Close"].iloc[-1]
    )

    atr = float(
        d["ATR"].iloc[-1]
    )

    rsi = float(
        d["RSI"].iloc[-1]
    )

    setups = []

    setups.extend(
        liquidity_sweep(
            d,
            pip
        )
    )

    setups.extend(
        range_breakout(
            d
        )
    )

    setups.extend(
        pattern_setups(
            d
        )
    )

    setups.extend(
        technical_setups(
            d
        )
    )

    bull, bear, body, rng = candle_info(d)

    for s in setups:

        score = float(
            s["base"]
        )

        tags = []

        # شمعة تأكيد
        if (
            s["dir"] == 1
            and bull
        ) or (
            s["dir"] == -1
            and bear
        ):

            score += 1

            tags.append(
                "شمعة تأكيد"
            )

        # RSI
        if (
            s["dir"] == 1
            and rsi < 55
        ) or (
            s["dir"] == -1
            and rsi > 45
        ):

            score += 1

            tags.append(
                f"RSI مناسب {rsi:.0f}"
            )

        # قوة الشمعة
        if body >= 0.7 * atr:

            score += 1

            tags.append(
                "زخم شمعة قوي"
            )

        s["score"] = score
        s["tags"] = tags

    return setups, {
        "price": price,
        "atr": atr,
        "rsi": rsi
    }


# =========================================================
# BUILD TRADE
# =========================================================

def build(
    setup,
    price,
    atr,
    pip,
    rr
):

    direction = setup["dir"]

    sl = float(
        setup["sl"]
    )

    # الستوب لازم يكون بالجهة الصحيحة
    if (
        direction == 1
        and sl >= price
    ):
        return None

    if (
        direction == -1
        and sl <= price
    ):
        return None

    risk = abs(
        price - sl
    )

    # لا ستوب قريب جداً
    if risk < 0.7 * atr:

        sl = (
            price
            - direction * 0.7 * atr
        )

        risk = 0.7 * atr

    # ولا ستوب بعيد بشكل مبالغ
    if risk > 3.5 * atr:
        return None

    raw_tp = setup.get(
        "tp_raw"
    )

    if (
        raw_tp is not None
        and (
            (
                direction == 1
                and raw_tp > price
            )
            or
            (
                direction == -1
                and raw_tp < price
            )
        )
    ):

        tp1 = float(
            raw_tp
        )

    else:

        tp1 = (
            price
            + direction * rr * risk
        )

    # لا نقبل TP ضعيف
    if abs(tp1 - price) < 1.2 * risk:

        tp1 = (
            price
            + direction * rr * risk
        )

    tp2 = (
        price
        + direction * max(
            2.0 * risk,
            rr * risk
        )
    )

    return {
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_p": risk / pip,
        "tp1_p": abs(
            tp1 - price
        ) / pip
    }


# =========================================================
# QUALITY
# =========================================================

def grade(score):

    if score >= EXCELLENT_SCORE:

        return (
            "ممتازة ⭐⭐⭐",
            "ممتازة"
        )

    if score >= GOOD_SCORE:

        return (
            "جيدة ⭐⭐",
            "جيدة"
        )

    return (
        "متوسطة ⭐",
        "متوسطة"
    )


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
            "sig": {}
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
            indent=1
        )


# =========================================================
# TELEGRAM
# =========================================================

def tg(text):

    print(
        "\n========== TELEGRAM =========="
    )

    print(text)

    if not TG_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN غير موجود"
        )

        return False

    if not TG_CHAT:

        print(
            "❌ TELEGRAM_CHAT_ID غير موجود"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TG_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            json={
                "chat_id": TG_CHAT,
                "text": text
            },
            timeout=20
        )

        print(
            "Telegram HTTP:",
            response.status_code
        )

        print(
            "Telegram response:",
            response.text[:500]
        )

        if response.status_code == 200:

            try:

                data = response.json()

                if data.get("ok") is True:

                    SENT.append(text)

                    print(
                        "✅ TELEGRAM MESSAGE SENT"
                    )

                    return True

            except Exception:

                pass

        print(
            "❌ TELEGRAM MESSAGE FAILED"
        )

        return False

    except Exception as e:

        print(
            "❌ TELEGRAM CONNECTION ERROR:",
            repr(e)
        )

        return False


def telegram_check():

    if not TG_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN غير موجود"
        )

        return False

    if not TG_CHAT:

        print(
            "❌ TELEGRAM_CHAT_ID غير موجود"
        )

        return False

    try:

        response = requests.get(
            (
                "https://api.telegram.org/"
                f"bot{TG_TOKEN}/getMe"
            ),
            timeout=15
        )

        print(
            "Telegram getMe:",
            response.status_code
        )

        print(
            "Telegram getMe response:",
            response.text[:500]
        )

        if response.status_code != 200:

            return False

        data = response.json()

        if not data.get("ok"):

            return False

        bot_name = (
            data
            .get("result", {})
            .get("username", "unknown")
        )

        print(
            f"✅ Telegram Bot OK: @{bot_name}"
        )

        return tg(
            "🤖 BOT STARTED\n\n"
            "✅ Telegram متصل بنجاح\n"
            "📊 XAU/USD: مراقبة M5 + M15\n"
            "🔒 صفقة واحدة فقط بنفس الوقت\n"
            "🧠 إدارة الصفقة + Early Exit مفعلة\n\n"
            "⏳ عم بفحص السوق..."
        )

    except Exception as e:

        print(
            "❌ Telegram check error:",
            repr(e)
        )

        return False


# =========================================================
# EARLY EXIT
# =========================================================

def reversal_reasons(
    df,
    trade,
    live
):

    d = prep(df)

    if len(d) < 40:
        return []

    atr = float(
        d["ATR"].iloc[-1]
    )

    direction = trade["dir"]

    risk = abs(
        trade["entry"]
        - trade["sl"]
    )

    if risk <= 0:
        return []

    current_r = (
        direction
        * (
            live
            - trade["entry"]
        )
        / risk
    )

    H, L = swings(
        d,
        2
    )

    bull, bear, body, rng = candle_info(d)

    reasons = []

    # -----------------------------------------------------
    # 1. كسر هيكل عكس الصفقة
    # -----------------------------------------------------

    if (
        direction == 1
        and L
    ):

        level = L[-1][1]

        if (
            d["Close"].iloc[-1]
            < level
            and
            d["Close"].iloc[-2]
            >= level
        ):

            reasons.append(
                "كسر هيكل هابط بعد الدخول"
            )

    if (
        direction == -1
        and H
    ):

        level = H[-1][1]

        if (
            d["Close"].iloc[-1]
            > level
            and
            d["Close"].iloc[-2]
            <= level
        ):

            reasons.append(
                "كسر هيكل صاعد بعد الدخول"
            )

    # -----------------------------------------------------
    # 2. شمعة زخم قوية عكس الصفقة
    # -----------------------------------------------------

    if (
        direction == 1
        and bear
        and body >= 0.8 * atr
    ):

        reasons.append(
            "شمعة زخم قوية عكس الشراء"
        )

    if (
        direction == -1
        and bull
        and body >= 0.8 * atr
    ):

        reasons.append(
            "شمعة زخم قوية عكس البيع"
        )

    # -----------------------------------------------------
    # 3. كسر EMA50 مع الاتجاه المعاكس
    # -----------------------------------------------------

    ema_now = float(
        d["EMA50"].iloc[-1]
    )

    ema_prev = float(
        d["EMA50"].iloc[-2]
    )

    if (
        direction == 1
        and d["Close"].iloc[-1] < ema_now
        and d["Close"].iloc[-2] >= ema_prev
    ):

        reasons.append(
            "كسر EMA50 عكس صفقة الشراء"
        )

    if (
        direction == -1
        and d["Close"].iloc[-1] > ema_now
        and d["Close"].iloc[-2] <= ema_prev
    ):

        reasons.append(
            "كسر EMA50 عكس صفقة البيع"
        )

    # -----------------------------------------------------
    # 4. إذا وصلت الصفقة لمنطقة سلبية قوية
    # -----------------------------------------------------

    if current_r <= -0.6:

        reasons.append(
            f"السعر وصل {current_r:+.2f}R مع علامات انعكاس"
        )

    # نحتاج أكثر من دليل
    if len(reasons) >= EARLY_EXIT_SCORE:

        return reasons

    # حالة خاصة:
    # كسر هيكل + شمعة قوية يكفيان
    if (
        len(reasons) >= 2
        and (
            "كسر هيكل"
            in " ".join(reasons)
        )
    ):

        return reasons

    return []


# =========================================================
# CHECK OPEN TRADE
# =========================================================

def check_open(
    st,
    m5s,
    frames,
    now,
    day
):

    still_open = []

    for trade in st["open"]:

        symbol = trade["sym"]

        df = m5s.get(
            symbol
        )

        if df is None:

            still_open.append(
                trade
            )

            continue

        trade_time = pd.Timestamp(
            trade["time"]
        )

        live = float(
            df["Close"].iloc[-1]
        )

        risk = abs(
            trade["entry"]
            - trade["sl"]
        )

        if risk <= 0:
            risk = 1e-9

        result = None

        # -------------------------------------------------
        # فحص TP / SL
        # -------------------------------------------------

        candles_after = df[
            df.index > trade_time
        ]

        for _, c in candles_after.iterrows():

            if trade["dir"] == 1:

                sl_hit = (
                    c["Low"]
                    <= trade["sl"]
                )

                tp_hit = (
                    c["High"]
                    >= trade["tp1"]
                )

            else:

                sl_hit = (
                    c["High"]
                    >= trade["sl"]
                )

                tp_hit = (
                    c["Low"]
                    <= trade["tp1"]
                )

            # إذا لمس الاثنين بنفس الشمعة
            # نعتبر الستوب أولاً للتحفظ
            if sl_hit:

                result = "SL"

                break

            if tp_hit:

                result = "TP1"

                break

        current_r = (
            trade["dir"]
            * (
                live
                - trade["entry"]
            )
            / risk
        )

        # -------------------------------------------------
        # TP
        # -------------------------------------------------

        if result == "TP1":

            day["wins"] += 1

            day["r"] += float(
                trade.get(
                    "rr",
                    RR
                )
            )

            tg(
                "✅ TP1 HIT\n"
                f"{symbol} {trade['tf']}\n"
                f"{trade['name']}\n\n"
                f"Entry: {trade['entry']:.2f}\n"
                f"TP1: {trade['tp1']:.2f}\n\n"
                "🤖 الصفقة أُغلقت حسابياً.\n"
                "🔎 البوت رجع يدور على Setup جديد."
            )

            continue

        # -------------------------------------------------
        # SL
        # -------------------------------------------------

        if result == "SL":

            day["losses"] += 1

            day["r"] -= 1

            tg(
                "❌ SL HIT\n"
                f"{symbol} {trade['tf']}\n"
                f"{trade['name']}\n\n"
                f"Entry: {trade['entry']:.2f}\n"
                f"SL: {trade['sl']:.2f}\n\n"
                "🤖 الصفقة انتهت.\n"
                "🔎 البوت رجع يدور على Setup جديد."
            )

            continue

        # -------------------------------------------------
        # انتهاء زمني
        # -------------------------------------------------

        age_hours = (
            now - trade_time
        ).total_seconds() / 3600

        if age_hours >= EXPIRE_HOURS:

            tg(
                "⌛ EXPIRED\n"
                f"{symbol} {trade['tf']}\n"
                f"{trade['name']}\n\n"
                f"مدة الصفقة: {age_hours:.1f} ساعة\n"
                f"النتيجة الحالية: {current_r:+.2f}R\n\n"
                "البوت أغلقها حسابياً ورجع يراقب السوق."
            )

            continue

        # -------------------------------------------------
        # EARLY EXIT
        # -------------------------------------------------

        frame = (
            frames
            .get(symbol, {})
            .get(
                trade.get(
                    "tf",
                    "5min"
                )
            )
        )

        reasons = []

        if frame is not None:

            reasons = reversal_reasons(
                frame["df"],
                trade,
                live
            )

        if reasons:

            day["early"] += 1

            day["r"] += current_r

            if current_r > 0.05:

                day["wins"] += 1

            elif current_r < -0.05:

                day["losses"] += 1

            tg(
                "🚨 EARLY EXIT\n"
                f"{symbol} {trade['tf']}\n"
                f"{trade['name']}\n\n"
                f"Entry: {trade['entry']:.2f}\n"
                f"Current: {live:.2f}\n"
                f"Result: {current_r:+.2f}R\n\n"
                "أسباب الانعكاس:\n"
                + "\n".join(
                    "• " + x
                    for x in reasons
                )
                + "\n\n"
                "🛑 الصفقة أُغلقت حسابياً.\n"
                "🔒 لن يفتح البوت صفقة عكسية قبل انتهاء هذه الصفقة."
            )

            continue

        # -------------------------------------------------
        # الصفقة ما زالت مفتوحة
        # -------------------------------------------------

        still_open.append(
            trade
        )

    st["open"] = still_open


# =========================================================
# FETCH DATA
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

        data = response.json()

    except Exception as e:

        LAST_ERR["msg"] = str(e)[:300]

        return None

    if "values" not in data:

        LAST_ERR["msg"] = str(
            data.get(
                "message",
                data
            )
        )[:300]

        print(
            "DATA ERROR:",
            LAST_ERR["msg"]
        )

        return None

    df = pd.DataFrame(
        data["values"]
    )

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )

    for c in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[c] = df[c].astype(
            float
        )

    df = (
        df
        .sort_values("datetime")
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
            "Close"
        ]
    ]


# =========================================================
# RESAMPLE
# =========================================================

def rs(
    df,
    rule
):

    if rule == "5min":
        return df

    return (
        df
        .resample(rule)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last"
            }
        )
        .dropna()
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "=" * 60
    )

    print(
        "🤖 XAU/USD BOT STARTING"
    )

    print(
        "=" * 60
    )

    # -----------------------------------------------------
    # STATE
    # -----------------------------------------------------

    st = load()

    st.setdefault(
        "open",
        []
    )

    st.setdefault(
        "last",
        {}
    )

    st.setdefault(
        "sig",
        {}
    )

    # -----------------------------------------------------
    # TIME
    # -----------------------------------------------------

    now = pd.Timestamp(
        datetime.now(
            timezone.utc
        ).replace(
            tzinfo=None
        )
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    day = (
        st
        .setdefault(
            "days",
            {}
        )
        .setdefault(
            today,
            {
                "sent": 0,
                "wins": 0,
                "losses": 0,
                "early": 0,
                "r": 0.0,
                "summary": False
            }
        )
    )

    day.setdefault(
        "early",
        0
    )

    # -----------------------------------------------------
    # TELEGRAM STARTUP
    # -----------------------------------------------------

    last_start = st.get(
        "startup_msg"
    )

    should_startup = (
        not last_start
        or
        (
            now
            - pd.Timestamp(
                last_start
            )
        ).total_seconds()
        >= 21600
    )

    if should_startup:

        if telegram_check():

            st["startup_msg"] = (
                now.isoformat()
            )

    # -----------------------------------------------------
    # TWELVE DATA
    # -----------------------------------------------------

    if not TD_KEY:

        print(
            "❌ TWELVE_DATA_API_KEY غير موجود"
        )

        tg(
            "🔴 BOT ERROR\n\n"
            "TWELVE_DATA_API_KEY غير موجود "
            "داخل GitHub Secrets."
        )

        save(st)

        sys.exit(0)

    # -----------------------------------------------------
    # FETCH
    # -----------------------------------------------------

    m5_data = {}

    for symbol in SYMBOLS:

        df = fetch(
            symbol
        )

        if (
            df is not None
            and len(df) > 500
        ):

            m5_data[
                symbol
            ] = df

        time.sleep(
            1
        )

    # -----------------------------------------------------
    # DATA ERROR
    # -----------------------------------------------------

    if not m5_data:

        tg(
            "⚠️ DATA ERROR\n\n"
            "البوت اشتغل، لكن Twelve Data "
            "ما رجّع بيانات للسوق.\n\n"
            f"{LAST_ERR['msg']}"
        )

        save(st)

        return

    # -----------------------------------------------------
    # FRAMES
    # -----------------------------------------------------

    frames = {}

    for symbol, df in m5_data.items():

        m5_completed = (
            df.iloc[:-1]
        )

        m15 = rs(
            df,
            "15min"
        )

        m15_completed = (
            m15.iloc[:-1]
        )

        frames[
            symbol
        ] = {

            "5min": {
                "df":
                    m5_completed
            },

            "15min": {
                "df":
                    m15_completed
            }
        }

    # -----------------------------------------------------
    # MANAGE OPEN TRADES FIRST
    # -----------------------------------------------------

    check_open(
        st,
        m5_data,
        frames,
        now,
        day
    )

    # -----------------------------------------------------
    # إذا بقيت صفقة مفتوحة
    # لا نفتح أي صفقة جديدة
    # -----------------------------------------------------

    if st["open"]:

        print(
            "🔒 Open trade exists. "
            "No new trade."
        )

        save(st)

        return

    # -----------------------------------------------------
    # DAILY LIMITS
    # -----------------------------------------------------

    if day["losses"] >= MAX_LOSSES:

        print(
            "Daily loss limit reached."
        )

        save(st)

        return

    if day["sent"] >= MAX_PER_DAY:

        print(
            "Daily trade limit reached."
        )

        save(st)

        return

    # -----------------------------------------------------
    # SCAN M5 + M15
    # -----------------------------------------------------

    candidates = []

    for symbol, symbol_frames in frames.items():

        pip = PIPS.get(
            symbol,
            0.0001
        )

        live = float(
            m5_data[
                symbol
            ]["Close"].iloc[-1]
        )

        for timeframe, frame in symbol_frames.items():

            d = frame["df"]

            if len(d) < 80:
                continue

            setups, ctx = scan(
                d,
                timeframe,
                pip
            )

            # M5 يحتاج فلترة أقوى
            required_score = (
                MIN_SCORE
                + (
                    1
                    if timeframe == "5min"
                    else 0
                )
            )

            for setup in setups:

                score = float(
                    setup["score"]
                )

                if score < required_score:
                    continue

                # -----------------------------------------
                # منع الإشارة المكررة
                # -----------------------------------------

                candle_time = (
                    d.index[-1]
                    .isoformat()
                )

                signal_key = (
                    f"{symbol}|"
                    f"{timeframe}|"
                    f"{setup['name']}|"
                    f"{setup['dir']}|"
                    f"{candle_time}"
                )

                if signal_key in st["sig"]:

                    continue

                # -----------------------------------------
                # بناء الصفقة
                # -----------------------------------------

                trade = build(
                    setup,
                    live,
                    ctx["atr"],
                    pip,
                    RR
                )

                if trade is None:

                    continue

                quality, quality_key = grade(
                    score
                )

                setup.update(
                    trade
                )

                setup["sym"] = symbol
                setup["tf"] = timeframe
                setup["key"] = signal_key
                setup["quality"] = quality
                setup["quality_key"] = quality_key

                candidates.append(
                    setup
                )

    # -----------------------------------------------------
    # اختيار أفضل فرصة واحدة
    # -----------------------------------------------------

    if candidates:

        best = max(
            candidates,
            key=lambda x: (
                x["score"],
                1 if x["tf"] == "15min" else 0
            )
        )

        side = (
            "شراء 🟢"
            if best["dir"] == 1
            else "بيع 🔴"
        )

        tags_text = ""

        if best.get("tags"):

            tags_text = "\n".join(
                "• " + x
                for x in best["tags"]
            )

        message = (
            f"⚡ {side} — {best['sym']}\n\n"

            f"⭐ الجودة: "
            f"{best['quality']}\n"

            f"📊 الفريم: "
            f"{best['tf']}\n"

            f"🎯 الاستراتيجية: "
            f"{best['name']}\n\n"

            f"Entry: "
            f"{best['entry']:.2f}\n"

            f"SL: "
            f"{best['sl']:.2f} "
            f"({best['risk_p']:.0f} نقطة)\n"

            f"TP1: "
            f"{best['tp1']:.2f} "
            f"({best['tp1_p']:.0f} نقطة)\n"

            f"TP2: "
            f"{best['tp2']:.2f}\n\n"

            f"🧠 سبب الصفقة:\n"
            f"{best['why']}\n\n"

            f"{tags_text}\n\n"

            "🔒 إدارة الصفقة:\n"
            "• لا توجد صفقة عكسية أثناء فتح هذه الصفقة.\n"
            "• البوت يراقب TP / SL.\n"
            "• إذا ظهر انعكاس قوي، يرسل EARLY EXIT.\n"
            "• بعد انتهاء الصفقة يرجع يبحث مباشرة.\n\n"

            f"📈 صفقات اليوم: "
            f"{day['sent'] + 1}/{MAX_PER_DAY}"
        )

        tg(
            message
        )

        # -------------------------------------------------
        # تخزين الصفقة
        # -------------------------------------------------

        st["open"].append(
            {
                "sym":
                    best["sym"],

                "dir":
                    best["dir"],

                "entry":
                    best["entry"],

                "sl":
                    best["sl"],

                "tp1":
                    best["tp1"],

                "tp2":
                    best["tp2"],

                "rr":
                    RR,

                "name":
                    best["name"],

                "tf":
                    best["tf"],

                "time":
                    now.isoformat(),

                "quality":
                    best["quality_key"]
            }
        )

        st["sig"][
            best["key"]
        ] = now.isoformat()

        day["sent"] += 1

        st["last"][
            best["sym"]
        ] = now.isoformat()

    # -----------------------------------------------------
    # NO SETUP
    # -----------------------------------------------------

    else:

        last_heartbeat = st.get(
            "heartbeat"
        )

        should_heartbeat = (
            not last_heartbeat
            or
            (
                now
                - pd.Timestamp(
                    last_heartbeat
                )
            ).total_seconds()
            >= HEARTBEAT_HOURS * 3600
        )

        if should_heartbeat:

            tg(
                "🫀 BOT ALIVE\n\n"
                "✅ البوت شغال\n"
                "📊 XAU/USD: M5 + M15\n"
                "🔎 تم فحص السوق\n"
                "❌ لا يوجد Setup مطابق لشروطنا حالياً\n\n"
                f"📈 صفقات اليوم: "
                f"{day['sent']}/{MAX_PER_DAY}\n"
                f"❌ خسائر: "
                f"{day['losses']}/{MAX_LOSSES}\n"
                f"🕐 {now:%H:%M} UTC"
            )

            st["heartbeat"] = (
                now.isoformat()
            )

    # -----------------------------------------------------
    # تنظيف الإشارات القديمة
    # -----------------------------------------------------

    cleaned = {}

    for key, value in st["sig"].items():

        try:

            age = (
                now
                - pd.Timestamp(value)
            ).total_seconds()

            if age < 86400:

                cleaned[
                    key
                ] = value

        except Exception:

            pass

    st["sig"] = cleaned

    # -----------------------------------------------------
    # DAILY SUMMARY
    # -----------------------------------------------------

    if (
        now.hour >= 21
        and not day.get(
            "summary",
            False
        )
    ):

        tg(
            f"📊 DAILY SUMMARY\n\n"
            f"التاريخ: {today}\n"
            f"الصفقات: {day['sent']}\n"
            f"الرابحة: {day['wins']}\n"
            f"الخاسرة: {day['losses']}\n"
            f"Early Exit: {day['early']}\n"
            f"صافي R: {day['r']:+.1f}"
        )

        day["summary"] = True

    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    save(st)

    print(
        "=" * 60
    )

    print(
        "✅ BOT FINISHED"
    )

    print(
        "=" * 60
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
