"""
بوت متداول مساعد وتتبع صفقات الذهب (XAU/USD) - النسخة المتقدمة
المصدر: Twelve Data | التنبيهات: Telegram | حفظ الحالة: state.json
الفريمات: M5 و M15 معاً | الحد الأقصى: صفقة واحدة فقط على XAU/USD
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

SYMBOL = "XAU/USD"
ENTRY_TFS = ["15min", "5min"]
RR = float(env("RR", "1.5"))
MAX_PER_DAY = int(env("MAX_PER_DAY", "6"))
MAX_LOSSES = int(env("MAX_LOSSES", "3"))
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "5"))
EXPIRE_HOURS = float(env("EXPIRE_HOURS", "4"))
PIP = 0.1
STATE_FILE = "state.json"


# ---------------- المؤشرات الفنية والسوينغات ----------------
def prep(df):
    d = df.copy()
    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
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


def swings(d, n=2):
    h, l = d["High"].values, d["Low"].values
    H, L = [], []
    for i in range(n, len(d) - n):
        if h[i] == h[i - n:i + n + 1].max():
            H.append((i, float(h[i])))
        if l[i] == l[i - n:i + n + 1].min():
            L.append((i, float(l[i])))
    return H, L


# ---------------- مسح الاستراتيجيات المتقدمة واقتناص الفرص ----------------
def scan(df, tf_name):
    d = prep(df)
    last = d.iloc[-1]
    prev = d.iloc[-2]
    prev2 = d.iloc[-3]
    price, atr, rsi = float(last["Close"]), float(last["ATR"]), float(last["RSI"])
    H, L = swings(d, n=2)
    out = []

    def add(name, dr, sl, base, why):
        out.append({"name": f"{name} ({tf_name})", "dir": dr, "sl": float(sl), "score": base, "why": why})

    # 1. Fair Value Gap (FVG) - فجوة السعر غير المكتملة
    if len(d) >= 4:
        # FVG صاعد
        if d["Low"].iloc[-1] > d["High"].iloc[-3] and d["Close"].iloc[-2] > d["Open"].iloc[-2]:
            gap_low = d["High"].iloc[-3]
            if price >= gap_low:
                add("Bullish FVG Retest", 1, gap_low - 0.3 * atr, 2,
                    f"اختبار فجوة سعرية صاعدة (FVG) عند {gap_low:.2f} مع اتجاه صاعد")
        # FVG هابط
        if d["High"].iloc[-1] < d["Low"].iloc[-3] and d["Close"].iloc[-2] < d["Open"].iloc[-2]:
            gap_high = d["Low"].iloc[-3]
            if price <= gap_high:
                add("Bearish FVG Retest", -1, gap_high + 0.3 * atr, 2,
                    f"اختبار فجوة سعرية هابطة (FVG) عند {gap_high:.2f} مع اتجاه هابط")

    # 2. Breakout High/Low (كسر أعلى/أقل سعر لأخر 24 شمعة)
    recent_max = d["High"].iloc[-25:-1].max()
    recent_min = d["Low"].iloc[-25:-1].min()

    if price > recent_max and prev["Close"] <= recent_max:
        add("Momentum High Breakout", 1, recent_max - 0.4 * atr, 2,
            f"اختراق زخم لأعلى قمة سابقة عند {recent_max:.2f}")
    elif price < recent_min and prev["Close"] >= recent_min:
        add("Momentum Low Breakout", -1, recent_min + 0.4 * atr, 2,
            f"كسر زخم لأدنى قاع سابق عند {recent_min:.2f}")

    # 3. EMA20/50 Pullback + Rejection Candle
    body = abs(last["Close"] - last["Open"])
    wick_dn = min(last["Close"], last["Open"]) - last["Low"]
    wick_up = last["High"] - max(last["Close"], last["Open"])

    if price > last["EMA50"] and last["Low"] <= last["EMA20"] + 0.2 * atr:
        if wick_dn > 1.2 * body or (last["Close"] > last["Open"] and price > prev["High"]):
            add("EMA Dynamic Bounce", 1, last["Low"] - 0.2 * atr, 2,
                "ارتداد صاعد مع شمعة رفض من المتوسط الديناميكي (EMA)")

    if price < last["EMA50"] and last["High"] >= last["EMA20"] - 0.2 * atr:
        if wick_up > 1.2 * body or (last["Close"] < last["Open"] and price < prev["Low"]):
            add("EMA Dynamic Bounce", -1, last["High"] + 0.2 * atr, 2,
                "ارتداد هابط مع شمعة رفض من المتوسط الديناميكي (EMA)")

    # 4. Liquidity Sweep
    if len(H) >= 1 and len(L) >= 1:
        if d["High"].iloc[-3:].max() > H[-1][1] and price < prev["High"]:
            add("Liquidity Sweep", -1, d["High"].iloc[-3:].max() + 0.2 * atr, 2,
                f"سحب سيولة أعلى {H[-1][1]:.2f} وانعكاس هابط")
        if d["Low"].iloc[-3:].min() < L[-1][1] and price > prev["Low"]:
            add("Liquidity Sweep", 1, d["Low"].iloc[-3:].min() - 0.2 * atr, 2,
                f"سحب سيولة أسفل {L[-1][1]:.2f} وانعكاس صاعد")

    # تقييم الشروط مع الـ RSI
    for s in out:
        dr = s["dir"]
        s["score"] += 1 if (dr == 1 and rsi < 70) or (dr == -1 and rsi > 30) else 0

    return out, atr


def build(s, price, atr):
    dr, sl = s["dir"], s["sl"]
    if (dr == 1 and sl >= price) or (dr == -1 and sl <= price):
        return None, "مستوى الستوب غير صحيح"
    risk = abs(price - sl)
    if risk < 0.25 * atr:
        sl, risk = price - dr * 0.25 * atr, 0.25 * atr
    if risk > 4.5 * atr:
        return None, "الستوب بعيد جداً"
    tp1_p = (RR * risk) / PIP
    return {"entry": price, "sl": sl, "tp1": price + dr * RR * risk, "tp2": price + dr * RR * 1.8 * risk,
            "risk_p": risk / PIP, "tp1_p": tp1_p, "tp2_p": RR * 1.8 * risk / PIP}, ""


# ---------------- جلب البيانات والتنفيذ ----------------
def fetch(symbol):
    r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
        "symbol": symbol, "interval": "5min", "outputsize": 1200,
        "timezone": "UTC", "apikey": TD_KEY})
    j = r.json()
    if "values" not in j:
        print("Twelve Data Error:", j.get("message", j))
        return None
    df = pd.DataFrame(j["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]
    return df[["Open", "High", "Low", "Close"]]


def resample_tf(df, rule):
    if rule == "5min":
        return df
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": [], "days": {}, "last": {}}


def save_state(st):
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
        print("فشل إرسال تيليجرام:", e)


# ---------------- مراقبة الصفقات ونظام EARLY EXIT ----------------
def check_trade_management(st, m5_df, now, day):
    still = []
    for t in st["open"]:
        since = m5_df[m5_df.index > pd.Timestamp(t["time"])]
        res = None
        curr_price = float(m5_df["Close"].iloc[-1])

        # 1. فحص TP1 و SL
        for _, c in since.iterrows():
            if t["dir"] == 1:
                sl_hit, tp_hit = c["Low"] <= t["sl"], c["High"] >= t["tp1"]
            else:
                sl_hit, tp_hit = c["High"] >= t["sl"], c["Low"] <= t["tp1"]
            if sl_hit:
                res = "SL"
                break
            if tp_hit:
                res = "TP1"
                break

        # 2. فحص الخروج المبكر (EARLY EXIT)
        early_reasons = []
        if res is None and len(m5_df) >= 30:
            d = prep(m5_df)
            c1, c2 = d.iloc[-1], d.iloc[-2]
            H, L = swings(d, n=2)

            if t["dir"] == 1:
                if len(L) >= 2 and c1["Close"] < L[-1][1] and L[-1][0] > (len(d) - len(since) - 5):
                    early_reasons.append(f"كسر هيكل السوق (BOS/CHOCH) هبوطاً أسفل {L[-1][1]:.2f}")
                if c1["Close"] < c1["EMA50"] and c1["RSI"] < 45:
                    early_reasons.append("ضعف الزخم وهبوط السعر أسفل EMA50")
                if c1["Close"] < c1["Open"] and (c2["Open"] - c2["Close"]) > 0 and \
                   (c1["Open"] - c1["Close"]) > 1.5 * abs(c2["Close"] - c2["Open"]):
                    early_reasons.append("ظهور شمعة ابتلاعية هابطة حادة")

            elif t["dir"] == -1:
                if len(H) >= 2 and c1["Close"] > H[-1][1] and H[-1][0] > (len(d) - len(since) - 5):
                    early_reasons.append(f"كسر هيكل السوق (BOS/CHOCH) صعوداً أعلى {H[-1][1]:.2f}")
                if c1["Close"] > c1["EMA50"] and c1["RSI"] > 55:
                    early_reasons.append("ضعف الزخم وصعود السعر أعلى EMA50")
                if c1["Close"] > c1["Open"] and (c2["Close"] - c2["Open"]) < 0 and \
                   (c1["Close"] - c1["Open"]) > 1.5 * abs(c2["Open"] - c2["Close"]):
                    early_reasons.append("ظهور شمعة ابتلاعية صاعدة حادة")

            if len(early_reasons) >= 2:
                res = "EARLY_EXIT"

        if res is None and (now - pd.Timestamp(t["time"])).total_seconds() > EXPIRE_HOURS * 3600:
            res = "EXPIRED"

        pnl_pts = (curr_price - t["entry"]) if t["dir"] == 1 else (t["entry"] - curr_price)
        pnl_str = f"{pnl_pts:+.2f} $"
        side_type = "BUY" if t["dir"] == 1 else "SELL"

        if res == "TP1":
            day["wins"] += 1
            day["r"] += t["rr"]
            tg(f"✅ تحقق الهدف الأول (TP1)!\nالرمز: {t['sym']} ({side_type})\nسعر الدخول: {t['entry']:.2f}\nسعر الإغلاق: {t['tp1']:.2f}\n\nيرجى حماية الأرباح واستكمال الاستهداف لنقطة TP2 {t['tp2']:.2f}.")
        elif res == "SL":
            day["losses"] += 1
            day["r"] -= 1.0
            tg(f"❌ ضرب الستوب (SL)\nالرمز: {t['sym']} ({side_type})\nالستوب: {t['sl']:.2f}")
        elif res == "EARLY_EXIT":
            day["losses"] += 1
            day["r"] -= 0.3
            reasons_txt = "\n• " + "\n• ".join(early_reasons)
            tg(f"⚠️ EARLY EXIT – {side_type}\n\n"
               f"السبب: اجتماع أدلة انعكاس مؤكدة:{reasons_txt}\n\n"
               f"الدخول: {t['entry']:.2f}\n"
               f"السعر الحالي: {curr_price:.2f}\n"
               f"الربح/الخسارة الحالية: {pnl_str}\n\n"
               f"TP: {t['tp1']:.2f}\n"
               f"SL: {t['sl']:.2f}\n\n"
               f"توصية: إغلاق الصفقة الآن عند سعر السوق منطقياً وتحرير المراقبة.")
        elif res == "EXPIRED":
            tg(f"⌛ انتهت فترة انتظار الصفقة ({EXPIRE_HOURS}h)\nالرمز: {t['sym']} ({side_type})\nتم تحرير المراقبة لتلقي إشارات جديدة.")
        else:
            still.append(t)

    st["open"] = still


# ---------------- التشغيل الرئيسي ----------------
def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)

    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load_state()
    st.setdefault("open", [])
    st.setdefault("last", {})
    day = st.setdefault("days", {}).setdefault(
        today, {"sent": 0, "wins": 0, "losses": 0, "r": 0.0, "summary": False})

    m5_df = fetch(SYMBOL)
    if m5_df is None or len(m5_df) < 200:
        save_state(st)
        return

    check_trade_management(st, m5_df, now, day)

    if len(st["open"]) == 0 and day["losses"] < MAX_LOSSES and day["sent"] < MAX_PER_DAY:
        last_time = st["last"].get(SYMBOL)
        can_scan = not last_time or (now - pd.Timestamp(last_time)).total_seconds() >= COOLDOWN_MIN * 60

        if can_scan:
            best_signal = None

            for tf in ENTRY_TFS:
                tf_df = resample_tf(m5_df, tf).iloc[:-1]
                if len(tf_df) < 25:
                    continue
                setups, atr = scan(tf_df, tf)
                live_price = float(m5_df["Close"].iloc[-1])

                for s in setups:
                    t, _ = build(s, live_price, atr)
                    if t:
                        if not best_signal or s["score"] > best_signal["score"]:
                            s.update(t)
                            s.update(sym=SYMBOL, ctx_time=m5_df.index[-1].isoformat())
                            best_signal = s

            if best_signal:
                side = "BUY" if best_signal["dir"] == 1 else "SELL"
                tg(f"⚡ إشارة جديدة: {side} 🟢\n" if best_signal["dir"] == 1 else f"⚡ إشارة جديدة: {side} 🔴\n"
                   f"الرمز: {best_signal['sym']}\n"
                   f"النموذج/الاستراتيجية: {best_signal['name']}\n"
                   f"الدخول: {best_signal['entry']:.2f}\n"
                   f"الستوب: {best_signal['sl']:.2f} ({best_signal['risk_p']:.0f} نقطة)\n"
                   f"TP1: {best_signal['tp1']:.2f} ({best_signal['tp1_p']:.0f} نقطة)\n"
                   f"TP2: {best_signal['tp2']:.2f} ({best_signal['tp2_p']:.0f} نقطة)\n"
                   f"التفاصيل: {best_signal['why']}")

                st["open"].append({
                    "sym": best_signal["sym"], "dir": best_signal["dir"], "entry": best_signal["entry"],
                    "sl": best_signal["sl"], "tp1": best_signal["tp1"], "tp2": best_signal["tp2"],
                    "rr": RR, "name": best_signal["name"], "time": best_signal["ctx_time"]
                })
                st["last"][SYMBOL] = now.isoformat()
                day["sent"] += 1

    if now.hour >= 21 and not day["summary"]:
        tg(f"📊 ملخص أداء اليوم {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | "
           f"خاسرة: {day['losses']} | الصافي: {day['r']:+.1f}R")
        day["summary"] = True

    save_state(st)


if __name__ == "__main__":
    main()
