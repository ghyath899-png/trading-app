"""
بوت تداول XAU/USD ومؤشرات/عملات على GitHub Actions
استراتيجية Break & Retest الشرسة بأهداف لا تقل عن 50 بيب ونسبة عائد 1:2.5
"""
import os
import sys
import json
import time
from datetime import datetime, timezone
import pandas as pd
import requests


def env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


TD_KEY = env("TWELVE_DATA_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")
SYMBOLS = [s.strip() for s in env("SYMBOLS", "XAU/USD").split(",") if s.strip()]

MIN_SCORE = float(env("MIN_SCORE", "4"))
RR = float(env("RR", "2.5"))
MIN_TP_PIPS = float(env("MIN_TP_PIPS", "50"))
MAX_PER_DAY = int(env("MAX_PER_DAY", "5"))
MAX_LOSSES = int(env("MAX_LOSSES", "3"))
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "15"))

PIPS = {"XAU/USD": 0.1, "GBP/JPY": 0.01, "USD/JPY": 0.01, "EUR/USD": 0.0001,
        "GBP/USD": 0.0001, "BTC/USD": 1.0, "NDX": 1.0}
STATE_FILE = "state.json"
LAST_ERR = {"msg": ""}


def prep(df):
    d = df.copy()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()
    ch = d["Close"].diff()
    up = ch.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["RSI"] = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - d["Close"].shift()).abs(),
                    (d["Low"] - d["Close"].shift()).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return d


def swings(d, n=3):
    h, l = d["High"].values, d["Low"].values
    H, L = [], []
    for i in range(n, len(d) - n):
        if h[i] == h[i - n:i + n + 1].max():
            H.append((i, float(h[i])))
        if l[i] == l[i - n:i + n + 1].min():
            L.append((i, float(l[i])))
    return H, L


def trend(df):
    d = prep(df)
    last = d.iloc[-1]
    H, L = swings(d)
    t = 1 if last["EMA50"] > last["EMA200"] else -1
    if len(H) >= 2 and len(L) >= 2:
        if H[-1][1] > H[-2][1] and L[-1][1] > L[-2][1]:
            t += 1
        elif H[-1][1] < H[-2][1] and L[-1][1] < L[-2][1]:
            t -= 1
    return (t > 0) - (t < 0)


def candle(d):
    c, p = d.iloc[-1], d.iloc[-2]
    body = abs(c["Close"] - c["Open"])
    rng = (c["High"] - c["Low"]) or 1e-9
    up = c["High"] - max(c["Close"], c["Open"])
    lo = min(c["Close"], c["Open"]) - c["Low"]
    bull = (lo >= 2 * body and lo >= 0.5 * rng) or (
        c["Close"] > c["Open"] and p["Close"] < p["Open"] and c["Close"] >= p["Open"] and c["Open"] <= p["Close"])
    bear = (up >= 2 * body and up >= 0.5 * rng) or (
        c["Close"] < c["Open"] and p["Close"] > p["Open"] and c["Close"] <= p["Open"] and c["Open"] >= p["Close"])
    return bool(bull), bool(bear)


def get_htf_target(df_h1, df_h4, price, direction):
    targets = []
    for df in (df_h1, df_h4):
        if df is None or len(df) < 20:
            continue
        d = prep(df)
        H, L = swings(d, n=3)
        if direction == 1:
            for _, lvl in reversed(H):
                if lvl > price + 0.5:
                    targets.append(lvl)
        else:
            for _, lvl in reversed(L):
                if lvl < price - 0.5:
                    targets.append(lvl)
    
    if targets:
        return min(targets) if direction == 1 else max(targets)
    return None


def extra_setups(d, price, atr, H, L, tr):
    out = []
    n = len(d)
    hi, lo, cl = d["High"].values, d["Low"].values, d["Close"].values

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    g = lambda x: f"{x:.5g}"
    done = set()

    for i in range(max(n - 12, 8), n - 1):
        for hidx, lv in H[-4:]:
            if 1 in done:
                break
            if hidx < i and hi[i] > lv and cl[i] < lv:
                level = lo[i - 6:i].min()
                ks = [k for k in range(i + 1, n) if cl[k] < level]
                if ks and ks[0] >= n - 3 and price <= level + 0.3 * atr:
                    add("Sweep + CHOCH بيع", -1, hi[i:].max() + 0.2 * atr, 5,
                        f"سحب سيولة فوق {g(lv)} ثم كسر هيكل هابط وإعادة اختبار عند {g(level)}")
                    done.add(1)
        for lidx, lv in L[-4:]:
            if 2 in done:
                break
            if lidx < i and lo[i] < lv and cl[i] > lv:
                level = hi[i - 6:i].max()
                ks = [k for k in range(i + 1, n) if cl[k] > level]
                if ks and ks[0] >= n - 3 and price >= level - 0.3 * atr:
                    add("Sweep + CHOCH شراء", 1, lo[i:].min() - 0.2 * atr, 5,
                        f"سحب سيولة تحت {g(lv)} ثم كسر هيكل صاعد وإعادة اختبار عند {g(level)}")
                    done.add(2)

    if len(H) >= 2 and len(L) >= 1:
        lv = H[-1][1]
        if abs(price - lv) <= 0.4 * atr and cl[-1] > lv:
            add("Break & Retest صاعد (CHOCH)", 1, L[-1][1] - 0.2 * atr, 5, f"كسر القمة {g(lv)} وإعادة اختبار المنطقة الحالية")
    if len(L) >= 2 and len(H) >= 1:
        lv = L[-1][1]
        if abs(price - lv) <= 0.4 * atr and cl[-1] < lv:
            add("Break & Retest هابط (CHOCH)", -1, H[-1][1] + 0.2 * atr, 5, f"كسر القاع {g(lv)} وإعادة اختبار المنطقة الحالية")

    return out


def scan(df, crypto):
    d = prep(df)
    last = d.iloc[-1]
    price, atr, rsi = float(last["Close"]), float(last["ATR"]), float(last["RSI"])
    H, L = swings(d)
    bull, bear = candle(d)
    tr = trend(d)
    out = []

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    n = len(d)
    for i, lv in H[-3:]:
        if i < n - 12 and abs(price - lv) <= 0.5 * atr:
            add("Break & Retest دعم/مقاومة", 1, lv - 0.8 * atr, 4, f"إعادة اختبار القمة المكسورة عند {lv:.2f}")
            break
    for i, lv in L[-3:]:
        if i < n - 12 and abs(price - lv) <= 0.5 * atr:
            add("Break & Retest دعم/مقاومة", -1, lv + 0.8 * atr, 4, f"إعادة اختبار القاع المكسور عند {lv:.2f}")
            break

    out += extra_setups(d, price, atr, H, L, tr)

    hour = datetime.now(timezone.utc).hour
    session = crypto or 7 <= hour <= 20
    cnt = {1: len({s["name"] for s in out if s["dir"] == 1}), -1: len({s["name"] for s in out if s["dir"] == -1})}

    for s in out:
        dr = s["dir"]
        tags, warn = [], []
        if (dr == 1 and bull) or (dr == -1 and bear):
            s["score"] += 1
            tags.append("شمعة تأكيد ارتدادية")
        if session:
            s["score"] += 1
            tags.append("جلسة نشطة")
        if cnt[dr] > 1:
            s["score"] += 1
            tags.append("تلاقي إشارات بنفس الاتجاه")
        s["tags"], s["warn"] = tags, warn

    ctx = {"price": price, "atr": atr, "rsi": rsi, "trend": tr}
    return out, ctx


def build(s, price, atr, pip, min_rr, min_tp_pips, htf_target=None):
    dr, sl = s["dir"], s["sl"]
    if (dr == 1 and sl >= price) or (dr == -1 and sl <= price):
        return None, "ستوب بالاتجاه الخاطئ"
    
    risk = abs(price - sl)
    if risk < 0.5 * atr:
        sl, risk = price - dr * 0.5 * atr, 0.5 * atr
    
    calculated_tp = price + dr * (risk * min_rr)
    
    if htf_target:
        htf_dist = abs(htf_target - price)
        if htf_dist / risk >= min_rr:
            tp1 = htf_target
        else:
            tp1 = calculated_tp
    else:
        tp1 = calculated_tp

    tp1_p = abs(tp1 - price) / pip
    risk_p = risk / pip
    actual_rr = tp1_p / risk_p if risk_p > 0 else 0

    if tp1_p < min_tp_pips or actual_rr < min_rr:
        return None, "عدم استيفاء الشروط الأدنى للأهداف والعائد"

    tp2 = price + dr * (risk * (min_rr * 1.5))
    return {
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_p": risk_p,
        "tp1_p": tp1_p,
        "tp2_p": abs(tp2 - price) / pip,
        "rr_actual": actual_rr
    }, ""


def fetch(symbol):
    try:
        j = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
            "symbol": symbol, "interval": "5min", "outputsize": 500,
            "timezone": "UTC", "apikey": TD_KEY}).json()
    except Exception as e:
        LAST_ERR["msg"] = str(e)[:200]
        return None
    if "values" not in j:
        LAST_ERR["msg"] = str(j.get("message", j))[:200]
        print(symbol, "ERROR:", LAST_ERR["msg"])
        return None
    df = pd.DataFrame(j["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]
    return df[["Open", "High", "Low", "Close"]]


def rs(df, rule):
    if rule == "5min":
        return df
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def make_frames(df):
    m15, h1, h4 = (rs(df, r).iloc[:-1] for r in ("15min", "1h", "4h"))
    return {
        "5min": {"df": df.iloc[:-1], "h1": h1, "h4": h4},
        "15min": {"df": m15, "h1": h1, "h4": h4},
    }


def load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": [], "days": {}, "last": {}}


def save(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def tg(text):
    print(text)
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text}, timeout=15)
    except Exception as e:
        print("telegram failed:", e)


def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)
    
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    
    for k, dflt in (("open", []), ("days", {}), ("last", {}), ("sig", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
            
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0)):
        day.setdefault(k, v)

    st["open"] = [t for t in st["open"] if isinstance(t, dict) and t.get("sym") in SYMBOLS]

    m5s = {}
    for sym in SYMBOLS:
        df = fetch(sym)
        if df is not None and len(df) > 100:
            m5s[sym] = df
        time.sleep(1.2)
        
    if not m5s:
        save(st)
        return

    frames = {sym: make_frames(df) for sym, df in m5s.items()}

    if day["losses"] >= MAX_LOSSES or day["sent"] >= MAX_PER_DAY:
        save(st)
        return

    open_syms = {t["sym"] for t in st["open"]}
    best = {}
    for sym, fr in frames.items():
        last = st["last"].get(sym)
        if sym in open_syms or (last and (now - pd.Timestamp(last)).total_seconds() < COOLDOWN_MIN * 60):
            continue
        live, pip = float(m5s[sym]["Close"].iloc[-1]), PIPS.get(sym, 0.1)
        
        for tf, f in fr.items():
            if len(f["df"]) < 60:
                continue
            setups, ctx = scan(f["df"], "BTC" in sym)
            
            for s in setups:
                key = f"{sym}|{s['name']}|{s['dir']}"
                if key in st["sig"] and (now - pd.Timestamp(st["sig"][key])).total_seconds() < 7200:
                    continue
                
                target_for_dir = get_htf_target(f.get("h1"), f.get("h4"), live, s["dir"])
                t, err_msg = build(s, live, ctx["atr"], pip, RR, MIN_TP_PIPS, target_for_dir)
                
                if t and s["score"] >= MIN_SCORE and (sym not in best or s["score"] > best[sym]["score"]):
                    s.update(t)
                    s.update(sym=sym, tf=tf, key=key, ctx_time=m5s[sym].index[-1].isoformat())
                    best[sym] = s

    for s in sorted(best.values(), key=lambda x: -x["score"]):
        if day["sent"] >= MAX_PER_DAY:
            break
        
        tg(f"⚡ إشارة إعـادة اخـتـبـار — {s['sym']}\n"
           f"التقييم: نقاط {s['score']:.0f} | فريم {s['tf']}\n"
           f"النموذج: {s['name']}\n\n"
           f"📍 الدخول: {s['entry']:.5g}\n"
           f"🛑 الستوب: {s['sl']:.5g} ({s['risk_p']:.0f} بيب)\n"
           f"🎯 الهدف الأول (TP1): {s['tp1']:.5g} ({s['tp1_p']:.0f} بيب)\n"
           f"🚀 الهدف الممتد (TP2): {s['tp2']:.5g} ({s['tp2_p']:.0f} بيب)\n"
           f"⚖️ نسبة المخاطرة/العائد: 1:{s['rr_actual']:.1f}\n\n"
           f"سبب الدخول:\n- {s['why']}")
        
        st["open"].append({"sym": s["sym"], "dir": s["dir"], "entry": s["entry"], "sl": s["sl"],
                           "tp1": s["tp1"], "tp2": s["tp2"], "rr": s['rr_actual'], "name": s["name"],
                           "tf": s["tf"], "time": s["ctx_time"]})
        st["last"][s["sym"]] = now.isoformat()
        st["sig"][s["key"]] = now.isoformat()
        day["sent"] += 1

    save(st)


if __name__ == "__main__":
    main()
