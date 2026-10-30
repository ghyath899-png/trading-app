"""بوت الذهب XAU/USD على GitHub Actions - شغّال من افتتاح السوق لإغلاقه (ذهب بس)
السيناريوهات (كلها بتأكيد، مافي دخول على أول لمسة):
  1) سحب سيولة: السعر بيسحب قمة/قاع منطقة قوية وبيرجع، والدخول بعد شمعة بتكسر شمعة السحب
  2) تجميع ثم سحب سيولة: نطاق أفقي ضيق، سحب فوقه/تحته، رجوع جواه وكسر شمعة السحب
  3) ارتداد: لمسة منطقة قوية + شمعة رفض واضحة + شمعة تأكيد بنفس الاتجاه
  4) كسر حقيقي + إعادة اختبار: شمعة كسر قوية، بعدين رجعة بتثبت
الستوب: خلف قمة/قاع السيناريو. الهدف: عند أقرب حاجز/قمة/قاع منطقي. الرسالة: دخول/ستوب/هدف بس
عند نهاية الصفقة بيبعت السيناريو والسبب (وإذا خسرت شو ما كان متوفر)
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests


def env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def envf(name, default):
    return float(env(name, str(default)))


def envi(name, default):
    return int(float(env(name, str(default))))


TD_KEY = env("TWELVE_DATA_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")
SYMBOLS = [s.strip() for s in env("SYMBOLS", "XAU/USD").split(",") if s.strip()]
PIPS = {"XAU/USD": 0.1, "GBP/JPY": 0.01, "USD/JPY": 0.01, "EUR/USD": 0.0001, "GBP/USD": 0.0001, "BTC/USD": 1.0}
# الأرقام النفسية: (رقم نفسي عادي، رقم نفسي كبير)
PSY = {"XAU/USD": (10.0, 50.0), "EUR/USD": (0.0050, 0.0100), "GBP/USD": (0.0050, 0.0100),
       "USD/JPY": (0.50, 1.00), "GBP/JPY": (0.50, 1.00)}
# السبريد بالبيب (الذهب: 14 سنت ≈ 1.4 بيب، حاطينه 1.5)
SPREADS = {"XAU/USD": 1.5, "EUR/USD": 1, "GBP/USD": 1.5, "USD/JPY": 1, "GBP/JPY": 2}
USE_M1 = env("USE_M1", "1") == "1"            # شموع الدقيقة لمتابعة الأهداف بدقة

# ---- المناطق ----
ZONE_HISTORY = envi("ZONE_HISTORY", 2000)
PIV_LEN = envi("PIV_LEN", 6)
MERGE_ATR = envf("MERGE_ATR", 0.6)
MAXH_ATR = envf("MAXH_ATR", 1.2)
PAD_ATR = envf("PAD_ATR", 0.10)
MIN_BOUNCE = envi("MIN_BOUNCE", 2)
REACT_BARS = envi("REACT_BARS", 40)
BIG_REACT = envf("BIG_REACT", 4.0)
TOUCH_GAP = envi("TOUCH_GAP", 6)
BRK_ATR = envf("BRK_ATR", 0.30)
MIN_ZONE_SCORE = envi("MIN_ZONE_SCORE", 6)    # 7 = ★★ ، 12 = ★★★
BOUNCE_MIN_SCORE = envi("BOUNCE_MIN_SCORE", 8)  # الارتداد بده منطقة أقوى من السحب
WALL_MIN_SCORE = envi("WALL_MIN_SCORE", 8)      # أقل قوة لمنطقة تعتبر حاجز قدام الهدف
PSY_STEP = envf("PSY_STEP", 10.0)
PSY_MAJOR = envf("PSY_MAJOR", 50.0)
H1_PIV = envi("H1_PIV", 4)
H1_HISTORY = envi("H1_HISTORY", 1000)
H1_MIN_SCORE = envi("H1_MIN_SCORE", 5)

# ---- شروط الشموع ----
TOL_ATR = envf("TOL_ATR", 0.15)
PIERCE_ATR = envf("PIERCE_ATR", 0.40)         # أقل عمق لسحب السيولة
SW_MAX = envf("SW_MAX", 2.5)
WICK_MIN = envf("WICK_MIN", 0.40)
BODY_MIN = envf("BODY_MIN", 0.45)
MIN_RANGE_ATR = envf("MIN_RANGE_ATR", 0.5)
BRK_BODY = envf("BRK_BODY", 0.50)
BRK_RANGE = envf("BRK_RANGE", 0.8)
SPIKE_ATR = envf("SPIKE_ATR", 4.0)
RETEST_BARS = envi("RETEST_BARS", 16)
RETEST_RUN = envf("RETEST_RUN", 0.5)
RETEST_TOUCH = envf("RETEST_TOUCH", 0.35)
MIN_CONFIRM = envi("MIN_CONFIRM", 2)          # من 3: الساعة، 4 ساعات، المنطقة على الساعة

# ---- تجميع ----
ACC_MIN_BARS = envi("ACC_MIN_BARS", 8)
ACC_MAX_BARS = envi("ACC_MAX_BARS", 24)
ACC_MIN_ATR = envf("ACC_MIN_ATR", 0.6)        # أقل عرض (ارتفاع) للنطاق
ACC_MAX_ATR = envf("ACC_MAX_ATR", 2.2)        # أكبر ارتفاع للنطاق (أفقي مو عمودي)
ACC_EDGE_TOUCH = envi("ACC_EDGE_TOUCH", 2)    # كم مرة لازم تلمس كل حافة
ACC_PIERCE = envf("ACC_PIERCE", 0.15)
ACC_SCORE = envi("ACC_SCORE", 7)
ACC_BONUS = envi("ACC_BONUS", 20)             # أفضلية لسيناريو التجميع

# ---- اندفاع ثم تراجع مع الاتجاه (شغّال) ----
PB_ON = env("PB_ON", "1") == "1"
IMP_ATR = envf("IMP_ATR", 1.6)
IMP_MAX_ATR = envf("IMP_MAX_ATR", 4.0)
IMP_BODY = envf("IMP_BODY", 0.60)
IMP_LOOK = envi("IMP_LOOK", 20)
PB_BARS = envi("PB_BARS", 12)
PB_MIN = envf("PB_MIN", 0.30)
PB_MAX = envf("PB_MAX", 0.70)

# ---- الستوب والهدف ----
STOP_BUF_ATR = envf("STOP_BUF_ATR", 0.25)     # مسافة خلف قمة/قاع السيناريو (غير السبريد)
MIN_RISK_ATR = envf("MIN_RISK_ATR", 0.8)      # ستوب أصغر من هيك بينضرب بالعشوائية، بنوسعه
MAX_RISK_ATR = envf("MAX_RISK_ATR", 2.5)      # أكبر من هيك الصفقة بتنلغي
MIN_RR = envf("MIN_RR", 1.5)                  # أقل نسبة ربح/خسارة
MAX_RR = envf("MAX_RR", 4.0)
DEFAULT_RR = envf("DEFAULT_RR", 2.0)          # إذا ما في حاجز/قمة قدام السعر
TP_BUF_ATR = envf("TP_BUF_ATR", 0.10)         # بنحط الهدف قبل الحاجز بشوي
BE_FRAC = envf("BE_FRAC", 0.5)                # بعد هالنسبة من المسافة للهدف بنحرك الستوب للدخول
MIN_TP_PIPS = {"XAU/USD": envf("MIN_TP_PIPS", 60)}  # أقل هدف (60 بيب = 6$)
SPREAD_PIPS = envf("SPREAD_PIPS", 3)

# ---- حدود وتنبيهات ----
MAX_PER_DAY = envi("MAX_PER_DAY", 4)
MAX_LOSSES = envi("MAX_LOSSES", 2)
SESSION_ON = env("SESSION_ON", "0") == "1"
SESSION_START = envi("SESSION_START", 7)
SESSION_END = envi("SESSION_END", 20)
QUIET_ON = env("QUIET_ON", "0") == "1"        # بالساعات الهادية بنطلب تأكيد كامل (3/3)
QUIET_START = envi("QUIET_START", 21)         # UTC
QUIET_END = envi("QUIET_END", 7)              # UTC (لندن بتفتح تقريباً 7 UTC)
MAX_AGE_MIN = envi("MAX_AGE_MIN", 40)
GAP_MIN = envi("GAP_MIN", 70)
LOOKBACK = envi("LOOKBACK", 2)
ZONE_LOCK_HOURS = envf("ZONE_LOCK_HOURS", 1.0)
EXPIRE_HOURS = envf("EXPIRE_HOURS", 8)
MIN_PROFIT_R = envf("MIN_PROFIT_R", 0.4)
REV_MIN_REASONS = envi("REV_MIN_REASONS", 2)
HEARTBEAT_HOURS = envf("HEARTBEAT_HOURS", 6)
STATE_FILE = "state.json"

KIND_AR = {"BOUNCE": "ارتداد", "SWEEP": "سحب سيولة", "ACC": "تجميع وسحب سيولة", "RETEST": "كسر وإعادة اختبار",
           "PULLBACK": "اندفاع وتراجع"}
SENT = []
LAST_ERR = {"msg": ""}
DIAG = {}
DIAG_LABELS = {
    "raw": "إشارات خام مكتشفة", "low_confirm": "مرفوض: تأكيد الفريمات ناقص", "quiet": "مرفوض: ساعة هادية بدون تأكيد كامل",
    "stop_wrong_side": "مرفوض: الستوب بالاتجاه الغلط", "risk_far": "مرفوض: الستوب بعيد",
    "no_room": "مرفوض: حاجز قوي قريب قدام الهدف", "rr_low": "مرفوض: الهدف قريب نسبةً للستوب",
    "chase": "مرفوض: السعر ابتعد (مطاردة)", "stale": "الشمعة قديمة (السوق مسكّر؟)",
    "zone_locked": "المنطقة مستخدمة قبل شوي", "after_gap": "أول شموع بعد افتتاح السوق (انتظار)",
    "pb_no_trend": "اندفاع وتراجع: ضد اتجاه الساعة و4 ساعات",
}


def dg(k, n=1):
    DIAG[k] = DIAG.get(k, 0) + n


def fmt(x, pip):
    return f"{x:,.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"


def dist_txt(d, pip, sym):
    p = d / pip
    return f"{p:.0f} بيب ≈ {d:.1f}$" if sym == "XAU/USD" else f"{p:.1f} بيب"


p2 = lambda v: f"{v:,.2f}"


# ---------------- البيانات ----------------
def fetch(symbol, interval, size):
    try:
        j = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
            "symbol": symbol, "interval": interval, "outputsize": size,
            "timezone": "UTC", "apikey": TD_KEY}).json()
    except Exception as e:
        LAST_ERR["msg"] = str(e)[:200]
        return None
    if "values" not in j:
        LAST_ERR["msg"] = str(j.get("message", j))[:200]
        print(symbol, interval, "ERROR:", LAST_ERR["msg"])
        return None
    df = pd.DataFrame(j["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("datetime").drop_duplicates("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]
    return df[["Open", "High", "Low", "Close"]]


def closed(df, minutes, now):
    return df[df.index + pd.Timedelta(minutes=minutes) <= now]


def rs_complete(d15c, rule, minutes):
    if len(d15c) == 0:
        return d15c
    r = d15c.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    last_end = d15c.index[-1] + pd.Timedelta(minutes=15)
    return r[r.index + pd.Timedelta(minutes=minutes) <= last_end]


def add_atr(d, n=14):
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - d["Close"].shift()).abs(),
                    (d["Low"] - d["Close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def ema_dir(d, fast, slow):
    if len(d) < slow + 5:
        return 0
    ef = d["Close"].ewm(span=fast, adjust=False).mean().iloc[-1]
    es = d["Close"].ewm(span=slow, adjust=False).mean().iloc[-1]
    return 1 if ef > es else -1 if ef < es else 0


def h1_side(h1):
    if len(h1) < 60:
        return 0
    e = h1["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
    return 1 if h1["Close"].iloc[-1] > e else -1


def last_gap_pos(win):
    """موضع أول شمعة بعد آخر إغلاق للسوق (فجوة أكبر من GAP_MIN دقيقة). 0 = ما في فجوة"""
    if len(win) < 3:
        return 0
    dm = np.diff(win.index.values).astype("timedelta64[m]").astype(np.int64)
    g = np.flatnonzero(dm > GAP_MIN)
    return int(g[-1]) + 1 if len(g) else 0


def key_levels(d):
    keys = {}
    try:
        today = d.index[-1].normalize()
        agg = {"High": "max", "Low": "min"}
        day = d.resample("1D").agg(agg).dropna()
        day = day[day.index < today]
        if len(day):
            keys["PDH"], keys["PDL"] = float(day["High"].iloc[-1]), float(day["Low"].iloc[-1])
        wk = d.resample("W").agg(agg).dropna()
        wk = wk[wk.index < today]
        if len(wk):
            keys["PWH"], keys["PWL"] = float(wk["High"].iloc[-1]), float(wk["Low"].iloc[-1])
        mo = d.resample("MS").agg(agg).dropna()
        mo = mo[mo.index < today.replace(day=1)]
        if len(mo):
            keys["PMH"], keys["PML"] = float(mo["High"].iloc[-1]), float(mo["Low"].iloc[-1])
    except Exception as e:
        print("key levels failed:", e)
    return keys


# ---------------- المناطق ----------------
def pivots(d, L):
    H = d["High"].values
    Lw = d["Low"].values
    if len(H) < 2 * L + 3:
        return np.array([], dtype=int), np.array([], dtype=int)
    hmax = pd.Series(H).rolling(2 * L + 1, center=True).max().values
    lmin = pd.Series(Lw).rolling(2 * L + 1, center=True).min().values
    return np.flatnonzero(H == hmax), np.flatnonzero(Lw == lmin)


def build_zones(d, atr, keys, piv, psy=None):
    """مناطق من قمم وقيعان حقيقية: لمسات، ارتدادات، دور (دعم/مقاومة)، انكسار، قوة"""
    psy_step, psy_major = psy if psy else (PSY_STEP, PSY_MAJOR)
    n = len(d)
    if not atr or atr != atr or atr <= 0 or n < 2 * piv + 20:
        return []
    keys = keys or {}
    H = d["High"].values
    Lw = d["Low"].values
    C = d["Close"].values
    ph, pl = pivots(d, piv)
    pts = []
    for i in ph:
        e = min(i + REACT_BARS, n - 1)
        pts.append((float(H[i]), 1, int(i), float((H[i] - Lw[i:e + 1].min()) / atr)))
    for i in pl:
        e = min(i + REACT_BARS, n - 1)
        pts.append((float(Lw[i]), -1, int(i), float((H[i:e + 1].max() - Lw[i]) / atr)))
    if not pts:
        return []
    pts.sort(key=lambda x: x[0])

    clusters = []
    cur = None
    for p, ty, bi, rc in pts:
        if cur is not None and (p - cur["hi"] > MERGE_ATR * atr or p - cur["lo"] > MAXH_ATR * atr):
            clusters.append(cur)
            cur = None
        if cur is None:
            cur = {"lo": p, "hi": p, "sw": 0, "fi": bi, "ft": ty, "re": 0.0}
        cur["hi"] = p
        cur["sw"] += 1
        cur["re"] = max(cur["re"], rc)
        if bi < cur["fi"]:
            cur["fi"], cur["ft"] = bi, ty
    if cur is not None:
        clusters.append(cur)

    pad, brk = PAD_ATR * atr, BRK_ATR * atr
    out = []
    for z in clusters:
        if not (z["sw"] >= MIN_BOUNCE or z["re"] >= BIG_REACT):
            continue
        top, bot = z["hi"] + pad, z["lo"] - pad
        idx = np.flatnonzero((H >= bot) & (Lw <= top))
        touches = 0 if len(idx) == 0 else 1 + int((np.diff(idx) > TOUCH_GAP).sum())
        role = -1 if z["ft"] == 1 else 1
        flips, lfi, lfd = 0, -1, 0
        fi = z["fi"]
        ev = sorted([(int(j), 1) for j in np.flatnonzero(C > top + brk) if j > fi] +
                    [(int(j), -1) for j in np.flatnonzero(C < bot - brk) if j > fi])
        for j, e in ev:
            if role == -1 and e == 1:
                role, flips, lfi, lfd = 1, flips + 1, j, 1
            elif role == 1 and e == -1:
                role, flips, lfi, lfd = -1, flips + 1, j, -1
        score = touches + 2 * z["sw"] + int(round(min(z["re"], 8.0) / 2))
        tags = []
        if math.floor(top / psy_major) * psy_major >= bot:
            score += 2
            tags.append("رقم نفسي كبير")
        elif math.floor(top / psy_step) * psy_step >= bot:
            score += 1
            tags.append("رقم نفسي")
        for nm, kv in keys.items():
            if bot - 0.1 * atr <= kv <= top + 0.1 * atr:
                score += 3
                tags.append(nm)
        out.append({"top": top, "bot": bot, "mid": (top + bot) / 2, "sw": z["sw"], "touches": touches,
                    "role": role, "flips": flips, "lfi": lfi, "lfd": lfd, "score": score,
                    "stars": 3 if score >= 12 else 2 if score >= 7 else 1, "tags": tags, "re": z["re"]})
    out.sort(key=lambda z: z["mid"])
    return out


def h1_overlap(z, h1z, atr):
    for hz in h1z:
        if hz["score"] >= H1_MIN_SCORE and hz["bot"] - 0.3 * atr <= z["top"] and hz["top"] + 0.3 * atr >= z["bot"]:
            return True
    return False


def near_zone(px, zones, atr):
    for z in zones:
        if z["score"] >= MIN_ZONE_SCORE and z["bot"] - 0.3 * atr <= px <= z["top"] + 0.3 * atr:
            return True
    return False


# ---------------- كشف السيناريوهات: المناطق (الشمعة الأخيرة في win، والمناطق مبنية قبلها) ----------------
def detect(win, zones, atr):
    out = []
    n = len(win)
    if n < 30 or not atr:
        return out
    o, h, l, c = (win[k].values for k in ("Open", "High", "Low", "Close"))
    i = n - 1
    rng = h[i] - l[i]
    if rng <= 0:
        return out
    body = abs(c[i] - o[i])
    up_w = h[i] - max(o[i], c[i])
    lo_w = min(o[i], c[i]) - l[i]
    bull, bear = c[i] > o[i], c[i] < o[i]
    tol = TOL_ATR * atr
    big = rng >= MIN_RANGE_ATR * atr
    strong = body >= BODY_MIN * rng

    # شمعة الرفض (الشمعة قبل الأخيرة) للارتداد
    j = i - 1
    rj = h[j] - l[j]
    okj = rj > 0 and rj >= MIN_RANGE_ATR * atr
    if okj:
        uwj = h[j] - max(o[j], c[j])
        lwj = min(o[j], c[j]) - l[j]
        bengj = c[j - 1] < o[j - 1] and c[j] > o[j] and o[j] <= c[j - 1] and c[j] >= o[j - 1]
        sengj = c[j - 1] > o[j - 1] and c[j] < o[j] and o[j] >= c[j - 1] and c[j] <= o[j - 1]

    def add(kind, name, dr, z, anchor, why):
        out.append({"kind": kind, "name": name, "dir": dr, "zone": z, "anchor": float(anchor), "why": why,
                    "ci": float(c[i]), "info": "", "area": (z["bot"], z["top"])})

    for z in zones:
        if z["score"] < MIN_ZONE_SCORE:
            continue
        if h[i] < z["bot"] - 3 * atr or l[i] > z["top"] + 3 * atr:
            continue
        top, bot, mid = z["top"], z["bot"], z["mid"]
        age = (i - z["lfi"]) if z["flips"] > 0 else None
        if age is not None and age < 2:
            continue
        recent = age is not None and age <= RETEST_BARS

        # ---- كسر حقيقي ثم إعادة اختبار ----
        if recent:
            k = z["lfi"]
            bk = h[k] - l[k]
            real_brk = (bk >= BRK_RANGE * atr and bk <= SPIKE_ATR * atr and abs(c[k] - o[k]) >= BRK_BODY * bk
                        and ((c[k] > o[k]) == (z["lfd"] == 1)))
            if not real_brk:
                continue
            if z["lfd"] == 1 and z["role"] == 1:
                held = bool((c[k + 1:i] >= bot - 0.15 * atr).all())
                run = h[k:i].max() - top
                touch = l[i] <= top + RETEST_TOUCH * atr and c[i] > top
                if held and run >= RETEST_RUN * atr and touch and bull and big and (lo_w >= 0.3 * rng or body >= 0.5 * rng):
                    add("RETEST", "كسر مقاومة وإعادة اختبارها كدعم", 1, z, min(l[i], bot),
                        [f"شمعة قوية كسرت المقاومة {p2(top)}", "السعر رجع اختبرها وثبت فوقها وطلعت شمعة رفض صاعدة"])
            elif z["lfd"] == -1 and z["role"] == -1:
                held = bool((c[k + 1:i] <= top + 0.15 * atr).all())
                run = bot - l[k:i].min()
                touch = h[i] >= bot - RETEST_TOUCH * atr and c[i] < bot
                if held and run >= RETEST_RUN * atr and touch and bear and big and (up_w >= 0.3 * rng or body >= 0.5 * rng):
                    add("RETEST", "كسر دعم وإعادة اختباره كمقاومة", -1, z, max(h[i], top),
                        [f"شمعة قوية كسرت الدعم {p2(bot)}", "السعر رجع اختبره ورُفض منه وطلعت شمعة رفض هابطة"])
            continue

        if z["role"] == 1:     # دعم
            done = False
            for jj in (i - 1, i - 2):
                ext = float(l[jj:i + 1].min())
                pierce = bot - ext
                if not (PIERCE_ATR * atr < pierce <= SW_MAX * atr):
                    continue
                if c[jj:i].min() < bot - 0.15 * atr:      # إغلاق تحت الدعم = كسر مو سحب
                    continue
                if bull and big and strong and c[i] > max(float(h[jj:i].max()), bot):
                    add("SWEEP", "سحب سيولة تحت الدعم", 1, z, ext,
                        [f"السعر سحب السيولة تحت الدعم {p2(bot)} ({pierce / atr:.1f} ATR) ورجع فوقه",
                         "شمعة التأكيد أغلقت فوق قمة شمعة السحب (انعكاس مؤكد)"])
                    done = True
                    break
            if done:
                continue
            if (okj and z["score"] >= BOUNCE_MIN_SCORE and bot - PIERCE_ATR * atr <= l[j] <= top + tol and c[j] > mid
                    and (lwj >= WICK_MIN * rj or bengj) and bull and big and strong and c[i] > c[j]
                    and l[i] >= bot - 0.1 * atr):
                add("BOUNCE", "ارتداد من دعم قوي", 1, z, min(l[j], l[i], bot),
                    [f"دعم قوي {p2(bot)} - {p2(top)} ({z['touches']} لمسات)",
                     "شمعة رفض واضحة ثم شمعة تأكيد صاعدة أغلقت أعلى منها"])
        else:                  # مقاومة
            done = False
            for jj in (i - 1, i - 2):
                ext = float(h[jj:i + 1].max())
                pierce = ext - top
                if not (PIERCE_ATR * atr < pierce <= SW_MAX * atr):
                    continue
                if c[jj:i].max() > top + 0.15 * atr:
                    continue
                if bear and big and strong and c[i] < min(float(l[jj:i].min()), top):
                    add("SWEEP", "سحب سيولة فوق المقاومة", -1, z, ext,
                        [f"السعر سحب السيولة فوق المقاومة {p2(top)} ({pierce / atr:.1f} ATR) ورجع تحتها",
                         "شمعة التأكيد أغلقت تحت قاع شمعة السحب (انعكاس مؤكد)"])
                    done = True
                    break
            if done:
                continue
            if (okj and z["score"] >= BOUNCE_MIN_SCORE and bot - tol <= h[j] <= top + PIERCE_ATR * atr and c[j] < mid
                    and (uwj >= WICK_MIN * rj or sengj) and bear and big and strong and c[i] < c[j]
                    and h[i] <= top + 0.1 * atr):
                add("BOUNCE", "ارتداد من مقاومة قوية", -1, z, max(h[j], h[i], top),
                    [f"مقاومة قوية {p2(bot)} - {p2(top)} ({z['touches']} لمسات)",
                     "شمعة رفض واضحة ثم شمعة تأكيد هابطة أغلقت أدنى منها"])
    return out


# ---------------- كشف السيناريوهات: تجميع ثم سحب سيولة ----------------
def find_range(h, l, c, end, atr):
    """نطاق تجميع أفقي ضيق بينتهي قبل الشمعة end (غير شاملة). بيرجع أطول نافذة مطابقة"""
    for nb in range(ACC_MAX_BARS, ACC_MIN_BARS - 1, -1):
        s = end - nb
        if s < 0:
            continue
        hh, ll = float(h[s:end].max()), float(l[s:end].min())
        w = hh - ll
        if not (ACC_MIN_ATR * atr <= w <= ACC_MAX_ATR * atr):
            continue
        if abs(c[end - 1] - c[s]) > 0.6 * w:        # لازم أفقي مو ماشي باتجاه
            continue
        eh = int((h[s:end] >= hh - 0.25 * w).sum())
        el = int((l[s:end] <= ll + 0.25 * w).sum())
        if eh >= ACC_EDGE_TOUCH and el >= ACC_EDGE_TOUCH:
            return {"hi": hh, "lo": ll, "n": nb, "w": w}
    return None


def detect_acc(win, atr):
    out = []
    n = len(win)
    if n < ACC_MAX_BARS + 6 or not atr:
        return out
    o, h, l, c = (win[k].values for k in ("Open", "High", "Low", "Close"))
    i = n - 1
    rng = h[i] - l[i]
    if rng <= 0:
        return out
    body = abs(c[i] - o[i])
    bull, bear = c[i] > o[i], c[i] < o[i]
    big = rng >= MIN_RANGE_ATR * atr
    strong = body >= BODY_MIN * rng
    for jj in (i - 1, i - 2):
        r = find_range(h, l, c, jj, atr)
        if not r:
            continue
        hi, lo = r["hi"], r["lo"]
        z = {"top": hi, "bot": lo, "mid": (hi + lo) / 2, "score": ACC_SCORE, "stars": 2, "touches": r["n"],
             "sw": 0, "tags": ["تجميع"], "role": 0}
        ext_h = float(h[jj:i + 1].max())
        ext_l = float(l[jj:i + 1].min())
        if (ACC_PIERCE * atr < ext_h - hi <= SW_MAX * atr and c[jj:i].max() <= hi + 0.15 * atr
                and bear and big and strong and c[i] < min(float(l[jj:i].min()), hi)):
            out.append({"kind": "ACC", "name": "تجميع ثم سحب سيولة فوق النطاق (بيع)", "dir": -1, "zone": z,
                        "anchor": ext_h, "ci": float(c[i]), "info": "", "area": (lo, hi),
                        "why": [f"تجميع أفقي {r['n']} شمعة بين {p2(lo)} و{p2(hi)}",
                                "السعر سحب السيولة فوق النطاق ورجع جواه وكسر قاع شمعة السحب"]})
            break
        if (ACC_PIERCE * atr < lo - ext_l <= SW_MAX * atr and c[jj:i].min() >= lo - 0.15 * atr
                and bull and big and strong and c[i] > max(float(h[jj:i].max()), lo)):
            out.append({"kind": "ACC", "name": "تجميع ثم سحب سيولة تحت النطاق (شراء)", "dir": 1, "zone": z,
                        "anchor": ext_l, "ci": float(c[i]), "info": "", "area": (lo, hi),
                        "why": [f"تجميع أفقي {r['n']} شمعة بين {p2(lo)} و{p2(hi)}",
                                "السعر سحب السيولة تحت النطاق ورجع جواه وكسر قمة شمعة السحب"]})
            break
    return out


# ---------------- كشف السيناريوهات: اندفاع ثم تراجع (مطفي افتراضياً) ----------------
def detect_pullback(win, atr):
    out = []
    n = len(win)
    if n < IMP_LOOK + PB_BARS + 5 or not atr:
        return out
    o, h, l, c = (win[k].values for k in ("Open", "High", "Low", "Close"))
    i = n - 1
    rng_i = h[i] - l[i]
    if rng_i <= 0 or rng_i < MIN_RANGE_ATR * atr:
        return out
    body_i = abs(c[i] - o[i])
    up_w = h[i] - max(o[i], c[i])
    lo_w = min(o[i], c[i]) - l[i]
    gp = last_gap_pos(win)
    kmin = max(i - PB_BARS, IMP_LOOK, gp + 1 if gp > 0 else 0)
    for k in range(i - 2, kmin - 1, -1):
        rk = h[k] - l[k]
        if rk <= 0 or not (IMP_ATR * atr <= rk <= IMP_MAX_ATR * atr) or abs(c[k] - o[k]) < IMP_BODY * rk:
            continue
        if c[k] > o[k] and c[k] > h[k - IMP_LOOK:k].max():
            start = l[k]
            peak = h[k:i].max()
            low_pb = l[k + 1:i + 1].min()
            leg = peak - start
            depth = (peak - low_pb) / leg if leg > 0 else 0.0
            if PB_MIN <= depth <= PB_MAX and c[k + 1:i + 1].min() > start and l[i] <= low_pb + 0.3 * atr \
                    and c[i] > o[i] and c[i] < peak and (body_i >= BODY_MIN * rng_i or lo_w >= WICK_MIN * rng_i):
                out.append({"kind": "PULLBACK", "name": "اندفاع صاعد ثم تراجع (شراء مع الاتجاه)", "dir": 1, "zone": None,
                            "anchor": float(low_pb), "ci": float(c[i]), "info": "", "area": None,
                            "why": [f"اندفاع {rk / atr:.1f} ATR كسر أعلى {IMP_LOOK} شمعة",
                                    f"تراجع {depth * 100:.0f}% من الحركة وشمعة تأكيد صاعدة"]})
                break
        elif c[k] < o[k] and c[k] < l[k - IMP_LOOK:k].min():
            start = h[k]
            trough = l[k:i].min()
            high_pb = h[k + 1:i + 1].max()
            leg = start - trough
            depth = (high_pb - trough) / leg if leg > 0 else 0.0
            if PB_MIN <= depth <= PB_MAX and c[k + 1:i + 1].max() < start and h[i] >= high_pb - 0.3 * atr \
                    and c[i] < o[i] and c[i] > trough and (body_i >= BODY_MIN * rng_i or up_w >= WICK_MIN * rng_i):
                out.append({"kind": "PULLBACK", "name": "اندفاع هابط ثم تراجع (بيع مع الاتجاه)", "dir": -1, "zone": None,
                            "anchor": float(high_pb), "ci": float(c[i]), "info": "", "area": None,
                            "why": [f"اندفاع {rk / atr:.1f} ATR كسر أدنى {IMP_LOOK} شمعة",
                                    f"ارتداد {depth * 100:.0f}% من الحركة وشمعة تأكيد هابطة"]})
                break
    return out


# ---------------- خطة الصفقة: ستوب وهدف من الهيكل ----------------
def make_plan(s, live, atr, pip, zones, spread, keys, swings, min_tp):
    dr, z = s["dir"], s["zone"]
    sl = s["anchor"] - dr * (STOP_BUF_ATR * atr + spread * pip)
    if (dr == 1 and sl >= live) or (dr == -1 and sl <= live):
        return None, "stop_wrong_side"
    risk = abs(live - sl)
    floor = MIN_RISK_ATR * atr
    if risk < floor:                       # ستوب ضيق زيادة = بينضرب دغري، بنوسعه
        sl, risk = live - dr * floor, floor
    if risk > MAX_RISK_ATR * atr:
        return None, "risk_far"

    buf = TP_BUF_ATR * atr

    def ahead(p):
        d_ = (p - live) * dr
        return d_ if d_ > 0.2 * atr else None

    walls = []                             # حواجز: مناطق قوية ومستويات مفتاحية قدام السعر
    for o_ in zones:
        if o_ is z or o_["score"] < WALL_MIN_SCORE:
            continue
        d_ = ahead(o_["bot"] if dr == 1 else o_["top"])
        if d_ is not None:
            walls.append(d_)
    for kv in (keys or {}).values():
        d_ = ahead(kv)
        if d_ is not None:
            walls.append(d_)
    need = max(MIN_RR * risk, min_tp)
    wall = min(walls) if walls else None
    if wall is not None and wall - buf < need:
        return None, "no_room"
    cands = []                             # أهداف: أقرب قمة/قاع منطقي بيحقق الحد الأدنى
    for sp in swings:
        d_ = ahead(sp)
        if d_ is not None and d_ - buf >= need:
            cands.append(d_ - buf)
    if wall is not None:
        cands.append(wall - buf)
    tp_d = min(cands) if cands else max(DEFAULT_RR * risk, min_tp)
    tp_d = min(tp_d, max(MAX_RR * risk, need))
    if tp_d < need - 1e-9:
        return None, "rr_low"
    return {"entry": live, "sl": sl, "tp": live + dr * tp_d, "be": live + dr * BE_FRAC * tp_d,
            "risk": risk, "tp_d": tp_d, "rr": tp_d / risk}, ""


def build_ctx(sym, df15, df1, now):
    d15c = closed(df15, 15, now)
    if len(d15c) < 500:
        return None
    pip = PIPS.get(sym, 0.0001)
    psy = PSY.get(sym, (PSY_STEP, PSY_MAJOR))
    live = float(df1["Close"].iloc[-1]) if df1 is not None and len(df1) else float(df15["Close"].iloc[-1])
    h1 = rs_complete(d15c, "1h", 60)
    h4 = rs_complete(d15c, "4h", 240)
    atr_s = add_atr(d15c)
    atr = float(atr_s.iloc[-1])
    keys = key_levels(d15c)
    h1_atr = float(add_atr(h1).iloc[-1]) if len(h1) > 40 else 0.0
    h1z = build_zones(h1.iloc[-H1_HISTORY:], h1_atr, {}, H1_PIV, psy) if h1_atr > 0 else []
    zones = build_zones(d15c.iloc[-ZONE_HISTORY:], atr, keys, PIV_LEN, psy)
    w = d15c.iloc[-400:]
    ph, pl = pivots(w, 4)
    swings = [float(w["High"].values[k]) for k in ph] + [float(w["Low"].values[k]) for k in pl]
    age_min = (now - (d15c.index[-1] + pd.Timedelta(minutes=15))).total_seconds() / 60
    return {"sym": sym, "pip": pip, "psy": psy, "df15": df15, "df1": df1, "d15c": d15c, "live": live, "atr": atr,
            "atr_s": atr_s, "keys": keys, "h1": h1, "h1d": ema_dir(h1, 50, 200), "h4d": ema_dir(h4, 20, 50),
            "h1side": h1_side(h1), "h1z": h1z, "zones": zones, "swings": swings, "fresh": age_min <= MAX_AGE_MIN}


def evaluate(ctx, off, now):
    sym, pip, d, live = ctx["sym"], ctx["pip"], ctx["d15c"], ctx["live"]
    n = len(d) - off
    if n < 400:
        return []
    dd = d.iloc[:n]
    end = dd.index[-1] + pd.Timedelta(minutes=15)
    if (now - end).total_seconds() / 60 > MAX_AGE_MIN:
        dg("stale")
        return []
    atr = float(ctx["atr_s"].iloc[n - 1])
    if not atr > 0:
        return []
    win = dd.iloc[-(ZONE_HISTORY + 1):]
    gp = last_gap_pos(win)
    if gp > 0 and len(win) - 1 - gp < 2:
        dg("after_gap")
        return []
    zones = build_zones(win.iloc[:-1], atr, ctx["keys"], PIV_LEN, ctx["psy"])
    ctime = dd.index[-1].isoformat()
    spread = SPREADS.get(sym, SPREAD_PIPS)
    min_tp = MIN_TP_PIPS.get(sym, 0.0) * pip
    quiet = QUIET_ON and (now.hour >= QUIET_START or now.hour < QUIET_END)
    sigs = detect(win, zones, atr) + detect_acc(win, atr)
    if PB_ON:
        sigs += detect_pullback(win, atr)
    out = []
    for s in sigs:
        dg("raw")
        z, dr = s["zone"], s["dir"]
        h1ok, h4ok = ctx["h1d"] == dr, ctx["h4d"] == dr
        if z is not None:
            zok = h1_overlap(z, ctx["h1z"], atr)
            zmiss = "المنطقة على فريم الساعة"
            zscore = z["score"]
            zk = f"Z|{sym}|{int(round(z['mid'] / max(0.5 * atr, pip)))}|{dr}"
        else:
            if not (h1ok or h4ok):
                dg("pb_no_trend")
                continue
            zok = near_zone(s["anchor"], zones, atr)
            zmiss = "التراجع عند منطقة قوية"
            zscore = 0
            zk = f"P|{sym}|{int(round(s['anchor'] / max(atr, pip)))}|{dr}"
        n_ok = int(h1ok) + int(h4ok) + int(zok)
        if n_ok < MIN_CONFIRM:
            dg("low_confirm")
            continue
        if quiet and n_ok < 3:
            dg("quiet")
            continue
        plan, why = make_plan(s, live, atr, pip, zones, spread, ctx["keys"], ctx["swings"], min_tp)
        if plan is None:
            dg(why)
            continue
        moved = dr * (live - s["ci"])
        if moved > 0.4 * plan["risk"] or moved < -0.5 * plan["risk"]:
            dg("chase")
            continue
        miss = ([] if h1ok else ["اتجاه الساعة"]) + ([] if h4ok else ["اتجاه 4 ساعات"]) + ([] if zok else [zmiss])
        s.update({
            "off": off, "plan": plan, "atr": atr, "n_ok": n_ok, "miss": miss, "quiet": bool(quiet),
            "rank": n_ok * 100 + zscore + (ACC_BONUS if s["kind"] == "ACC" else 0),
            "key": f"{sym}|{s['kind']}|{dr}|{ctime}", "zk": zk})
        out.append(s)
    return out


# ---------------- الحالة وتيليجرام ----------------
def load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": [], "days": {}, "sig": {}}


def save(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def tg(text):
    print(text)
    SENT.append(text)
    if not (TG_TOKEN and TG_CHAT):
        print("TELEGRAM_BOT_TOKEN او TELEGRAM_CHAT_ID فاضيين - تأكد من أسماء الـ secrets بالـ workflow")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text}, timeout=15)
        if r.status_code != 200:
            print("TELEGRAM ERROR", r.status_code, r.text[:300])
        else:
            print("telegram sent OK")
    except Exception as e:
        print("telegram failed:", e)


def sym_day(day, sym):
    return day.setdefault("ps", {}).setdefault(sym, {"sent": 0, "losses": 0})


def book(t, realized, day):
    w = day["t"].setdefault(t["kind"], [0, 0])
    day["r"] += realized
    if realized > 0.05:
        day["wins"] += 1
        w[0] += 1
    elif realized < -0.05:
        day["losses"] += 1
        w[1] += 1
        sym_day(day, t["sym"])["losses"] += 1


def migrate(t):
    """صفقات الإصدار القديم (v2) بتتحول لتنكمل مراقبتها"""
    if not isinstance(t, dict):
        return None
    if t.get("v") == 3:
        return t
    if t.get("v") == 2 and "tp2" in t:
        t = dict(t)
        t.update({"v": 3, "tp": t["tp2"], "be": t["tp1"], "rr": t.get("r2", 2.0), "tp_d": abs(t["tp2"] - t["entry"]),
                  "why": [], "miss": [], "quiet": False, "legacy": True})
        return t
    return None


def entry_msg(c, sym, pip):
    p = c["plan"]
    side = "شراء 🟢" if c["dir"] == 1 else "بيع 🔴"
    area = f"\nالمنطقة: {fmt(c['area'][0], pip)} – {fmt(c['area'][1], pip)}" if c.get("area") else ""
    return (f"{side} — {sym}{area}\n"
            f"الدخول: {fmt(p['entry'], pip)}\n"
            f"الستوب: {fmt(p['sl'], pip)} ({dist_txt(p['risk'], pip, sym)})\n"
            f"الهدف: {fmt(p['tp'], pip)} ({dist_txt(p['tp_d'], pip, sym)})")


def scn_txt(t):
    w = "\n".join("- " + x for x in (t.get("why") or []))
    return f"السيناريو: {t['name']}" + (f"\n{w}" if w else "")


def loss_note(t):
    if t.get("legacy"):
        return ""
    n = []
    if t.get("miss"):
        n.append("ما كان متوفر: " + "، ".join(t["miss"]))
    if t.get("quiet"):
        n.append("الدخول كان بساعة هادية")
    return "\n".join(n) if n else "كل التأكيدات كانت متوفرة، السوق ببساطة عاكسها."


# ---------------- متابعة الصفقات ----------------
def track(t, ctx):
    """بيعيد حساب الصفقة من الدخول: المرحلة (0/1) والنتيجة (SL/BE/TP/None). الستوب أولاً إذا تزامن (تحفظاً)"""
    t0 = pd.Timestamp(t["time"])
    df1, df15 = ctx["df1"], ctx["df15"]
    if df1 is not None and len(df1):
        cs = df1[df1.index >= t0.floor("1min") + pd.Timedelta(minutes=1)]
    else:
        cs = df15[df15.index >= t0.ceil("15min")]
    dr, stage, res = t["dir"], 0, None
    for r in cs.itertuples():
        hi, lo = r.High, r.Low
        hit_tp = (hi >= t["tp"]) if dr == 1 else (lo <= t["tp"])
        if stage == 0:
            if (lo <= t["sl"]) if dr == 1 else (hi >= t["sl"]):
                res = "SL"
                break
            if (hi >= t["be"]) if dr == 1 else (lo <= t["be"]):
                stage = 1
                if hit_tp:
                    res = "TP"
                    break
        else:
            if (lo <= t["entry"]) if dr == 1 else (hi >= t["entry"]):
                res = "BE"
                break
            if hit_tp:
                res = "TP"
                break
    return stage, res


def reversal_reasons(t, ctx, cands):
    d = ctx["d15c"]
    if d.index[-1] < pd.Timestamp(t["time"]).ceil("15min"):
        return []
    dr, atr = t["dir"], ctx["atr"]
    last = d.iloc[-1]
    lo_, hi_, op_, cl_ = last["Low"], last["High"], last["Open"], last["Close"]
    rng = hi_ - lo_
    reasons = []
    w = d.iloc[-300:]
    ph, pl = pivots(w, 3)
    C = w["Close"].values
    if dr == 1 and len(pl):
        lv = float(w["Low"].values[pl[-1]])
        if lv > t["sl"] and C[-1] < lv - 0.05 * atr and C[-4:-1].max() >= lv:
            reasons.append("كسر هيكل عكسي: شمعة 15 دقيقة أغلقت تحت آخر قاع")
    if dr == -1 and len(ph):
        lv = float(w["High"].values[ph[-1]])
        if lv < t["sl"] and C[-1] > lv + 0.05 * atr and C[-4:-1].min() <= lv:
            reasons.append("كسر هيكل عكسي: شمعة 15 دقيقة أغلقت فوق آخر قمة")
    if rng > 0 and rng >= atr and abs(cl_ - op_) >= 0.6 * rng and ((dr == 1 and cl_ < op_) or (dr == -1 and cl_ > op_)):
        reasons.append("شمعة زخم قوية عكس الصفقة")
    if rng > 0:
        for z in ctx["zones"]:
            if z["score"] < MIN_ZONE_SCORE:
                continue
            if dr == 1 and z["bot"] > t["entry"] and hi_ >= z["bot"] - 0.1 * atr and (hi_ - max(op_, cl_)) >= 0.5 * rng and cl_ < z["top"]:
                reasons.append("رفض عند منطقة مقاومة قوية فوق السعر")
                break
            if dr == -1 and z["top"] < t["entry"] and lo_ <= z["top"] + 0.1 * atr and (min(op_, cl_) - lo_) >= 0.5 * rng and cl_ > z["bot"]:
                reasons.append("رفض عند منطقة دعم قوية تحت السعر")
                break
    if any(c["dir"] == -dr for c in cands):
        reasons.append("ظهر سيناريو معاكس مؤكد")
    if t.get("h1side") == dr and ctx["h1side"] == -dr:
        reasons.append("فريم الساعة أغلق ضد الصفقة")
    return reasons


def manage(st, sym, ctx, cands, now, day):
    pip, live = ctx["pip"], ctx["live"]
    still = []
    for t in st["open"]:
        if t["sym"] != sym:
            still.append(t)
            continue
        side = "شراء" if t["dir"] == 1 else "بيع"
        risk = t["risk"]
        stage, res = track(t, ctx)
        r_now = t["dir"] * (live - t["entry"]) / risk

        if stage >= 1 and t.get("stage", 0) < 1:
            t["stage"] = 1
            if res is None:
                tg(f"🔒 {sym} ({side}): السعر قطع نص المسافة للهدف\n"
                   f"حرّك الستوب لسعر الدخول {fmt(t['entry'], pip)}")

        if res == "TP":
            book(t, t["rr"], day)
            tg(f"✅ ربحت — {sym} ({side})\n"
               f"{dist_txt(abs(t['tp'] - t['entry']), pip, sym)} ({t['rr']:+.1f}R)\n\n" + scn_txt(t))
            continue
        if res == "BE":
            book(t, 0.0, day)
            tg(f"↩️ تعادل — {sym} ({side})\n"
               f"السعر قطع نص المسافة وبعدين رجع للدخول، سكّرت بدون خسارة.\n\n" + scn_txt(t))
            continue
        if res == "SL":
            book(t, -1.0, day)
            note = loss_note(t)
            tg(f"❌ خسرت — {sym} ({side})\n"
               f"-{dist_txt(risk, pip, sym)} (-1R)\n\n" + scn_txt(t) + (f"\n\n{note}" if note else ""))
            continue

        # الصفقة بتنتهي بعد EXPIRE_HOURS ساعات سوق مفتوح (الإغلاق ما بينحسب)
        age = int((ctx["d15c"].index >= pd.Timestamp(t["time"]).ceil("15min")).sum())
        if age >= EXPIRE_HOURS * 4:
            book(t, r_now, day)
            tg(f"⌛ انتهت الصفقة بدون هدف أو ستوب بعد {EXPIRE_HOURS:g} ساعات — {sym} ({side})\n"
               f"النتيجة عند السعر الحالي: {r_now:+.1f}R\n\n" + scn_txt(t))
            continue

        # تنبيه التسكير: بس وهي بربح
        stg = t.get("stage", 0)
        if (r_now >= MIN_PROFIT_R) or (stg >= 1 and r_now > 0.1):
            reasons = reversal_reasons(t, ctx, cands)
            if len(reasons) >= REV_MIN_REASONS:
                book(t, r_now, day)
                gain = abs(live - t["entry"])
                tg(f"⚠️ سكّر هلأ — {sym} ({side})\n"
                   f"بربح {dist_txt(gain, pip, sym)} ({r_now:+.1f}R) والسوق عم يعكس:\n- " + "\n- ".join(reasons) + "\n\n"
                   + scn_txt(t))
                continue
        still.append(t)
    st["open"] = still


def try_open(st, sym, ctx, cands, now, day):
    if any(t["sym"] == sym for t in st["open"]):
        return "في صفقة مفتوحة عم يراقبها"
    sd = sym_day(day, sym)
    if sd["losses"] >= MAX_LOSSES:
        return f"وصل حد الخسائر اليومي ({MAX_LOSSES})"
    if sd["sent"] >= MAX_PER_DAY:
        return f"وصل حد الإشارات اليومي ({MAX_PER_DAY})"
    if not ctx["fresh"]:
        return "السوق مسكّر حالياً"
    if SESSION_ON and not (SESSION_START <= now.hour < SESSION_END):
        return f"خارج ساعات التداول ({SESSION_START}:00 - {SESSION_END}:00 UTC)"
    ok = []
    for c in cands:
        if c["key"] in st["sig"]:
            continue
        zt = st["sig"].get(c["zk"])
        if zt and (now - pd.Timestamp(zt)).total_seconds() < ZONE_LOCK_HOURS * 3600:
            dg("zone_locked")
            continue
        ok.append(c)
    if not ok:
        return "ما في سيناريو مؤكد حالياً"
    best = max(ok, key=lambda x: (x["rank"], -x["off"]))
    p, pip = best["plan"], ctx["pip"]
    tg(entry_msg(best, sym, pip))
    st["open"].append({"v": 3, "sym": sym, "dir": best["dir"], "entry": p["entry"], "sl": p["sl"], "tp": p["tp"],
                       "be": p["be"], "risk": p["risk"], "tp_d": p["tp_d"], "rr": p["rr"], "name": best["name"],
                       "why": best["why"], "miss": best["miss"], "quiet": best["quiet"],
                       "kind": KIND_AR[best["kind"]], "time": now.isoformat(), "stage": 0, "h1side": ctx["h1side"]})
    st["sig"][best["key"]] = now.isoformat()
    st["sig"][best["zk"]] = now.isoformat()
    day["sent"] += 1
    sd["sent"] += 1
    return ""


def zones_text(ctx):
    px, pip, sym = ctx["live"], ctx["pip"], ctx["sym"]
    sz = [z for z in ctx["zones"] if z["score"] >= MIN_ZONE_SCORE]
    up = sorted([z for z in sz if z["bot"] > px], key=lambda z: z["bot"] - px)[:2]
    dn = sorted([z for z in sz if z["top"] < px], key=lambda z: px - z["top"])[:2]
    lines = [f"فوق: {fmt(z['mid'], pip)} {'★' * z['stars']} {'مقاومة' if z['role'] == -1 else 'دعم'} (+{dist_txt(z['bot'] - px, pip, sym)})" for z in up]
    lines += [f"تحت: {fmt(z['mid'], pip)} {'★' * z['stars']} {'دعم' if z['role'] == 1 else 'مقاومة'} (-{dist_txt(px - z['top'], pip, sym)})" for z in dn]
    return "\n".join(lines) if lines else "ما في مناطق قوية قريبة"


# ---------------- التشغيل ----------------
def main():
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        tg("⚠️ مفتاح TWELVE_DATA_API_KEY غير موجود بالـ secrets")
        sys.exit(0)
    DIAG.clear()
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    for k, dflt in (("open", []), ("days", {}), ("sig", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    st["open"] = [m for m in (migrate(t) for t in st["open"]) if m and m.get("sym") in SYMBOLS]
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("summary", False)):
        day.setdefault(k, v)
    day.setdefault("t", {})
    day.setdefault("ps", {})
    for k in list(st["days"].keys())[:-30]:
        del st["days"][k]
    st["sig"] = {k: v for k, v in st["sig"].items() if (now - pd.Timestamp(v)).total_seconds() < 86400}
    st.setdefault("last_msg", now.isoformat())

    ctxs = {}
    for sym in SYMBOLS:
        df15 = fetch(sym, "15min", 5000)
        time.sleep(1.2)
        df1 = fetch(sym, "1min", 900) if USE_M1 else None
        time.sleep(1.2)
        if df15 is None or len(df15) < 600:
            continue
        c = build_ctx(sym, df15, df1, now)
        if c:
            ctxs[sym] = c
    if not ctxs:
        last = st.get("err_time")
        if not last or (now - pd.Timestamp(last)).total_seconds() > 3600:
            tg(f"⚠️ البوت ما قدر يجيب بيانات Twelve Data:\n{LAST_ERR['msg']}")
            st["err_time"] = now.isoformat()
        save(st)
        return

    reasons = {}
    for sym, ctx in ctxs.items():
        cands = []
        for off in range(LOOKBACK):
            cands += evaluate(ctx, off, now)
        manage(st, sym, ctx, cands, now, day)
        reasons[sym] = try_open(st, sym, ctx, cands, now, day)

    if now.hour >= 21 and not day["summary"]:
        tot = {}
        for dd_ in st["days"].values():
            for k, v in dd_.get("t", {}).items():
                a = tot.setdefault(k, [0, 0])
                a[0] += v[0]
                a[1] += v[1]
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | خاسرة: {day['losses']} | صافي: {day['r']:+.1f}R\n"
           "اليوم حسب السيناريو: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in day["t"].items()) or "لا يوجد")
           + f"\n\nآخر {len(st['days'])} يوم حسب السيناريو:\n"
           + ("\n".join(f"{k}: {v[0]}✅ / {v[1]}❌" for k, v in tot.items()) or "لا يوجد"))
        day["summary"] = True

    print("DIAG", DIAG)
    fresh_syms = [s for s, c in ctxs.items() if c["fresh"]]
    if fresh_syms and not SENT and (now - pd.Timestamp(st["last_msg"])).total_seconds() >= HEARTBEAT_HOURS * 3600:
        nm = lambda v: "صاعد" if v == 1 else "هابط" if v == -1 else "عرضي"
        dtxt = "\n".join(f"- {DIAG_LABELS.get(k, k)}: {v}" for k, v in sorted(DIAG.items(), key=lambda kv: -kv[1])[:5])
        parts = []
        for s0 in fresh_syms:
            c0 = ctxs[s0]
            parts.append(f"▪️ {s0}\nالسعر: {fmt(c0['live'], c0['pip'])}\n"
                         f"اتجاه الساعة: {nm(c0['h1d'])} | 4 ساعات: {nm(c0['h4d'])}\n"
                         f"الحالة: {reasons.get(s0) or 'ما في سيناريو مؤكد حالياً'}\n"
                         f"أقرب مناطق قوية:\n{zones_text(c0)}")
        tg(f"🫀 البوت شغال ({now:%H:%M} UTC)\n\n" + "\n\n".join(parts) + "\n\n"
           f"اليوم: إشارات {day['sent']} | رابحة {day['wins']} | خاسرة {day['losses']} | صافي {day['r']:+.1f}R"
           + (f"\n\nليش انرفضت إشارات:\n{dtxt}" if dtxt else ""))
    if SENT:
        st["last_msg"] = now.isoformat()
    save(st)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            st = load()
            now_ = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
            last_ = st.get("err_time")
            if not last_ or (now_ - pd.Timestamp(last_)).total_seconds() > 3600:
                tg("🚨 خطأ بالبوت (ابعت هالنص للمساعد):\n" + tb[-800:])
                st["err_time"] = now_.isoformat()
                save(st)
        except Exception as e2:
            print("failed to report error:", e2)
        sys.exit(0)
