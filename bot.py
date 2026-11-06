# -*- coding: utf-8 -*-
"""بوت الذهب XAU/USD على GitHub Actions - استراتيجية الهايكن (الانسحاب) على فريم 15 دقيقة
القاعدة: بعد حركة صاعدة، شمعة هايكن هابطة بدون ذيل علوي (شمعة الانعكاس نفسها أو اللي بعدها) = بيع عند إغلاقها.
العكس للشراء. الستوب فوق قمة آخر شمعة صاعدة (أو تحت قاع آخر شمعة هابطة).
الهدف الأول: أول مستوى. الهدف الثاني: المستوى اللي بعدو. بعد الهدف الأول: حرّك الستوب للدخول.
رسالة عند كل هدف وعند الستوب، وبعدها بيدوّر على صفقة جديدة. صفقة وحدة بالوقت."""
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
PIPS = {"XAU/USD": 0.1, "EUR/USD": 0.0001, "GBP/USD": 0.0001, "USD/JPY": 0.01, "GBP/JPY": 0.01}
PSY = {"XAU/USD": (10.0, 50.0), "EUR/USD": (0.0050, 0.0100), "GBP/USD": (0.0050, 0.0100),
       "USD/JPY": (0.50, 1.00), "GBP/JPY": (0.50, 1.00)}
SPREADS = {"XAU/USD": 1.5, "EUR/USD": 1, "GBP/USD": 1.5, "USD/JPY": 1, "GBP/JPY": 2}
USE_M1 = env("USE_M1", "1") == "1"          # شموع الدقيقة لمتابعة الأهداف والستوب بدقة

# ---- الهايكن ----
HA_MIN_RUN = envi("HA_MIN_RUN", 2)          # أقل عدد شموع بالحركة قبل الانعكاس
HA_WICK_TOL = envf("HA_WICK_TOL", 0.15)     # أكبر ذيل مسموح (من حجم شمعة الهايكن) لتعتبر "بدون ذيل"
LOOKBACK = envi("LOOKBACK", 2)              # آخر شمعتين مغلقتين (لو التشغيل تأخر شوي)
MAX_AGE_MIN = envi("MAX_AGE_MIN", 40)       # آخر شمعة أقدم من هيك = السوق مسكّر
GAP_MIN = envi("GAP_MIN", 70)

# ---- المستويات (للأهداف) ----
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
LEVEL_MIN_SCORE = envi("LEVEL_MIN_SCORE", 7)    # قوة المنطقة لتعتبر مستوى هدف (7 = ★★)
H1_PIV = envi("H1_PIV", 4)
H1_HISTORY = envi("H1_HISTORY", 1000)
H1_MIN_SCORE = envi("H1_MIN_SCORE", 5)

# ---- الصفقة ----
STOP_BUF_ATR = envf("STOP_BUF_ATR", 0.10)       # مسافة خلف قمة/قاع الشمعة (غير السبريد)
MIN_RISK_ATR = envf("MIN_RISK_ATR", 0.5)        # ستوب أضيق من هيك بنوسعو
MIN_TP_ATR = envf("MIN_TP_ATR", 0.3)            # الهدف ما يكون لاصق بالدخول
TP_BUF_ATR = envf("TP_BUF_ATR", 0.10)           # الهدف قبل المستوى بشوي
TP2_GAP_ATR = envf("TP2_GAP_ATR", 1.0)          # الهدف الثاني على الأقل هالمسافة بعد الأول
TP1_PART = envf("TP1_PART", 0.5)                # حصة الصفقة اللي بتتسكّر عند الهدف الأول (للحساب بالـ R)
EXPIRE_HOURS = envf("EXPIRE_HOURS", 8)
EXIT_WICK = envf("EXIT_WICK", 0.12)             # شمعة بذيلين (كل ذيل >= هالنسبة) = ضعف
EXIT_MIN_R = envf("EXIT_MIN_R", 0.3)
MAX_PER_DAY = envi("MAX_PER_DAY", 0)            # 0 = بدون حد
MAX_LOSSES = envi("MAX_LOSSES", 0)              # 0 = بدون حد
HEARTBEAT_HOURS = envf("HEARTBEAT_HOURS", 0)    # 0 = مطفي
STATE_FILE = "state.json"
BOT_VERSION = "2026-11-07-heikin-simple"
SENT = []
LAST_ERR = {"msg": ""}


def fmt(x, pip):
    return f"{x:,.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"


def dist_txt(d, pip, sym):
    p = d / pip
    return f"{p:.0f} بيب ≈ {d:.1f}$" if sym == "XAU/USD" else f"{p:.1f} بيب"


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


KEY_AR = {"PDH": "قمة أمس", "PDL": "قاع أمس", "PWH": "قمة الأسبوع الماضي", "PWL": "قاع الأسبوع الماضي",
          "PMH": "قمة الشهر الماضي", "PML": "قاع الشهر الماضي"}


# ---------------- المناطق (بتستعمل كأهداف) ----------------
def pivots(d, L):
    H = d["High"].values
    Lw = d["Low"].values
    if len(H) < 2 * L + 3:
        return np.array([], dtype=int), np.array([], dtype=int)
    hmax = pd.Series(H).rolling(2 * L + 1, center=True).max().values
    lmin = pd.Series(Lw).rolling(2 * L + 1, center=True).min().values
    return np.flatnonzero(H == hmax), np.flatnonzero(Lw == lmin)


def build_zones(d, atr, keys, piv, psy):
    psy_step, psy_major = psy
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
    clusters, cur = [], None
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
        score = touches + 2 * z["sw"] + int(round(min(z["re"], 8.0) / 2))
        if math.floor(top / psy_major) * psy_major >= bot:
            score += 2
        elif math.floor(top / psy_step) * psy_step >= bot:
            score += 1
        for nm, kv in keys.items():
            if bot - 0.1 * atr <= kv <= top + 0.1 * atr:
                score += 3
        out.append({"top": top, "bot": bot, "mid": (top + bot) / 2, "score": score,
                    "stars": 3 if score >= 12 else 2 if score >= 7 else 1})
    out.sort(key=lambda z: z["mid"])
    return out


# ---------------- الهايكن آشي ----------------
def ha_candles(d, n_use=800):
    w = d.iloc[-n_use:]
    o, h, l, c = (w[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    hc = (o + h + l + c) / 4.0
    ho = np.empty(len(w))
    ho[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(w)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2.0
    hh = np.maximum(h, np.maximum(ho, hc))
    hl = np.minimum(l, np.minimum(ho, hc))
    return pd.DataFrame({"HO": ho, "HH": hh, "HL": hl, "HC": hc}, index=w.index)


def ha_signal(d, off):
    """شموع صاعدة -> شمعة هابطة بدون ذيل علوي = بيع. شموع هابطة -> شمعة صاعدة بدون ذيل سفلي = شراء.
    شمعة الإشارة: شمعة الانعكاس نفسها (lag=0) أو اللي بعدها (lag=1). الستوب خلف آخر شمعة من الحركة."""
    ha = ha_candles(d)
    n = len(ha)
    k = n - 1 - off
    if k < HA_MIN_RUN + 3:
        return None
    gaps = np.diff(ha.index.values[k - HA_MIN_RUN - 2:k + 1]).astype("timedelta64[m]").astype(np.int64)
    if (gaps > 30).any():                       # فجوة سوق (إغلاق يومي/أسبوعي): الشموع مو متتالية
        return None
    HO, HH, HL, HC = (ha[x].values for x in ("HO", "HH", "HL", "HC"))
    bull, bear = HC > HO, HC < HO
    r = HH[k] - HL[k]
    if r <= 0:
        return None
    for dr in (1, -1):
        same, opp = (bull, bear) if dr == 1 else (bear, bull)
        if not same[k]:
            continue
        wick = (min(HO[k], HC[k]) - HL[k]) if dr == 1 else (HH[k] - max(HO[k], HC[k]))
        if wick > HA_WICK_TOL * r:
            continue
        for lag in (0, 1):
            if lag == 1 and not same[k - 1]:
                continue
            run_end = k - lag - 1
            if run_end - HA_MIN_RUN + 1 < 0 or not all(opp[run_end - j] for j in range(HA_MIN_RUN)):
                continue
            run = 0
            while run_end - run >= 0 and opp[run_end - run] and run < 30:
                run += 1
            anchor = HL[run_end] if dr == 1 else HH[run_end]
            return {"dir": dr, "pos": len(d) - 1 - off, "anchor": float(anchor), "run": run, "lag": lag}
    return None


# ---------------- خطة الصفقة ----------------
def make_plan(sig, live, ctx):
    dr, atr, pip, sym = sig["dir"], ctx["atr"], ctx["pip"], ctx["sym"]
    spread = SPREADS.get(sym, 3)
    sl = sig["anchor"] - dr * (STOP_BUF_ATR * atr + spread * pip)
    if (dr == 1 and sl >= live) or (dr == -1 and sl <= live):
        return None, "الستوب بالاتجاه الغلط"
    risk = abs(live - sl)
    if risk < MIN_RISK_ATR * atr:
        sl, risk = live - dr * MIN_RISK_ATR * atr, MIN_RISK_ATR * atr
    buf = TP_BUF_ATR * atr
    lv = []
    for z in ctx["zones"]:
        if z["score"] >= LEVEL_MIN_SCORE:
            d_ = ((z["bot"] if dr == 1 else z["top"]) - live) * dr
            if d_ > 0:
                lv.append((d_, f"منطقة {fmt(z['bot'], pip)} – {fmt(z['top'], pip)}"))
    for z in ctx["h1z"]:
        if z["score"] >= H1_MIN_SCORE:
            d_ = ((z["bot"] if dr == 1 else z["top"]) - live) * dr
            if d_ > 0:
                lv.append((d_, f"منطقة فريم الساعة {fmt(z['bot'], pip)} – {fmt(z['top'], pip)}"))
    for nm, kv in ctx["keys"].items():
        d_ = (kv - live) * dr
        if d_ > 0:
            lv.append((d_, f"{KEY_AR.get(nm, nm)} ({fmt(kv, pip)})"))
    major = float(ctx["psy"][1])
    base = (math.ceil(live / major) * major) if dr == 1 else (math.floor(live / major) * major)
    for q in range(3):                                   # الأرقام النفسية الكبيرة (كل 50$ للذهب)
        lvl = base + dr * q * major
        d_ = (lvl - live) * dr
        if d_ > 0:
            lv.append((d_, f"رقم نفسي كبير ({fmt(lvl, pip)})"))
    lv.sort()
    first = [x for x in lv if x[0] - buf >= MIN_TP_ATR * atr]
    if first:
        d1, desc1 = first[0]
        tp1_d = d1 - buf
        second = [x for x in first[1:] if x[0] - buf >= tp1_d + TP2_GAP_ATR * atr]
        if second:
            tp2_d, desc2 = second[0][0] - buf, second[0][1]
        else:
            tp2_d, desc2 = 2.0 * tp1_d, "ما في مستوى واضح بعدو (ضعف مسافة الأول)"
    else:
        tp1_d, desc1 = 1.0 * risk, "ما في مستوى واضح قدام السعر (1R)"
        tp2_d, desc2 = 2.0 * risk, "(2R)"
    return {"entry": live, "sl": sl, "tp1": live + dr * tp1_d, "tp2": live + dr * tp2_d, "tp": live + dr * tp2_d,
            "be": live + dr * tp1_d, "risk": risk, "tp1_d": tp1_d, "tp_d": tp2_d, "rr1": tp1_d / risk,
            "rr": tp2_d / risk, "desc1": desc1, "desc2": desc2}, ""


def build_ctx(sym, df15, df1, now):
    d15c = closed(df15, 15, now)
    if len(d15c) < 500:
        return None
    pip = PIPS.get(sym, 0.0001)
    psy = PSY.get(sym, (10.0, 50.0))
    live = float(df1["Close"].iloc[-1]) if df1 is not None and len(df1) else float(df15["Close"].iloc[-1])
    h1 = rs_complete(d15c, "1h", 60)
    atr = float(add_atr(d15c).iloc[-1])
    keys = key_levels(d15c)
    h1_atr = float(add_atr(h1).iloc[-1]) if len(h1) > 40 else 0.0
    h1z = build_zones(h1.iloc[-H1_HISTORY:], h1_atr, {}, H1_PIV, psy) if h1_atr > 0 else []
    zones = build_zones(d15c.iloc[-ZONE_HISTORY:], atr, keys, PIV_LEN, psy)
    age_min = (now - (d15c.index[-1] + pd.Timedelta(minutes=15))).total_seconds() / 60
    return {"sym": sym, "pip": pip, "psy": psy, "df15": df15, "df1": df1, "d15c": d15c, "live": live, "atr": atr,
            "keys": keys, "h1z": h1z, "zones": zones, "fresh": age_min <= MAX_AGE_MIN}


def evaluate(ctx, off, now):
    d, live, sym, pip = ctx["d15c"], ctx["live"], ctx["sym"], ctx["pip"]
    if len(d) < 400 + off:
        return []
    end = d.index[len(d) - 1 - off] + pd.Timedelta(minutes=15)
    if (now - end).total_seconds() / 60 > MAX_AGE_MIN:
        return []
    sig = ha_signal(d, off)
    if not sig:
        return []
    plan, why = make_plan(sig, live, ctx)
    if plan is None:
        return []
    dr = sig["dir"]
    ci = float(d["Close"].iloc[sig["pos"]])
    moved = dr * (live - ci)
    if moved > 0.4 * plan["risk"] or moved < -0.5 * plan["risk"]:       # السعر ابتعد (ما منطارد)
        return []
    buy = dr == 1
    ctime = d.index[sig["pos"]].isoformat()
    flip = d.index[sig["pos"] - sig["lag"]].isoformat()
    return [{
        "dir": dr, "off": off, "plan": plan,
        "name": f"هايكن انعكاس ({'شراء' if buy else 'بيع'})",
        "why": [f"هايكن 15 دقيقة: {sig['run']} شموع {'هابطة' if buy else 'صاعدة'} ثم انعكاس، "
                f"{'شمعة الانعكاس نفسها' if sig['lag'] == 0 else 'وبعدها شمعة التأكيد'} "
                f"{'صاعدة بدون ذيل سفلي' if buy else 'هابطة بدون ذيل علوي'}",
                f"الستوب {'تحت قاع' if buy else 'فوق قمة'} آخر شمعة {'هابطة' if buy else 'صاعدة'}"],
        "key": f"{sym}|HA|{dr}|{ctime}", "zk": f"E|{sym}|{dr}|{flip}"}]


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


def entry_msg(c, sym, pip):
    p = c["plan"]
    side = "شراء 🟢" if c["dir"] == 1 else "بيع 🔴"
    return (f"{side} — {sym}\n"
            f"السيناريو: {c['name']}\n" + "\n".join("- " + w for w in c["why"]) + "\n\n"
            f"الدخول: {fmt(p['entry'], pip)}\n"
            f"الستوب: {fmt(p['sl'], pip)} ({dist_txt(p['risk'], pip, sym)})\n"
            f"الهدف الأول: {fmt(p['tp1'], pip)} ({dist_txt(p['tp1_d'], pip, sym)}) — {p['desc1']}\n"
            f"الهدف الثاني: {fmt(p['tp2'], pip)} ({dist_txt(p['tp_d'], pip, sym)}) — {p['desc2']}")


def scn_txt(t):
    w = "\n".join("- " + x for x in (t.get("why") or []))
    return f"السيناريو: {t['name']}" + (f"\n{w}" if w else "")


# ---------------- متابعة الصفقات ----------------
def track(t, ctx):
    """بيعيد حساب الصفقة من الدخول: المرحلة (0/1: وصل الهدف الأول) والنتيجة (SL/BE/TP/None).
    الستوب أولاً إذا تزامن (تحفظاً). بعد الهدف الأول الستوب بينتقل للدخول (BE)"""
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


def weak_candle(ctx, t, now):
    """شمعة هايكن بذيلين (فوق وتحت) بعد الدخول = ضعف بالاتجاه"""
    d = ctx["d15c"]
    if len(d) < 50 or d.index[-1] < pd.Timestamp(t["time"]).ceil("15min"):
        return False
    if (now - (d.index[-1] + pd.Timedelta(minutes=15))).total_seconds() / 60 > 20:
        return False
    ha = ha_candles(d).iloc[-1]
    rg = ha["HH"] - ha["HL"]
    if rg <= 0:
        return False
    up_w, lo_w = ha["HH"] - max(ha["HO"], ha["HC"]), min(ha["HO"], ha["HC"]) - ha["HL"]
    return up_w >= EXIT_WICK * rg and lo_w >= EXIT_WICK * rg


def manage(st, sym, ctx, now, day):
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
        part = TP1_PART
        if stage >= 1 and t.get("stage", 0) < 1:
            t["stage"] = 1
            if res is None:
                tg(f"🎯 الهدف الأول وصل ✅ — {sym} ({side})\n"
                   f"حرّك الستوب لسعر الدخول {fmt(t['entry'], pip)}")
        if res == "TP":
            realized = part * t["rr1"] + (1 - part) * t["rr"]
            book(t, realized, day)
            tg(f"🎯🎯 الهدف الثاني وصل ✅ — {sym} ({side})\n"
               f"{dist_txt(abs(t['tp'] - t['entry']), pip, sym)} ({realized:+.1f}R إجمالي)\n\n" + scn_txt(t))
            continue
        if res == "BE":
            realized = part * t["rr1"]
            book(t, realized, day)
            tg(f"↩️ سكّرت على الدخول بعد الهدف الأول — {sym} ({side})\n"
               f"ربح جزئي من الهدف الأول ({realized:+.1f}R)\n\n" + scn_txt(t))
            continue
        if res == "SL":
            book(t, -1.0, day)
            tg(f"❌ خسرت — {sym} ({side})\n-{dist_txt(risk, pip, sym)} (-1R)\n\n" + scn_txt(t))
            continue
        age = int((ctx["d15c"].index >= pd.Timestamp(t["time"]).ceil("15min")).sum())
        r_if_close = r_now if stage < 1 else part * t["rr1"] + (1 - part) * r_now
        if age >= EXPIRE_HOURS * 4:
            book(t, r_if_close, day)
            tg(f"⌛ انتهت الصفقة بدون هدف ثاني أو ستوب بعد {EXPIRE_HOURS:g} ساعات — {sym} ({side})\n"
               f"النتيجة عند السعر الحالي: {r_if_close:+.1f}R\n\n" + scn_txt(t))
            continue
        if r_now >= EXIT_MIN_R and weak_candle(ctx, t, now):
            book(t, r_if_close, day)
            tg(f"⚠️ سكّر هلأ — {sym} ({side})\n"
               f"طلعت شمعة هايكن بذيلين (ضعف) وإنت بربح {dist_txt(abs(live - t['entry']), pip, sym)} "
               f"({r_if_close:+.1f}R)\nالبوت سكّرها عندو وبيدوّر على فرصة جديدة.\n\n" + scn_txt(t))
            continue
        still.append(t)
    st["open"] = still


def try_open(st, sym, ctx, cands, now, day):
    if any(t["sym"] == sym for t in st["open"]):
        return "في صفقة مفتوحة عم يراقبها"
    sd = sym_day(day, sym)
    if MAX_LOSSES and sd["losses"] >= MAX_LOSSES:
        return f"وصل حد الخسائر اليومي ({MAX_LOSSES})"
    if MAX_PER_DAY and sd["sent"] >= MAX_PER_DAY:
        return f"وصل حد الإشارات اليومي ({MAX_PER_DAY})"
    if not ctx["fresh"]:
        return "السوق مسكّر حالياً"
    ok = [c for c in cands if c["key"] not in st["sig"] and c["zk"] not in st["sig"]]
    if not ok:
        return "ما في إشارة جديدة"
    best = min(ok, key=lambda x: x["off"])                  # الأحدث
    p, pip = best["plan"], ctx["pip"]
    tg(entry_msg(best, sym, pip))
    st["open"].append({"v": 3, "sym": sym, "dir": best["dir"], "entry": p["entry"], "sl": p["sl"], "tp": p["tp"],
                       "tp1": p["tp1"], "tp2": p["tp2"], "be": p["be"], "risk": p["risk"], "tp_d": p["tp_d"],
                       "rr1": p["rr1"], "rr": p["rr"], "name": best["name"], "why": best["why"],
                       "kind": "هايكن", "time": now.isoformat(), "stage": 0})
    st["sig"][best["key"]] = now.isoformat()
    st["sig"][best["zk"]] = now.isoformat()
    day["sent"] += 1
    sd["sent"] += 1
    return ""


# ---------------- التشغيل ----------------
def run(now=None):
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        tg("⚠️ مفتاح TWELVE_DATA_API_KEY غير موجود بالـ secrets")
        sys.exit(0)
    now = now if now is not None else pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    for k, dflt in (("open", []), ("days", {}), ("sig", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    st["open"] = [t for t in st["open"] if isinstance(t, dict) and t.get("v") == 3 and t.get("sym") in SYMBOLS]
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("summary", False)):
        day.setdefault(k, v)
    day.setdefault("t", {})
    day.setdefault("ps", {})
    for k in list(st["days"].keys())[:-30]:
        del st["days"][k]
    st["sig"] = {k: v for k, v in st["sig"].items() if (now - pd.Timestamp(v)).total_seconds() < 86400}
    st.setdefault("last_msg", now.isoformat())
    if st.get("version") != BOT_VERSION:
        tg("✅ البوت شغال — نسخة الهايكن (بسيطة).\n"
           "القاعدة: بعد حركة صاعدة، شمعة هايكن 15 دقيقة هابطة بدون ذيل علوي = بيع (والعكس شراء).\n"
           "الستوب خلف آخر شمعة من الحركة، الهدف الأول أول مستوى والثاني اللي بعدو.\n"
           "رسالة عند كل هدف وعند الستوب. ملخص يومي الساعة 21 UTC.")
        st["version"] = BOT_VERSION

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
        manage(st, sym, ctx, now, day)
        cands = []
        for off in range(LOOKBACK):
            cands += evaluate(ctx, off, now)
        reasons[sym] = try_open(st, sym, ctx, cands, now, day)

    if now.hour >= 21 and not day["summary"]:
        tot = {}
        for dd_ in st["days"].values():
            for k, v in dd_.get("t", {}).items():
                a = tot.setdefault(k, [0, 0])
                a[0] += v[0]
                a[1] += v[1]
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | خاسرة: {day['losses']} | صافي: {day['r']:+.1f}R"
           f"\n\nآخر {len(st['days'])} يوم:\n" + ("\n".join(f"{k}: {v[0]}✅ / {v[1]}❌" for k, v in tot.items()) or "لا يوجد"))
        day["summary"] = True

    fresh_syms = [s for s, c in ctxs.items() if c["fresh"]]
    if (HEARTBEAT_HOURS > 0 and fresh_syms and not SENT
            and (now - pd.Timestamp(st["last_msg"])).total_seconds() >= HEARTBEAT_HOURS * 3600):
        parts = []
        for s0 in fresh_syms:
            c0 = ctxs[s0]
            parts.append(f"▪️ {s0}\nالسعر: {fmt(c0['live'], c0['pip'])}\nالحالة: {reasons.get(s0) or 'ما في إشارة جديدة'}")
        tg(f"🫀 البوت شغال ({now:%H:%M} UTC)\n\n" + "\n\n".join(parts) + "\n\n"
           f"اليوم: إشارات {day['sent']} | رابحة {day['wins']} | خاسرة {day['losses']} | صافي {day['r']:+.1f}R")
    if SENT:
        st["last_msg"] = now.isoformat()
    save(st)


if __name__ == "__main__":
    try:
        run()
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
