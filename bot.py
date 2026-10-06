"""بوت الذهب XAU/USD على GitHub Actions
مناطق دعم/مقاومة قوية على M15: ارتداد، سحب سيولة، كسر، إعادة اختبار
ستوب دقيق مبني على الهيكل السعري والسيولة، أهداف منطقية ومدروسة"""
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
MIN_ZONE_SCORE = envi("MIN_ZONE_SCORE", 6)    
PSY_STEP = envf("PSY_STEP", 10.0)
PSY_MAJOR = envf("PSY_MAJOR", 50.0)
H1_PIV = envi("H1_PIV", 4)
H1_HISTORY = envi("H1_HISTORY", 1000)
H1_MIN_SCORE = envi("H1_MIN_SCORE", 5)

# ---- الإشارات ----
TOL_ATR = envf("TOL_ATR", 0.15)
PIERCE_ATR = envf("PIERCE_ATR", 0.40)         
SW_MIN = envf("SW_MIN", 0.10)
SW_MAX = envf("SW_MAX", 2.5)                  
WICK_MIN = envf("WICK_MIN", 0.40)
BODY_MIN = envf("BODY_MIN", 0.45)
MIN_RANGE_ATR = envf("MIN_RANGE_ATR", 0.5)
BRK_BUF = envf("BRK_BUF", 0.15)
BRK_BODY = envf("BRK_BODY", 0.50)
BRK_RANGE = envf("BRK_RANGE", 0.8)
SPIKE_ATR = envf("SPIKE_ATR", 4.0)            
RETEST_BARS = envi("RETEST_BARS", 16)
RETEST_RUN = envf("RETEST_RUN", 0.5)
RETEST_TOUCH = envf("RETEST_TOUCH", 0.35)
MIN_CONFIRM = envi("MIN_CONFIRM", 1)          

# ---- إعدادات الصفقة والستوب الدقيق ----
STOP_BUF_ATR = envf("STOP_BUF_ATR", 0.25)     # هامش أمان محسوب فوق/تحت نقطة الارتكاز الهيكلي
MIN_RISK_ATR = envf("MIN_RISK_ATR", 0.8)      # الحد الأدنى المنطقي للستوب حتى لا يضرب بالأسبريد
MAX_RISK_ATR = envf("MAX_RISK_ATR", 2.5)      # الحد الأقصى للستوب لضمان عدم تضخم المخاطرة
SPREAD_PIPS = envf("SPREAD_PIPS", 3)
TP1_R = envf("TP1_R", 1.0)                    # الهدف الأول 1:1 مضمون ومدروس
TP2_R = envf("TP2_R", 2.0)                    # الهدف الثاني 1:2 مبني على الهيكل
MIN_ROOM_R = envf("MIN_ROOM_R", 0.9)          # السماح بالمساحات المقبولة للوصول للأهداف

# ---- حدود وتنبيهات ----
MAX_PER_DAY = envi("MAX_PER_DAY", 8)          
MAX_LOSSES = envi("MAX_LOSSES", 3)
SESSION_ON = env("SESSION_ON", "1") == "1"
SESSION_START = envi("SESSION_START", 7)
SESSION_END = envi("SESSION_END", 20)
MAX_AGE_MIN = envi("MAX_AGE_MIN", 40)
LOOKBACK = envi("LOOKBACK", 2)
ZONE_LOCK_HOURS = envf("ZONE_LOCK_HOURS", 1.0) 
EXPIRE_HOURS = envf("EXPIRE_HOURS", 8)
MIN_PROFIT_R = envf("MIN_PROFIT_R", 0.4)
REV_MIN_REASONS = envi("REV_MIN_REASONS", 2)
HEARTBEAT_HOURS = envf("HEARTBEAT_HOURS", 6)
STATE_FILE = "state.json"

KIND_AR = {"BOUNCE": "ارتداد", "SWEEP": "سحب سيولة", "BREAK": "كسر", "RETEST": "إعادة اختبار"}
SENT = []
LAST_ERR = {"msg": ""}
DIAG = {}
DIAG_LABELS = {
    "raw": "إشارات خام مكتشفة", "low_confirm": "مرفوض: تأكيد الفريمات ناقص", "stop_wrong_side": "مرفوض: الستوب بالاتجاه الغلط",
    "risk_far": "مرفوض: الستوب بعيد", "no_room": "مرفوض: منطقة قوية معاكسة قريبة", "chase": "مرفوض: السعر ابتعد (مطاردة)",
    "stale": "الشمعة قديمة (السوق مسكّر؟)", "zone_locked": "المنطقة مستخدمة قبل شوي",
}


def dg(k, n=1):
    DIAG[k] = DIAG.get(k, 0) + n


def fmt(x, pip):
    return f"{x:,.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"


def dist_txt(d, pip, sym):
    p = d / pip
    return f"{p:.0f} بيب ≈ {d:.1f}$" if sym == "XAU/USD" else f"{p:.0f} بيب"


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


def build_zones(d, atr, keys, piv):
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
        if math.floor(top / PSY_MAJOR) * PSY_MAJOR >= bot:
            score += 2
            tags.append("رقم نفسي كبير")
        elif math.floor(top / PSY_STEP) * PSY_STEP >= bot:
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


# ---------------- كشف الإشارات ----------------
def detect(win, zones, atr):
    out = []
    n = len(win)
    if n < 10 or not atr:
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
    beng = c[i - 1] < o[i - 1] and bull and o[i] <= c[i - 1] and c[i] >= o[i - 1]
    seng = c[i - 1] > o[i - 1] and bear and o[i] >= c[i - 1] and c[i] <= o[i - 1]
    tol = TOL_ATR * atr
    big = rng >= MIN_RANGE_ATR * atr

    def add(kind, name, dr, z, anchor, why):
        out.append({"kind": kind, "name": name, "dir": dr, "zone": z, "anchor": float(anchor), "why": why, "ci": float(c[i])})

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

        if recent:
            k = z["lfi"]
            if z["lfd"] == 1 and z["role"] == 1:
                held = bool((c[k + 1:i] >= bot - 0.15 * atr).all())
                run = h[k:i].max() - top
                touch = l[i] <= top + RETEST_TOUCH * atr and c[i] > top
                if held and run >= RETEST_RUN * atr and touch and bull and big and (lo_w >= 0.3 * rng or body >= 0.5 * rng):
                    add("RETEST", "إعادة اختبار دعم بعد الكسر", 1, z, min(l[i], bot),
                        ["كسر المنطقة لفوق ثم رجع اختبرها وثبت فوقها", f"شمعة رفض صاعدة (ذيل سفلي {lo_w / rng * 100:.0f}%)"])
            elif z["lfd"] == -1 and z["role"] == -1:
                held = bool((c[k + 1:i] <= top + 0.15 * atr).all())
                run = bot - l[k:i].min()
                touch = h[i] >= bot - RETEST_TOUCH * atr and c[i] < bot
                if held and run >= RETEST_RUN * atr and touch and bear and big and (up_w >= 0.3 * rng or body >= 0.5 * rng):
                    add("RETEST", "إعادة اختبار مقاومة بعد الكسر", -1, z, max(h[i], top),
                        ["كسر المنطقة لتحت ثم رجع اختبرها ورُفض منها", f"شمعة رفض هابطة (ذيل علوي {up_w / rng * 100:.0f}%)"])
            continue

        if z["role"] == 1:
            ext = float(l[max(0, i - 2):i + 1].min())
            pierce = bot - ext
            if SW_MIN * atr <= pierce <= SW_MAX * atr and pierce > PIERCE_ATR * atr and c[i] > bot and bull and big \
                    and (body >= BODY_MIN * rng or lo_w >= WICK_MIN * rng):
                add("SWEEP", "سحب سيولة تحت الدعم", 1, z, ext,
                    [f"السعر نزل تحت الدعم بـ {pierce / atr:.1f} ATR وسكّر راجع فوقه (فخ للبائعين)"])
                continue
            touched = l[i] <= top + tol and l[i] >= bot - PIERCE_ATR * atr
            if touched and c[i] > mid and bull and big and (lo_w >= WICK_MIN * rng or beng):
                why = [f"ذيل رفض سفلي {lo_w / rng * 100:.0f}%"] if lo_w >= WICK_MIN * rng else []
                if beng:
                    why.append("شمعة ابتلاعية صاعدة")
                add("BOUNCE", "ارتداد من دعم قوي", 1, z, min(l[i], bot), why)
            if c[i - 1] >= bot - 0.05 * atr and c[i] < bot - BRK_BUF * atr and bear and body >= BRK_BODY * rng \
                    and BRK_RANGE * atr <= rng <= SPIKE_ATR * atr:
                add("BREAK", "كسر دعم قوي", -1, z, top,
                    [f"إغلاق شمعة قوية تحت الدعم (جسم {body / rng * 100:.0f}%)"])
        else:
            ext = float(h[max(0, i - 2):i + 1].max())
            pierce = ext - top
            if SW_MIN * atr <= pierce <= SW_MAX * atr and pierce > PIERCE_ATR * atr and c[i] < top and bear and big \
                    and (body >= BODY_MIN * rng or up_w >= WICK_MIN * rng):
                add("SWEEP", "سحب سيولة فوق المقاومة", -1, z, ext,
                    [f"السعر طلع فوق المقاومة بـ {pierce / atr:.1f} ATR وسكّر راجع تحتها (فخ للمشترين)"])
                continue
            touched = h[i] >= bot - tol and h[i] <= top + PIERCE_ATR * atr
            if touched and c[i] < mid and bear and big and (up_w >= WICK_MIN * rng or seng):
                why = [f"ذيل رفض علوي {up_w / rng * 100:.0f}%"] if up_w >= WICK_MIN * rng else []
                if seng:
                    why.append("شمعة ابتلاعية هابطة")
                add("BOUNCE", "ارتداد من مقاومة قوية", -1, z, max(h[i], top), why)
            if c[i - 1] <= top + 0.05 * atr and c[i] > top + BRK_BUF * atr and bull and body >= BRK_BODY * rng \
                    and BRK_RANGE * atr <= rng <= SPIKE_ATR * atr:
                add("BREAK", "كسر مقاومة قوية", 1, z, bot,
                    [f"إغلاق شمعة قوية فوق المقاومة (جسم {body / rng * 100:.0f}%)"])
    return out


# ---------------- حساب خطة الصفقة والستوب الدقيق ----------------
def make_plan(s, live, atr, pip, zones):
    dr, z = s["dir"], s["zone"]
    
    # حساب الستوب الدقيق بناءً على الـ Anchor (القمة/القاع أو سحب السيولة) مع هامش الأمان والأسبريد
    base_sl = s["anchor"] - dr * (STOP_BUF_ATR * atr + SPREAD_PIPS * pip)
    
    # ضمان عدم وقوع الستوب بالاتجاه الخاطئ
    if (dr == 1 and base_sl >= live) or (dr == -1 and base_sl <= live):
        return None, "stop_wrong_side"
        
    risk = abs(live - base_sl)
    
    # فلترة الستوب ليكون منطقياً (لا قريب جداً ولا بعيد جداً)
    min_risk = MIN_RISK_ATR * atr
    max_risk = MAX_RISK_ATR * atr
    
    if risk < min_risk:
        base_sl = live - dr * min_risk
        risk = min_risk
    elif risk > max_risk:
        return None, "risk_far"

    # التحقق من المساحة المتاحة والهدف بناءً على المناطق المقابلة
    edge = None
    for o_ in zones:
        if o_ is z or o_["score"] < MIN_ZONE_SCORE:
            continue
        if dr == 1 and o_["bot"] > live + 0.2 * atr:
            e = o_["bot"] - live
        elif dr == -1 and o_["top"] < live - 0.2 * atr:
            e = live - o_["top"]
        else:
            continue
        edge = e if edge is None else min(edge, e)
        
    if edge is not None and edge < MIN_ROOM_R * risk:
        return None, "no_room"

    # تحديد الأهداف المنطقية
    tp1d = TP1_R * risk
    tp2d = TP2_R * risk
    
    # إذا كانت المنطقة المقابلة قريبة، نجعل الهدف الثاني يتوافق معها بذكاء
    if edge is not None and edge - 0.1 * atr < tp2d:
        tp2d = max(edge - 0.1 * atr, 1.2 * risk)

    return {"entry": live, "sl": base_sl, "tp1": live + dr * tp1d, "tp2": live + dr * tp2d,
            "risk": risk, "r1": tp1d / risk, "r2": tp2d / risk}, ""


def build_ctx(sym, df15, df1, now):
    d15c = closed(df15, 15, now)
    if len(d15c) < 500:
        return None
    pip = PIPS.get(sym, 0.0001)
    live = float(df1["Close"].iloc[-1]) if df1 is not None and len(df1) else float(df15["Close"].iloc[-1])
    h1 = rs_complete(d15c, "1h", 60)
    h4 = rs_complete(d15c, "4h", 240)
    atr_s = add_atr(d15c)
    atr = float(atr_s.iloc[-1])
    keys = key_levels(d15c)
    h1_atr = float(add_atr(h1).iloc[-1]) if len(h1) > 40 else 0.0
    h1z = build_zones(h1.iloc[-H1_HISTORY:], h1_atr, {}, H1_PIV) if h1_atr > 0 else []
    zones = build_zones(d15c.iloc[-ZONE_HISTORY:], atr, keys, PIV_LEN)
    return {"sym": sym, "pip": pip, "df15": df15, "df1": df1, "d15c": d15c, "live": live, "atr": atr, "atr_s": atr_s,
            "keys": keys, "h1": h1, "h1d": ema_dir(h1, 50, 200), "h4d": ema_dir(h4, 20, 50), "h1side": h1_side(h1),
            "h1z": h1z, "zones": zones}


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
    zones = build_zones(win.iloc[:-1], atr, ctx["keys"], PIV_LEN)
    ctime = dd.index[-1].isoformat()
    nm = lambda v: "صاعد" if v == 1 else "هابط" if v == -1 else "عرضي"
    mk = lambda ok: "✓" if ok else "✗"
    out = []
    for s in detect(win, zones, atr):
        dg("raw")
        z, dr = s["zone"], s["dir"]
        h1ok, h4ok = ctx["h1d"] == dr, ctx["h4d"] == dr
        zok = h1_overlap(z, ctx["h1z"], atr)
        n_ok = int(h1ok) + int(h4ok) + int(zok)
        if n_ok < MIN_CONFIRM:
            dg("low_confirm")
            continue
        plan, why = make_plan(s, live, atr, pip, zones)
        if plan is None:
            dg(why)
            continue
        moved = dr * (live - s["ci"])
        if moved > 0.4 * plan["risk"] or moved < -0.5 * plan["risk"]:
            dg("chase")
            continue
        s.update({
            "off": off, "plan": plan, "atr": atr, "n_ok": n_ok, "rank": n_ok * 100 + z["score"],
            "key": f"{sym}|{s['kind']}|{dr}|{ctime}",
            "zk": f"Z|{sym}|{int(round(z['mid'] / max(0.5 * atr, 0.01)))}|{dr}",
            "conf": [f"{mk(h1ok)} اتجاه الساعة: {nm(ctx['h1d'])}", f"{mk(h4ok)} اتجاه 4 ساعات: {nm(ctx['h4d'])}",
                     f"{mk(zok)} المنطقة قوية كمان على فريم الساعة"]})
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
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text}, timeout=15)
    except Exception as e:
        print("telegram failed:", e)


def book(t, realized, day):
    w = day["t"].setdefault(t["kind"], [0, 0])
    day["r"] += realized
    if realized > 0.05:
        day["wins"] += 1
        w[0] += 1
    elif realized < -0.05:
        day["losses"] += 1
        w[1] += 1


def zdesc(z, pip):
    return (f"{fmt(z['bot'], pip)} – {fmt(z['top'], pip)} | {'★' * z['stars']} | {z['touches']} لمسات · {z['sw']} ارتدادات"
            + ("".join(" | " + x for x in z["tags"]) if z["tags"] else ""))


def entry_msg(c, sym, pip):
    p, z = c["plan"], c["zone"]
    side = "شراء 🟢" if c["dir"] == 1 else "بيع 🔴"
    return (f"⚡ {side} — {sym}\n"
            f"النوع: {c['name']}\n"
            f"المنطقة: {zdesc(z, pip)}\n\n"
            f"الدخول: {fmt(p['entry'], pip)}\n"
            f"الستوب (محسوب بدقة): {fmt(p['sl'], pip)} ({dist_txt(p['risk'], pip, sym)})\n"
            f"الهدف 1: {fmt(p['tp1'], pip)} (+{p['r1']:.1f}R)\n"
            f"الهدف 2: {fmt(p['tp2'], pip)} (+{p['r2']:.1f}R)\n\n"
            f"التأكيد ({c['n_ok']}/3):\n" + "\n".join(c["conf"]) + "\n\n"
            f"ليش:\n" + "\n".join("- " + w for w in c["why"]) + "\n\n"
            f"الخطة: عند الهدف 1 سكّر نص الحجم وانقل الستوب للدخول. "
            f"البوت بيراقب وبيبعتلك: تحقق الهدف، أو تنبيه تسكير إذا انعكس السوق وإنت بربح.")


# ---------------- متابعة الصفقات ----------------
def track(t, ctx):
    t0 = pd.Timestamp(t["time"])
    df1, df15 = ctx["df1"], ctx["df15"]
    if df1 is not None and len(df1):
        cs = df1[df1.index >= t0.floor("1min") + pd.Timedelta(minutes=1)]
    else:
        cs = df15[df15.index >= t0.ceil("15min")]
    dr, stage, res = t["dir"], 0, None
    for r in cs.itertuples():
        hi, lo = r.High, r.Low
        tp2 = hi >= t["tp2"] if dr == 1 else lo <= t["tp2"]
        if stage == 0:
            if (lo <= t["sl"]) if dr == 1 else (hi >= t["sl"]):
                res = "SL"
                break
            if (hi >= t["tp1"]) if dr == 1 else (lo <= t["tp1"]):
                stage = 1
                if tp2:
                    stage, res = 2, "TP2"
                    break
        else:
            if (lo <= t["entry"]) if dr == 1 else (hi >= t["entry"]):
                res = "BE"
                break
            if tp2:
                stage, res = 2, "TP2"
                break
    return stage, res


def realized_r(t, stage, r_now):
    return r_now if stage == 0 else 0.5 * t["r1"] + 0.5 * r_now


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
        reasons.append("ظهرت إشارة معاكسة قوية")
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
            gain = abs(t["tp1"] - t["entry"])
            tg(f"✅ تحقق الهدف الأول — {sym} ({side})\n"
               f"دخلت من {fmt(t['entry'], pip)} والسعر وصل {fmt(t['tp1'], pip)}\n"
               f"الربح: {dist_txt(gain, pip, sym)} (+{t['r1']:.1f}R)\n\n"
               f"اعمل هلأ:\n"
               f"1) سكّر نص الصفقة واحجز الربح\n"
               f"2) حرّك الستوب لسعر الدخول {fmt(t['entry'], pip)} (هيك ما بتخسر على الباقي)\n"
               f"3) الهدف الثاني: {fmt(t['tp2'], pip)} والبوت بيكمل يراقب")

        if res == "TP2":
            rr = 0.5 * t["r1"] + 0.5 * t["r2"]
            book(t, rr, day)
            tg(f"🎯 تحقق الهدف الثاني — {sym} ({side})\nالوصول: {fmt(t['tp2'], pip)}\nسكّر الباقي. النتيجة الكلية: +{rr:.1f}R")
            continue
        if res == "BE":
            rr = 0.5 * t["r1"]
            book(t, rr, day)
            tg(f"↩️ رجع السعر لنقطة الدخول — {sym} ({side})\n"
               f"الباقي سكّر بدون خسارة، وربح الهدف الأول محجوز (+{rr:.1f}R).")
            continue
        if res == "SL":
            book(t, -1.0, day)
            tg(f"❌ ضربت الستوب — {sym} ({side})\nالستوب: {fmt(t['sl'], pip)} | الخسارة: -1R\nالبوت رجع يدوّر على فرصة جديدة.")
            continue

        if (now - pd.Timestamp(t["time"])).total_seconds() > EXPIRE_HOURS * 3600:
            rr = realized_r(t, t.get("stage", 0), r_now)
            book(t, rr, day)
            tg(f"⌛ انتهت الصفقة بدون هدف أو ستوب بعد {EXPIRE_HOURS:g} ساعات — {sym} ({side})\nالنتيجة عند السعر الحالي: {rr:+.1f}R")
            continue

        stg = t.get("stage", 0)
        if (r_now >= MIN_PROFIT_R) or (stg >= 1 and r_now > 0.1):
            reasons = reversal_reasons(t, ctx, cands)
            if len(reasons) >= REV_MIN_REASONS:
                rr = realized_r(t, stg, r_now)
                book(t, rr, day)
                gain = abs(live - t["entry"])
                tg(f"⚠️ تنبيه تسكير — {sym} ({side})\n"
                   f"الصفقة بربح: {dist_txt(gain, pip, sym)} ({r_now:+.1f}R)"
                   + (" | نص الصفقة محجوز من الهدف الأول" if stg >= 1 else "") + "\n"
                   f"السوق عم يعكس عليها:\n- " + "\n- ".join(reasons) + "\n\n"
                   f"يُفضّل تسكّر الآن وتحجز الربح. البوت سكّرها عندو وبيدوّر على فرصة جديدة.")
                continue
        still.append(t)
    st["open"] = still


def try_open(st, sym, ctx, cands, now, day):
    if any(t["sym"] == sym for t in st["open"]):
        return "في صفقة مفتوحة عم يراقبها"
    if day["losses"] >= MAX_LOSSES:
        return f"وصل حد الخسائر اليومي ({MAX_LOSSES})"
    if day["sent"] >= MAX_PER_DAY:
        return f"وصل حد الإشارات اليومي ({MAX_PER_DAY})"
    if now.weekday() >= 5:
        return "عطلة نهاية الأسبوع"
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
        return "ما في فرصة مطابقة حالياً"
    best = max(ok, key=lambda x: (x["rank"], -x["off"]))
    p, pip = best["plan"], ctx["pip"]
    tg(entry_msg(best, sym, pip))
    st["open"].append({"v": 2, "sym": sym, "dir": best["dir"], "entry": p["entry"], "sl": p["sl"], "tp1": p["tp1"],
                       "tp2": p["tp2"], "risk": p["risk"], "r1": p["r1"], "r2": p["r2"], "name": best["name"],
                       "kind": KIND_AR[best["kind"]], "time": now.isoformat(), "stage": 0, "h1side": ctx["h1side"]})
    st["sig"][best["key"]] = now.isoformat()
    st["sig"][best["zk"]] = now.isoformat()
    day["sent"] += 1
    return ""


def zones_text(ctx):
    px, pip = ctx["live"], ctx["pip"]
    sz = [z for z in ctx["zones"] if z["score"] >= MIN_ZONE_SCORE]
    up = sorted([z for z in sz if z["bot"] > px], key=lambda z: z["bot"] - px)[:2]
    dn = sorted([z for z in sz if z["top"] < px], key=lambda z: px - z["top"])[:2]
    lines = [f"فوق: {fmt(z['mid'], pip)} {'★' * z['stars']} {'مقاومة' if z['role'] == -1 else 'دعم'} (+{z['bot'] - px:.1f})" for z in up]
    lines += [f"تحت: {fmt(z['mid'], pip)} {'★' * z['stars']} {'دعم' if z['role'] == 1 else 'مقاومة'} (-{px - z['top']:.1f})" for z in dn]
    return "\n".join(lines) if lines else "ما في مناطق قوية قريبة"


def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    DIAG.clear()
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    for k, dflt in (("open", []), ("days", {}), ("sig", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    
    # [تم التصحيح هنا] إغلاق الأقواس بشكل صحيح لتفادي خطأ الـ SyntaxError
    st["open"] = [
        t for t in st["open"] 
        if isinstance(t, dict) and t.get("v") == 2 and t.get("sym") in SYMBOLS
    ]
    
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("summary", False)):
        day.setdefault(k, v)
    day.setdefault("t", {})
    for k in list(st["days"].keys())[:-14]:
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
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | خاسرة: {day['losses']} | صافي: {day['r']:+.1f}R\n"
           "حسب النوع: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in day["t"].items()) or "لا يوجد"))
        day["summary"] = True

    print("DIAG", DIAG)
    if not SENT and (now - pd.Timestamp(st["last_msg"])).total_seconds() >= HEARTBEAT_HOURS * 3600:
        sym0 = next(iter(ctxs))
        c0 = ctxs[sym0]
        nm = lambda v: "صاعد" if v == 1 else "هابط" if v == -1 else "عرضي"
        dtxt = "\n".join(f"- {DIAG_LABELS.get(k, k)}: {v}" for k, v in sorted(DIAG.items(), key=lambda kv: -kv[1])[:5])
        tg(f"🫀 البوت شغال ({now:%H:%M} UTC) — {sym0}\nالسعر: {fmt(c0['live'], c0['pip'])}\n"
           f"اتجاه الساعة: {nm(c0['h1d'])} | 4 ساعات: {nm(c0['h4d'])}\n"
           f"السبب إنو ما في إشارة: {reasons.get(sym0) or 'ما في فرصة مطابقة حالياً'}\n\n"
           f"أقرب مناطق قوية:\n{zones_text(c0)}\n\n"
           f"اليوم: إشارات {day['sent']} | رابحة {day['wins']} | خاسرة {day['losses']} | صافي {day['r']:+.1f}R"
           + (f"\n\nليش الإشارات انرفضت:\n{dtxt}" if dtxt else ""))
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
