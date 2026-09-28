import pandas as pd
import pandas_ta as ta
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ==========================================
# 1. إعدادات الصفحة والتحديث الآلي
# ==========================================
st.set_page_config(
    page_title="نظام صائد السيولة والانعكاس القوي", layout="wide"
)
st.title("🛡️ نظام صائد الصفقات القوية (SMC Sweeps & FVG Reversals)")

# تحديث آلي كل 10 ثوانٍ للقط الحركة اللحظية
st_autorefresh(interval=10000, key="datarefresh")

# ==========================================
# 2. المفاتيح والإعدادات (مدمج فيها مفتاحك)
# ==========================================
TWELVE_DATA_API_KEY = "3a962e7a32d34a53839c028ca49ff7b5"

st.sidebar.header("⚙️ إعدادات التليجرام (اختياري)")
TELEGRAM_BOT_TOKEN = st.sidebar.text_input(
    "Telegram Bot Token", value="", type="password"
)
TELEGRAM_CHAT_ID = st.sidebar.text_input("Telegram Chat ID", value="")


def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            st.error(f"خطأ في إرسال التليجرام: {e}")


# ==========================================
# 3. جلب بيانات الأسعار المباشرة عبر Twelve Data API
# ==========================================
def get_live_data(symbol, interval="5min", outputsize=100):
    symbol_map = {"XAUUSD": "XAU/USD", "USTEC": "IXIC", "EURUSD": "EUR/USD"}
    api_symbol = symbol_map.get(symbol, symbol)

    url = f"https://api.twelvedata.com/time_series?symbol={api_symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"

    try:
        response = requests.get(url).json()
        if "values" not in response:
            st.error(
                f"خطأ في جلب البيانات: {response.get('message', 'يرجى التأكد من الرمز')}"
            )
            return None

        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"خطأ في الاتصال: {e}")
        return None


# ==========================================
# 4. خوارزمية تحليل السيولة (Liquidity Sweeps & FVG)
# ==========================================
def analyze_smc_setup(df):
    if len(df) < 30:
        return None

    # حساب القمم والقيعان المرجعية (خلال آخر 20 شمعة)
    recent_high = df["high"].iloc[-25:-3].max()
    recent_low = df["low"].iloc[-25:-3].min()

    c1 = df.iloc[-3]  # الشمعة الأولى
    c2 = df.iloc[-2]  # شمعة سحب السيولة
    c3 = df.iloc[-1]  # الشمعة الحالية

    signal = "NEUTRAL"
    sl = 0.0
    tp = 0.0
    reason = ""

    # --- 1. فرصة شراء (Bullish Sweep + FVG) ---
    liquidity_sweep_buy = (c2["low"] < recent_low) and (c2["close"] > recent_low)
    fvg_buy = c3["low"] > c1["high"]

    if liquidity_sweep_buy and fvg_buy:
        signal = "BUY"
        sl = c2["low"] - 0.50  # أسفل الذيل بـ 50 بيب
        risk = c3["close"] - sl
        tp = c3["close"] + (risk * 2.0)  # هدف بنسبة 1:2
        reason = "سحب سيولة قاع سابق بالذيل (Sweep) + فجوة سعرية (FVG)"

    # --- 2. فرصة بيع (Bearish Sweep + FVG) ---
    liquidity_sweep_sell = (c2["high"] > recent_high) and (
        c2["close"] < recent_high
    )
    fvg_sell = c3["high"] < c1["low"]

    if liquidity_sweep_sell and fvg_sell:
        signal = "SELL"
        sl = c2["high"] + 0.50  # أعلى الذيل بـ 50 بيب
        risk = sl - c3["close"]
        tp = c3["close"] - (risk * 2.0)  # هدف بنسبة 1:2
        reason = "سحب سيولة قمة سابقة بالذيل (Sweep) + فجوة سعرية (FVG)"

    return signal, sl, tp, reason, recent_high, recent_low


# ==========================================
# 5. واجهة العرض والتنفيذ
# ==========================================
symbol = st.sidebar.selectbox("اختر الرمز", ["XAUUSD", "USTEC", "EURUSD"])
tf = st.sidebar.selectbox(
    "الإطار الزمني (ينصح بـ 5m للسكالبينج)", ["5min", "15min"]
)

df = get_live_data(symbol, interval=tf)

if df is not None:
    current_price = df["close"].iloc[-1]
    res = analyze_smc_setup(df)

    if res:
        signal, sl, tp, reason, rec_high, rec_low = res

        col1, col2, col3 = st.columns(3)
        col1.metric("السعر الحالي المباشر", f"{current_price:.2f}")
        col2.metric("قمة السيولة القريبة", f"{rec_high:.2f}")
        col3.metric("قاع السيولة القريب", f"{rec_low:.2f}")

        st.subheader("🎯 حالة الفرص المتاحة الآن")

        if signal in ["BUY", "SELL"]:
            st.success(f"🔥 صفقة انعكاسية عالية الجودة: {signal} ({symbol})")
            st.write(f"**السبب:** {reason}")
            st.write(
                f"**سعر الدخول:** {current_price:.2f} | **وقف الخسارة (SL):** {sl:.2f} | **الهدف (TP):** {tp:.2f}"
            )

            send_telegram_message(
                f"🚨 صفقة SMC جديدة على {symbol} ({tf})\nالنوع: {signal}\nالدخول: {current_price:.2f}\nالستوب: {sl:.2f}\nالهدف: {tp:.2f}\nالسبب: {reason}"
            )
        else:
            st.info(
                "🛡️ لا توجد تركيبة اكتمال سيولة (Sweep + FVG) حالياً. يتم فحص الشموع كل 10 ثوانٍ لاقتناص أي فرصة انعكاسية قوية."
            )
