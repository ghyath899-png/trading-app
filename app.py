import json
import re
import pandas as pd
import streamlit as st
import yfinance as yf
from openai import OpenAI

st.set_page_config(page_title="محلل الإشارات", page_icon="📈", layout="centered")

# ---------------- الأدوات ----------------
# ticker = رمز yfinance ، pip = حجم البيب/النقطة
INSTRUMENTS = {
    "XAUUSD (الذهب)": {"ticker": "GC=F", "pip": 0.1},
    "GBP/JPY": {"ticker": "GBPJPY=X", "pip": 0.01},
    "EUR/USD": {"ticker": "EURUSD=X", "pip": 0.0001},
    "GBP/USD": {"ticker": "GBPUSD=X", "pip": 0.0001},
    "BTC/USD": {"ticker": "BTC-USD", "pip": 1.0},
    "NASDAQ (NQ)": {"ticker": "NQ=F", "pip": 1.0},
    "DOW JONES (YM)": {"ticker": "YM=F", "pip": 1.0},
}

# اسم الفريم: (interval, period)
TF_CFG = {
    "D1": ("1d", "1y"),
    "H4": ("1h", "60d"),   # نجمّعها من H1 إلى 4 ساعات
    "H1": ("1h", "30d"),
    "M15": ("15m", "10d"),
}

MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]


# ---------------- البيانات ----------------
@st.cache_data(ttl=300, show_spinner=False)
def get_df(ticker, tf):
    interval, period = TF_CFG[tf]
    df = yf.download(ticker, interval=interval, period=period,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if tf == "H4":
        df = df.resample("4h").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        ).dropna()
    return df


# ---------------- المؤشرات ----------------
def add_indicators(df):
    df = df.copy()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    delta = df["Close"].diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + up / down)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df


def find_swings(df, n=3):
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(n, len(df) - n):
        if h[i] == max(h[i - n:i + n + 1]):
            highs.append((df.index[i], float(h[i])))
        if l[i] == min(l[i - n:i + n + 1]):
            lows.append((df.index[i], float(l[i])))
    return highs, lows


def find_fvg(df, lookback=80):
    out = []
    sub = df.iloc[-lookback:]
    hi, lo, idx = sub["High"].values, sub["Low"].values, sub.index
    for i in range(2, len(sub)):
        if lo[i] > hi[i - 2]:  # فجوة صاعدة
            zone = (float(hi[i - 2]), float(lo[i]))
            if all(lo[j] > zone[0] for j in range(i + 1, len(sub))):
                out.append(("صاعدة", idx[i], zone))
        if hi[i] < lo[i - 2]:  # فجوة هابطة
            zone = (float(hi[i]), float(lo[i - 2]))
            if all(hi[j] < zone[1] for j in range(i + 1, len(sub))):
                out.append(("هابطة", idx[i], zone))
    return out[-3:]


def fmt(x, pip):
    return f"{x:.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"


def summarize(df, tf, pip):
    df = add_indicators(df)
    last = df.iloc[-1]
    atr = float(last["ATR"])
    highs, lows = find_swings(df)
    trend = "غير واضح/عرضي"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1] > lows[-2][1]
        if hh and hl:
            trend = "صاعد (قمم وقيعان أعلى)"
        elif (not hh) and (not hl):
            trend = "هابط (قمم وقيعان أدنى)"
    close = float(last["Close"])
    bos = "لا يوجد كسر حديث"
    if highs and close > highs[-1][1]:
        bos = f"إغلاق فوق آخر قمة {fmt(highs[-1][1], pip)} (BOS صاعد)"
    elif lows and close < lows[-1][1]:
        bos = f"إغلاق تحت آخر قاع {fmt(lows[-1][1], pip)} (BOS هابط)"

    tol = 0.15 * atr
    eq_h = [round(a[1], 4) for a in highs[-8:] for b in highs[-8:]
            if a[0] < b[0] and abs(a[1] - b[1]) <= tol]
    eq_l = [round(a[1], 4) for a in lows[-8:] for b in lows[-8:]
            if a[0] < b[0] and abs(a[1] - b[1]) <= tol]

    fvg = find_fvg(df)
    lines = [
        f"--- فريم {tf} ---",
        f"آخر إغلاق: {fmt(close, pip)} | ATR: {fmt(atr, pip)}",
        f"EMA50: {fmt(float(last['EMA50']), pip)} | EMA200: {fmt(float(last['EMA200']), pip)}",
        f"RSI: {float(last['RSI']):.1f}",
        f"الاتجاه حسب الهيكل: {trend}",
        f"آخر 3 قمم: {[fmt(x[1], pip) for x in highs[-3:]]}",
        f"آخر 3 قيعان: {[fmt(x[1], pip) for x in lows[-3:]]}",
        f"كسر الهيكل: {bos}",
        f"سيولة قمم متساوية: {sorted(set(eq_h)) or 'لا يوجد'}",
        f"سيولة قيعان متساوية: {sorted(set(eq_l)) or 'لا يوجد'}",
        "فجوات FVG غير مغلقة: " + (
            "، ".join(f"{k} [{fmt(z[0], pip)} - {fmt(z[1], pip)}]" for k, _, z in fvg)
            if fvg else "لا يوجد"),
    ]
    if tf == "D1" and len(df) > 2:
        pd_ = df.iloc[-2]
        lines.append(f"قمة أمس: {fmt(float(pd_['High']), pip)} | قاع أمس: {fmt(float(pd_['Low']), pip)}")
    return "\n".join(lines), close, atr


# ---------------- الذكاء الاصطناعي ----------------
SYSTEM = """أنت متداول محترف تعتمد SMC/ICT وPrice Action.
ستصلك بيانات محسوبة بالكود من أسعار حقيقية لعدة فريمات. لا تخترع أي رقم غير موجود فيها، واعتمد فقط عليها.
القواعد:
- التحليل من الأكبر للأصغر: D1/H4 للاتجاه، H1 للمنطقة، M15 للدخول. لا تعاكس اتجاه الفريم الأكبر.
- الستوب خلف آخر قمة/قاع منطقي (وقرب 1-2 ATR من فريم H1 كمرجع)، وليس رقماً عشوائياً.
- TP1 بين 100 و150 بيب، وTP2 بين 200 و300 بيب، وR:R لا يقل عن 1:2.
- إذا لم تكتمل الشروط، القرار "no_trade". لا تجبر نفسك على صفقة.
أجب بـ JSON فقط بدون أي نص آخر وبدون ```:
{"decision":"trade" أو "no_trade",
 "direction":"Buy" أو "Sell" أو "",
 "order_type":"Market" أو "Limit" أو "",
 "entry":رقم,"sl":رقم,"tp1":رقم,"tp2":رقم,
 "analysis":"شرح مختصر بالعربية لسياق السوق والسبب",
 "invalidation":"متى يبطل التحليل"}"""


def ask_gemini(api_key, prompt):
    client = OpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=api_key, timeout=90)
    last = None
    for m in MODELS:
        try:
            r = client.chat.completions.create(
                model=m, temperature=0.2, max_tokens=2500,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}])
            txt = r.choices[0].message.content or ""
            txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
            return json.loads(txt), m
        except Exception as e:
            last = e
    raise RuntimeError(f"فشلت كل الموديلات. آخر خطأ: {last}")


def validate(sig, pip):
    """فحص بالكود: اتجاه منطقي، R:R، وهدف لا يقل عن 100 بيب"""
    try:
        e, sl, t1, t2 = (float(sig[k]) for k in ("entry", "sl", "tp1", "tp2"))
    except Exception:
        return False, "أرقام الصفقة ناقصة", {}
    d = sig.get("direction")
    if d == "Buy" and not (sl < e < t1 <= t2):
        return False, "ترتيب الأسعار غير منطقي لصفقة شراء", {}
    if d == "Sell" and not (sl > e > t1 >= t2):
        return False, "ترتيب الأسعار غير منطقي لصفقة بيع", {}
    if d not in ("Buy", "Sell"):
        return False, "اتجاه غير معروف", {}
    risk = abs(e - sl)
    rr1, rr2 = abs(t1 - e) / risk, abs(t2 - e) / risk
    info = {
        "risk_pips": risk / pip,
        "tp1_pips": abs(t1 - e) / pip,
        "tp2_pips": abs(t2 - e) / pip,
        "rr1": rr1, "rr2": rr2,
    }
    if rr1 < 2:
        return False, f"R:R للهدف الأول {rr1:.2f} أقل من 2", info
    if info["tp1_pips"] < 100:
        return False, f"الهدف الأول {info['tp1_pips']:.0f} بيب أقل من 100", info
    return True, "الصفقة اجتازت فحص الكود", info


# ---------------- الواجهة ----------------
st.title("📈 محلل الإشارات")
st.caption("بيانات حقيقية + مؤشرات محسوبة بالكود + قرار من Gemini + فحص R:R بالكود")

with st.sidebar:
    st.header("الإعدادات")
    api_key = st.text_input("مفتاح Gemini", type="password",
                            value=st.secrets.get("GEMINI_API_KEY", "") if hasattr(st, "secrets") else "")
    st.caption("المفتاح بيضل بجهازك ومو بينحفظ بأي ملف.")

name = st.selectbox("الأداة", list(INSTRUMENTS.keys()))
go = st.button("أعطني إشارة", type="primary", use_container_width=True)

if go:
    if not api_key:
        st.error("حط مفتاح Gemini بالقائمة الجانبية (السهم أعلى اليسار).")
        st.stop()

    inst = INSTRUMENTS[name]
    pip = inst["pip"]
    parts, price, h1_atr = [], None, None

    with st.spinner("جاري جلب البيانات وحساب المؤشرات..."):
        for tf in ["D1", "H4", "H1", "M15"]:
            df = get_df(inst["ticker"], tf)
            if df is None or len(df) < 60:
                st.error(f"ما قدرت أجيب بيانات كافية لفريم {tf}. السوق ممكن يكون مسكر، جرب بعد شوي.")
                st.stop()
            txt, close, atr = summarize(df, tf, pip)
            parts.append(txt)
            if tf == "M15":
                price = close
            if tf == "H1":
                h1_atr = atr

    prompt = (
        f"الأداة: {name}\nحجم البيب المعتمد: {pip} (1 بيب = {pip} من السعر)\n"
        f"السعر الحالي: {fmt(price, pip)}\nATR فريم H1: {fmt(h1_atr, pip)}\n\n"
        + "\n\n".join(parts)
    )

    with st.expander("البيانات اللي انبعتت للذكاء الاصطناعي"):
        st.text(prompt)

    with st.spinner("Gemini عم يحلل..."):
        try:
            sig, model_used = ask_gemini(api_key, prompt)
        except Exception as e:
            st.error(f"خطأ: {str(e)[:500]}")
            st.stop()

    st.subheader("التحليل")
    st.write(sig.get("analysis", ""))

    if sig.get("decision") != "trade":
        st.warning("لا توجد فرصة متوافقة مع القواعد حالياً.")
    else:
        ok, msg, info = validate(sig, pip)
        if not ok:
            st.error(f"❌ رفض الكود الصفقة: {msg}")
        else:
            st.success(f"✅ {sig['direction']} ({sig.get('order_type', '')}) — {msg}")
            c1, c2 = st.columns(2)
            c1.metric("الدخول", sig["entry"])
            c2.metric("وقف الخسارة", sig["sl"], f"-{info['risk_pips']:.0f} بيب", delta_color="off")
            c3, c4 = st.columns(2)
            c3.metric("TP1", sig["tp1"], f"{info['tp1_pips']:.0f} بيب | R:R {info['rr1']:.1f}")
            c4.metric("TP2", sig["tp2"], f"{info['tp2_pips']:.0f} بيب | R:R {info['rr2']:.1f}")
        if sig.get("invalidation"):
            st.info(f"شرط الإلغاء: {sig['invalidation']}")

    st.caption(f"الموديل: {model_used} | هاد تحليل مساعد وليس توصية مالية. جرّبه على حساب تجريبي أولاً.")
