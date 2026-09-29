"""بوت إشارات يعمل على GitHub Actions ويرسل الصفقات لتيليجرام (بدون واجهة)"""
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
SYMBOLS = [s.strip() for s in env("SYMBOLS", "XAU/USD,GBP/JPY,EUR/USD,BTC/USD").split(",") if s.strip()]
ENTRY_TF = env("ENTRY_TF", "15min")            # 5min | 15min | 1h
MIN_SCORE = float(env("MIN_SCORE", "4"))       # أقل نقاط تأكيد (2 نشط، 4 متوازن، 6 صارم)
RR = float(env("RR", "1.5"))                   # نسبة الهدف الأول للمخاطرة
MAX_PER_DAY = int(env("MAX_PER_DAY", "5"))     # أقصى عدد إشارات باليوم
MAX_LOSSES = int(env("MAX_LOSSES", "3"))       # بعد هالعدد من الخسائر بيوقف لباقي اليوم
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "60"))  # أقل فاصل بين إشارتين لنفس الأداة
PIPS = {"XAU/USD": 0.1, "GBP/JPY": 0.01, "USD/JPY": 0.01, "EUR/USD": 0.0001,
        "GBP/USD": 0.0001, "BTC/USD": 1.0, "NDX": 1.0}
STATE_FILE = "state.json"


def prep(df):
    d = df.copy()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()
    ch = d["Close"].diff()
    up = ch.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["RSI"] = 100 - 100 / (1 + up / dn)
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


def sr_zones(d, atr):
    H, L = swings(d, 3)
    pts = sorted(p for _, p in H + L)
    zs = []
    for p in pts:
        if zs and abs(p - zs[-1]["s"] / zs[-1]["n"]) <= 0.5 * atr:
            zs[-1]["s"] += p
            zs[-1]["n"] += 1
            zs[-1]["hi"] = p
        else:
            zs.append({"s": p, "n": 1, "lo": p, "hi": p})
    return [{"lvl": z["s"] / z["n"], "n": z["n"], "lo": z["lo"], "hi": z["hi"]} for z in zs if z["n"] >= 2]


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


def fvgs(d, look=80):
    sub = d.iloc[-look:]
    hi, lo = sub["High"].values, sub["Low"].values
    out = []
    for i in range(2, len(sub)):
        if lo[i] > hi[i - 2]:
            z = (float(hi[i - 2]), float(lo[i]))
            if all(lo[j] > z[0] for j in range(i + 1, len(sub))):
                out.append((1, z))
        if hi[i] < lo[i - 2]:
            z = (float(hi[i]), float(lo[i - 2]))
            if all(hi[j] < z[1] for j in range(i + 1, len(sub))):
                out.append((-1, z))
    return out[-4:]


def order_block(d):
    n = len(d)
    body = d["Close"] - d["Open"]
    for i in range(n - 4, max(n - 60, 2), -1):
        if abs(body.iloc[i]) >= 1.5 * float(d["ATR"].iloc[i]):
            p = d.iloc[i - 1]
            z = (float(p["Low"]), float(p["High"]))
            if body.iloc[i] > 0 and p["Close"] < p["Open"] and (d["Close"].iloc[i + 1:-1] > z[0]).all():
                return 1, z
            if body.iloc[i] < 0 and p["Close"] > p["Open"] and (d["Close"].iloc[i + 1:-1] < z[1]).all():
                return -1, z
            return None
    return None


# ---------------- الاستراتيجيات ----------------
def scan(df, b4, b1d, crypto):
    d = prep(df)
    last = d.iloc[-1]
    price, atr, rsi = float(last["Close"]), float(last["ATR"]), float(last["RSI"])
    ema50, ema200 = float(last["EMA50"]), float(last["EMA200"])
    H, L = swings(d)
    zones = sr_zones(d.iloc[-250:], atr)
    bull, bear = candle(d)
    tr = trend(d)
    out = []

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    swl = L[-1][1] if L else price - 2 * atr
    swh = H[-1][1] if H else price + 2 * atr

    # 1) ارتداد من EMA50 مع الاتجاه
    if tr == 1 and abs(price - ema50) <= 0.6 * atr and rsi < 60 and price > ema200:
        add("ارتداد EMA50 مع الاتجاه", 1, min(swl, price - 1.2 * atr) - 0.2 * atr, 3, "اتجاه صاعد وتراجع للمتوسط 50")
    if tr == -1 and abs(price - ema50) <= 0.6 * atr and rsi > 40 and price < ema200:
        add("ارتداد EMA50 مع الاتجاه", -1, max(swh, price + 1.2 * atr) + 0.2 * atr, 3, "اتجاه هابط وصعود للمتوسط 50")

    # 2) ارتداد من دعم/مقاومة مع شمعة رفض
    for z in zones:
        base = 2 + (1 if z["n"] >= 3 else 0)
        if bull and z["lo"] - 0.3 * atr <= price <= z["hi"] + 0.5 * atr:
            add("ارتداد من دعم", 1, z["lo"] - 0.3 * atr, base, f"دعم لمس {z['n']} مرات + شمعة رفض صاعدة")
        if bear and z["lo"] - 0.5 * atr <= price <= z["hi"] + 0.3 * atr:
            add("ارتداد من مقاومة", -1, z["hi"] + 0.3 * atr, base, f"مقاومة لمست {z['n']} مرات + شمعة رفض هابطة")

    # 3) سحب سيولة (Liquidity Sweep)
    rh, rl = float(d["High"].iloc[-3:].max()), float(d["Low"].iloc[-3:].min())
    for _, lv in H[-3:]:
        if rh > lv > price:
            add("سحب سيولة قمة", -1, rh + 0.2 * atr, 3, f"اختراق كاذب فوق {lv:.2f} وإغلاق تحته")
            break
    for _, lv in L[-3:]:
        if rl < lv < price:
            add("سحب سيولة قاع", 1, rl - 0.2 * atr, 3, f"اختراق كاذب تحت {lv:.2f} وإغلاق فوقه")
            break

    # 4) إعادة اختبار FVG
    for dr, (lo, hi) in fvgs(d):
        if dr == 1 and tr >= 0 and lo - 0.2 * atr <= price <= hi:
            add("إعادة اختبار FVG صاعدة", 1, lo - 0.3 * atr, 2, f"السعر داخل فجوة [{lo:.2f}-{hi:.2f}]")
        if dr == -1 and tr <= 0 and lo <= price <= hi + 0.2 * atr:
            add("إعادة اختبار FVG هابطة", -1, hi + 0.3 * atr, 2, f"السعر داخل فجوة [{lo:.2f}-{hi:.2f}]")

    # 5) كسر وإعادة اختبار
    n = len(d)
    for i, lv in H[-3:]:
        if i < n - 12 and (d["Close"].iloc[-12:-1] > lv).any() and lv - 0.2 * atr <= price <= lv + 0.5 * atr:
            add("كسر وإعادة اختبار", 1, lv - 0.8 * atr, 2, f"كسر {lv:.2f} وعودة لاختباره")
            break
    for i, lv in L[-3:]:
        if i < n - 12 and (d["Close"].iloc[-12:-1] < lv).any() and lv - 0.5 * atr <= price <= lv + 0.2 * atr:
            add("كسر وإعادة اختبار", -1, lv + 0.8 * atr, 2, f"كسر {lv:.2f} وعودة لاختباره")
            break

    # 6) فيبوناتشي 0.5 - 0.786
    if H and L:
        (iH, pH), (iL, pL) = H[-1], L[-1]
        leg = abs(pH - pL)
        if leg > 1.5 * atr:
            if iL < iH and tr >= 0:
                lo, hi = pH - 0.786 * leg, pH - 0.5 * leg
                if lo - 0.2 * atr <= price <= hi + 0.2 * atr:
                    add("فيبوناتشي ذهبية", 1, pL - 0.1 * atr, 2, f"تصحيح داخل 0.5-0.786 [{lo:.2f}-{hi:.2f}]")
            if iH < iL and tr <= 0:
                lo, hi = pL + 0.5 * leg, pL + 0.786 * leg
                if lo - 0.2 * atr <= price <= hi + 0.2 * atr:
                    add("فيبوناتشي ذهبية", -1, pH + 0.1 * atr, 2, f"تصحيح داخل 0.5-0.786 [{lo:.2f}-{hi:.2f}]")

    # 7) تشبع RSI عند منطقة (عكس الاتجاه، مخاطرة أعلى)
    near_zone = any(z["lo"] - 0.5 * atr <= price <= z["hi"] + 0.5 * atr for z in zones)
    if rsi <= 30 and near_zone:
        add("تشبع بيعي RSI عند دعم", 1, price - 1.5 * atr, 1, f"RSI={rsi:.0f} عند منطقة دعم")
    if rsi >= 70 and near_zone:
        add("تشبع شرائي RSI عند مقاومة", -1, price + 1.5 * atr, 1, f"RSI={rsi:.0f} عند منطقة مقاومة")

    # 8) Order Block
    ob = order_block(d)
    if ob:
        dr, (lo, hi) = ob
        if dr == 1 and lo - 0.2 * atr <= price <= hi + 0.2 * atr:
            add("Order Block صاعد", 1, lo - 0.3 * atr, 2, f"عودة لكتلة أوامر [{lo:.2f}-{hi:.2f}]")
        if dr == -1 and lo - 0.2 * atr <= price <= hi + 0.2 * atr:
            add("Order Block هابط", -1, hi + 0.3 * atr, 2, f"عودة لكتلة أوامر [{lo:.2f}-{hi:.2f}]")

    # ---- التقييم (Confluence) ----
    hour = datetime.now(timezone.utc).hour
    session = crypto or 7 <= hour <= 20
    cnt = {1: len({s["name"] for s in out if s["dir"] == 1}), -1: len({s["name"] for s in out if s["dir"] == -1})}
    for s in out:
        dr = s["dir"]
        s["score"] += (1 if dr == b4 else -1 if b4 else 0) + (1 if dr == b1d else -1 if b1d else 0)
        s["score"] += 1 if (dr == 1 and bull) or (dr == -1 and bear) else 0
        s["score"] += 1 if (dr == 1 and rsi < 50) or (dr == -1 and rsi > 50) else 0
        s["score"] += 1 if session else 0
        s["score"] += min(cnt[dr] - 1, 2)
    ctx = {"price": price, "atr": atr, "rsi": rsi, "trend": tr, "zones": zones}
    return out, ctx


def build(s, price, atr, pip, rr, min_tp):
    dr, sl = s["dir"], s["sl"]
    if (dr == 1 and sl >= price) or (dr == -1 and sl <= price):
        return None, "ستوب بالاتجاه الغلط"
    risk = abs(price - sl)
    if risk < 0.7 * atr:
        sl, risk = price - dr * 0.7 * atr, 0.7 * atr
    if risk > 4 * atr:
        return None, "الستوب بعيد (أكبر من 4 ATR)"
    tp1_p = rr * risk / pip
    if min_tp and tp1_p < min_tp:
        return None, f"TP1 = {tp1_p:.0f} بيب، أقل من {min_tp:.0f}"
    return {"entry": price, "sl": sl, "tp1": price + dr * rr * risk, "tp2": price + dr * rr * 1.8 * risk,
            "risk_p": risk / pip, "tp1_p": tp1_p, "tp2_p": rr * 1.8 * risk / pip}, ""


def fmt(x, pip):
    return f"{x:.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"




# ---------------- البيانات ----------------
def fetch(symbol):
    r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
        "symbol": symbol, "interval": "5min", "outputsize": 5000,
        "timezone": "UTC", "apikey": TD_KEY})
    j = r.json()
    if "values" not in j:
        print(symbol, "ERROR:", j.get("message", j))
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


# ---------------- الحالة وتيليجرام ----------------
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


# ---------------- متابعة الصفقات المفتوحة ----------------
def check_open(st, m5s, now, day):
    still = []
    for t in st["open"]:
        df = m5s.get(t["sym"])
        if df is None:
            still.append(t)
            continue
        since = df[df.index > pd.Timestamp(t["time"])]
        res = None
        for _, c in since.iterrows():
            if t["dir"] == 1:
                sl_hit, tp_hit = c["Low"] <= t["sl"], c["High"] >= t["tp1"]
            else:
                sl_hit, tp_hit = c["High"] >= t["sl"], c["Low"] <= t["tp1"]
            if sl_hit:               # لو الاثنين بنفس الشمعة نعتبر الستوب أول (تحفظاً)
                res = "SL"
                break
            if tp_hit:
                res = "TP1"
                break
        if res is None and (now - pd.Timestamp(t["time"])).total_seconds() > 24 * 3600:
            res = "EXPIRED"
        if res is None:
            still.append(t)
            continue
        side = "شراء" if t["dir"] == 1 else "بيع"
        if res == "TP1":
            day["wins"] += 1
            day["r"] += t["rr"]
            tg(f"✅ تحقق الهدف الأول\n{t['sym']} ({side}) - {t['name']}\nانقل الستوب لسعر الدخول {t['entry']:.5g}، والهدف الثاني {t['tp2']:.5g}")
        elif res == "SL":
            day["losses"] += 1
            day["r"] -= 1
            tg(f"❌ ضرب الستوب\n{t['sym']} ({side}) - {t['name']}\nالستوب {t['sl']:.5g}")
        else:
            tg(f"⌛ انتهت الصفقة بدون نتيجة بعد 24 ساعة\n{t['sym']} ({side}) - {t['name']}")
    st["open"] = still


# ---------------- التشغيل ----------------
def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    st.setdefault("open", [])
    st.setdefault("last", {})
    day = st.setdefault("days", {}).setdefault(
        today, {"sent": 0, "wins": 0, "losses": 0, "r": 0.0, "summary": False})
    for k in list(st["days"].keys())[:-14]:
        del st["days"][k]

    m5s = {}
    for sym in SYMBOLS:
        df = fetch(sym)
        if df is not None and len(df) > 500:
            m5s[sym] = df
        time.sleep(1.5)
    if not m5s:
        save(st)
        return

    check_open(st, m5s, now, day)

    if day["losses"] < MAX_LOSSES and day["sent"] < MAX_PER_DAY:
        open_syms = {t["sym"] for t in st["open"]}
        best = {}
        for sym, df in m5s.items():
            last = st["last"].get(sym)
            if sym in open_syms or (last and (now - pd.Timestamp(last)).total_seconds() < COOLDOWN_MIN * 60):
                continue
            entry = rs(df, ENTRY_TF).iloc[:-1]
            h1, h4 = rs(df, "1h").iloc[:-1], rs(df, "4h").iloc[:-1]
            if len(entry) < 80:
                continue
            b4 = trend(h4) if len(h4) >= 40 else 0
            b1 = trend(h1) if len(h1) >= 60 else 0
            setups, ctx = scan(entry, b4, b1, "BTC" in sym)
            live, pip = float(df["Close"].iloc[-1]), PIPS.get(sym, 0.0001)
            for s in setups:
                t, _ = build(s, live, ctx["atr"], pip, RR, 0)
                if t and s["score"] >= MIN_SCORE and (sym not in best or s["score"] > best[sym]["score"]):
                    s.update(t)
                    s.update(sym=sym, pip=pip, ctx_time=df.index[-1].isoformat())
                    best[sym] = s
        for s in sorted(best.values(), key=lambda x: -x["score"]):
            if day["sent"] >= MAX_PER_DAY:
                break
            side = "شراء 🟢" if s["dir"] == 1 else "بيع 🔴"
            tg(f"⚡ إشارة {side}\n"
               f"الأداة: {s['sym']} | فريم {ENTRY_TF}\n"
               f"الاستراتيجية: {s['name']} (نقاط {s['score']:.0f})\n"
               f"الدخول: {s['entry']:.5g}\n"
               f"الستوب: {s['sl']:.5g} ({s['risk_p']:.0f} بيب)\n"
               f"TP1: {s['tp1']:.5g} ({s['tp1_p']:.0f} بيب)\n"
               f"TP2: {s['tp2']:.5g} ({s['tp2_p']:.0f} بيب)\n"
               f"السبب: {s['why']}\n"
               f"راجع السعر عند وسيطك قبل الدخول. بعد TP1 انقل الستوب للدخول.")
            st["open"].append({"sym": s["sym"], "dir": s["dir"], "entry": s["entry"], "sl": s["sl"],
                               "tp1": s["tp1"], "tp2": s["tp2"], "rr": RR, "name": s["name"],
                               "time": s["ctx_time"]})
            st["last"][s["sym"]] = now.isoformat()
            day["sent"] += 1

    if now.hour >= 21 and not day["summary"]:
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | "
           f"خاسرة: {day['losses']} | صافي: {day['r']:+.1f}R")
        day["summary"] = True
    save(st)


if __name__ == "__main__":
    main()
