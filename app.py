import re
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import yfinance as yf
from openai import OpenAI

st.set_page_config(page_title="ماسح الإشارات", page_icon="📈", layout="centered")

INSTRUMENTS = {
    "XAUUSD (الذهب)": {"ticker": "GC=F", "spot": "XAUUSD=X", "pip": 0.1},
    "GBP/JPY": {"ticker": "GBPJPY=X", "spot": "GBPJPY=X", "pip": 0.01},
    "EUR/USD": {"ticker": "EURUSD=X", "spot": "EURUSD=X", "pip": 0.0001},
    "GBP/USD": {"ticker": "GBPUSD=X", "spot": "GBPUSD=X", "pip": 0.0001},
    "BTC/USD": {"ticker": "BTC-USD", "spot": "BTC-USD", "pip": 1.0},
    "NASDAQ (NQ)": {"ticker": "NQ=F", "spot": "^NDX", "pip": 1.0},
    "DOW JONES (YM)": {"ticker": "YM=F", "spot": "^DJI", "pip": 1.0},
}
TF_CFG = {"D1": ("1d", "1y"), "H4": ("1h", "60d"), "H1": ("1h", "30d"), "M15": ("15m", "10d")}
MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


# ---------------- البيانات والمؤشرات ----------------
@st.cache_data(ttl=300, show_spinner=False)
def get_df(ticker, tf):
    interval, period = TF_CFG[tf]
    df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if tf == "H4":
        df = df.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return df


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


def arrow(t):
    return "⬆️ صاعد" if t > 0 else "⬇️ هابط" if t < 0 else "↔️ عرضي"


def ask_ai(key, prompt):
    client = OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=key, timeout=60)
    last = None
    for m in MODELS:
        try:
            r = client.chat.completions.create(model=m, temperature=0.3, max_tokens=900, messages=[
                {"role": "system", "content": "أنت متداول محترف. راجع الصفقة المقترحة بإيجاز بالعربية: نقاط القوة، المخاطر، وشرط الإلغاء. لا تغيّر الأرقام ولا تخترع أرقاماً."},
                {"role": "user", "content": prompt}])
            return r.choices[0].message.content or ""
        except Exception as e:
            last = e
    raise RuntimeError(str(last)[:300])


# ---------------- الواجهة ----------------
st.title("📈 ماسح الإشارات متعدد الاستراتيجيات")
st.caption("8 استراتيجيات تُفحص بالكود: إذا ما طابقت وحدة بتنتقل للتانية، وبيطلع أقوى تجمّع تأكيدات")

with st.sidebar:
    st.header("الإعدادات")
    api_key = st.text_input("مفتاح Gemini (اختياري للشرح)", type="password")

name = st.selectbox("الأداة", list(INSTRUMENTS.keys()))
src_choice = st.radio("مصدر السعر", ["فوري/مؤشر (أقرب لوسيطك)", "عقود آجلة"], horizontal=True)
tf_entry = st.selectbox("فريم الدخول", ["M15", "H1", "H4"], index=1)
c1, c2 = st.columns(2)
mode = c1.select_slider("الصرامة", ["نشط", "متوازن", "صارم"], value="متوازن")
rr = c2.slider("R:R للهدف الأول", 1.0, 3.0, 1.5, 0.1)
min_tp = st.number_input("أقل هدف TP1 بالبيب (0 = بدون شرط)", min_value=0.0, value=0.0, step=10.0)
broker_price = st.number_input("سعر وسيطك الحالي (اختياري لمطابقة الأسعار)", min_value=0.0, value=0.0,
                               step=0.01, format="%.4f")
use_ai = st.checkbox("شرح من Gemini (اختياري)", value=False)
go = st.button("افحص السوق", type="primary", use_container_width=True)

if go:
    inst = INSTRUMENTS[name]
    pip = inst["pip"]
    offset = 0.0
    cands = [inst["spot"], inst["ticker"]] if src_choice.startswith("فوري") else [inst["ticker"]]
    cands = list(dict.fromkeys(cands))
    with st.spinner("جاري جلب البيانات وتشغيل الاستراتيجيات..."):
        tk, data = None, {}
        for cand in cands:
            tmp = {}
            for tf in dict.fromkeys([tf_entry, "H4", "D1"]):
                df = get_df(cand, tf)
                if df is None or len(df) < 60:
                    tmp = None
                    break
                tmp[tf] = df
            if tmp:
                tk, data = cand, tmp
                break
        if tk is None:
            st.error("ما قدرت أجيب بيانات كافية. السوق ممكن يكون مسكر أو المصدر تعطل، جرب بعد شوي.")
            st.stop()
        if tk != cands[0]:
            st.warning(f"بيانات {cands[0]} غير متاحة، استخدمت {tk} بدالها (ممكن يختلف السعر عن وسيطك).")
        if broker_price > 0:
            offset = broker_price - float(data[tf_entry]["Close"].iloc[-1])
        data = {k: v + offset for k, v in data.items()}
        b4, b1d = trend(data["H4"]), trend(data["D1"])
        setups, ctx = scan(data[tf_entry], b4, b1d, "BTC" in name)

    price, atr = ctx["price"], ctx["atr"]
    st.subheader("سياق السوق")
    m1, m2, m3 = st.columns(3)
    m1.metric("السعر", fmt(price, pip))
    m2.metric("RSI", f"{ctx['rsi']:.0f}")
    m3.metric("ATR", fmt(atr, pip))
    st.caption(f"مصدر البيانات: {tk}" + (f" | تم تعديل الأسعار بفرق {offset:+.2f} لتطابق سعرك" if offset else " | قارن السعر مع شارت وسيطك، وإذا في فرق اكتب سعر وسيطك بالخانة"))
    st.write(f"**D1:** {arrow(b1d)} | **H4:** {arrow(b4)} | **{tf_entry}:** {arrow(ctx['trend'])}")
    sup = sorted([z for z in ctx["zones"] if z["hi"] < price], key=lambda z: price - z["hi"])[:3]
    res = sorted([z for z in ctx["zones"] if z["lo"] > price], key=lambda z: z["lo"] - price)[:3]
    st.write("**مناطق ارتداد (دعم):** " + (" ، ".join(f"{fmt(z['lo'], pip)}-{fmt(z['hi'], pip)} ({z['n']}x)" for z in sup) or "لا يوجد"))
    st.write("**مناطق ارتداد (مقاومة):** " + (" ، ".join(f"{fmt(z['lo'], pip)}-{fmt(z['hi'], pip)} ({z['n']}x)" for z in res) or "لا يوجد"))

    trades, rejected = [], []
    for s in setups:
        t, why_no = build(s, price, atr, pip, rr, min_tp)
        if t:
            s.update(t)
            trades.append(s)
        else:
            rejected.append(f"{s['name']}: {why_no}")
    thr = {"نشط": 2, "متوازن": 4, "صارم": 6}[mode]
    good = sorted([s for s in trades if s["score"] >= thr], key=lambda s: -s["score"])

    st.subheader("النتيجة")
    if not good:
        st.warning(f"ما في إشارة توصل لحد الصرامة ({mode}) هلأ.")
        if trades:
            b = max(trades, key=lambda s: s["score"])
            st.info(f"أعلى مرشح: {b['name']} ({'شراء' if b['dir'] > 0 else 'بيع'}) بنقاط {b['score']}. جرّب تنزّل الصرامة لـ نشط.")
    else:
        b = good[0]
        side = "شراء 🟢" if b["dir"] > 0 else "بيع 🔴"
        st.success(f"{side} — {b['name']} — نقاط التأكيد: {b['score']}")
        st.write(b["why"])
        a1, a2 = st.columns(2)
        a1.metric("الدخول (سوق)", fmt(b["entry"], pip))
        a2.metric("وقف الخسارة", fmt(b["sl"], pip), f"-{b['risk_p']:.0f} بيب", delta_color="off")
        a3, a4 = st.columns(2)
        a3.metric("TP1", fmt(b["tp1"], pip), f"{b['tp1_p']:.0f} بيب")
        a4.metric("TP2", fmt(b["tp2"], pip), f"{b['tp2_p']:.0f} بيب")
        st.caption("الدخول على سعر السوق الحالي. للدخول الأدق انتظر رجوع السعر لمنطقة الدخول.")
        if len(good) > 1:
            st.write("**إشارات أخرى:**")
            st.dataframe(pd.DataFrame([{
                "الاستراتيجية": s["name"], "الاتجاه": "شراء" if s["dir"] > 0 else "بيع",
                "الستوب": fmt(s["sl"], pip), "TP1": fmt(s["tp1"], pip), "النقاط": s["score"]} for s in good[1:]]),
                hide_index=True)
        if use_ai and api_key:
            with st.spinner("Gemini عم يراجع..."):
                try:
                    st.markdown("**مراجعة Gemini:**")
                    st.write(ask_ai(api_key, f"الأداة {name}، فريم {tf_entry}. السعر {fmt(price, pip)}. "
                                    f"D1 {arrow(b1d)}، H4 {arrow(b4)}. الصفقة: {side} {b['name']} دخول {fmt(b['entry'], pip)} "
                                    f"ستوب {fmt(b['sl'], pip)} TP1 {fmt(b['tp1'], pip)}. السبب: {b['why']}"))
                except Exception as e:
                    st.warning(f"تعذّر شرح Gemini (الإشارة أعلاه سليمة): {e}")
    if rejected:
        with st.expander("مرشحات انرفضت بفحص الكود"):
            st.write("\n".join(f"- {r}" for r in rejected))
    st.caption("تحليل مساعد وليس توصية مالية. جرّب على حساب تجريبي وراجع الأسعار على شارت وسيطك.")
