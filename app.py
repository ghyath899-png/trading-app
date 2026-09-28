import time
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import ta

# ==========================================
# 1. إعدادات الصفحة وجلب المفاتيح بأمان
# ==========================================
st.set_page_config(
    page_title="SMC Sweeps & FVG Trading Bot", page_icon="📈", layout="wide"
)

# جلب بيانات التليجرام ومفتاح API بأمان من Streamlit Secrets
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY", "3a962e7a32d34a53839c028ca49ff7b5"
)

# تحديث الشاشة تلقائياً كل 10 ثوانٍ
st_autorefresh(interval=10000, key="datarefresh")

# ==========================================
# 2. القائمة الجانبية (Sidebar)
# ==========================================
st.sidebar.title("⚙️ إعدادات المنظومة")

# خيارات استثنائية لإدخال المفاتيح يدويًا إن لم تكن في Secrets
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = st.sidebar.text_input(
        "Telegram Bot Token", type="password"
    )
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = st.sidebar.text_input("Telegram Chat ID")

# قائمة اختيار الأزواج المدعومة بالتفصيل
symbol_display = st.sidebar.selectbox(
    "اختر الزوج / الأصل",
    [
        "XAUUSD (الذهب)",
        "USTEC (ناسداك)",
        "BTCUSD (البيتكوين)",
        "GBPJPY (الباوند ين)",
        "EURUSD (اليورو دولار)",
    ],
)

# ربط الرمز المختار بررمز Twelve Data الصحيح
symbol_map = {
    "XAUUSD (الذهب)": "XAU/USD",
    "USTEC (ناسداك)": "NDX",
    "BTCUSD (البيتكوين)": "BTC/USD",
    "GBPJPY (الباوند ين)": "GBP/JPY",
    "EURUSD (اليورو دولار)": "EUR/USD",
}
selected_symbol = symbol_map[symbol_display]

timeframe = st.sidebar.selectbox(
    "الإطار الزمني (Interval)", ["5min", "15min", "1h"]
)

# ==========================================
# دالة إرسال الإشعارات للتليجرام
# ==========================================
def send_telegram_alert(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            st.error(f"فشل إرسال التنبيه عبر تليجرام: {e}")

# ==========================================
# زر اختبار التليجرام (مضاف هنا في القائمة الجانبية)
# ==========================================
st.sidebar.markdown("---")
if st.sidebar.button("🧪 اختبار إرسال التليجرام"):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        test_msg = "✅ *تم الاتصال بنجاح!*\n\nنظام صائد الصفقات جاهز ومربوط بحسابك، ستصلك التنبيهات هنا فور تحقق الشروط."
        send_telegram_alert(test_msg)
        st.sidebar.success("تم إرسال رسالة تجريبية! تحقق من التليجرام.")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")

# ==========================================
# 3. دالة جلب البيانات الحية من Twelve Data
# ==========================================
@st.cache_data(ttl=8)
def get_live_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=50&apikey={TWELVE_DATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if "values" in data:
            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])

            # تحويل الأعمدة الأساسية المضمونة فقط
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            # تحويل الفوليوم إذا كان موجوداً فقط
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

            df = df.sort_values("datetime").reset_index(drop=True)
            return df
        else:
            st.error(
                f"خطأ في جلب البيانات: {data.get('message', 'رمز غير صالح')}"
            )
            return None
    except Exception as e:
        st.error(f"حدث خطأ أثناء الاتصال بالشبكة: {e}")
        return None

# ==========================================
# 4. الواجهة الرئيسية والتحليل
# ==========================================
st.title("🛡️ نظام صائد الصفقات القوية (SMC Sweeps & FVG Reversals)")
st.caption(f"الرمز المالي المختار حالياً: **{symbol_display}** | الفريم: **{timeframe}**")

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 10:
    # الحصول على الأسعار الحالية
    current_price = df["close"].iloc[-1]
    high_liquidity = df["high"].tail(10).max()
    low_liquidity = df["low"].tail(10).min()

    # عرض الأسعار الحالية والمستويات
    col1, col2, col3 = st.columns(3)
    col1.metric("السعر الحالي المباشر", f"{current_price:.2f}")
    col2.metric("قمة السيولة القريبة (Sweep High)", f"{high_liquidity:.2f}")
    col3.metric("قاع السيولة القريب (Sweep Low)", f"{low_liquidity:.2f}")

    st.markdown("---")
    st.subheader("🎯 حالة الفرص المتاحة الآن")

    # خوارزمية تحليل الـ SMC (سحب السيولة Sweep + FVG)
    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    third_candle = df.iloc[-3]

    signal = None

    # شرط الشراء (Bullish Sweep + FVG)
    if (
        prev_candle["low"] < low_liquidity
        and last_candle["close"] > prev_candle["low"]
    ):
        if last_candle["low"] > third_candle["high"]:  # وجود FVG صاعد
            signal = "BUY"
            entry = current_price
            sl = prev_candle["low"]
            tp = entry + (entry - sl) * 2

    # شرط البيع (Bearish Sweep + FVG)
    elif (
        prev_candle["high"] > high_liquidity
        and last_candle["close"] < prev_candle["high"]
    ):
        if last_candle["high"] < third_candle["low"]:  # وجود FVG هابط
            signal = "SELL"
            entry = current_price
            sl = prev_candle["high"]
            tp = entry - (sl - entry) * 2

    if signal:
        st.success(f"🚨 تم اكتشاف فرصة دخول {signal} قوية على {symbol_display}!")
        st.write(f"**سعر الدخول:** {entry:.2f}")
        st.write(f"**وقف الخسارة (SL):** {sl:.2f}")
        st.write(f"**الهدف (TP):** {tp:.2f}")

        # تجهيز وإرسال رسالة التليجرام
        msg = (
            f"🚨 *فرصة تداول SMC جديدة!*\n\n"
            f"📌 *الزوج:* {symbol_display}\n"
            f"⏱️ *الفريم:* {timeframe}\n"
            f"🟢 *الاتجاه:* {signal}\n"
            f"💵 *سعر الدخول:* {entry:.2f}\n"
            f"🛑 *وقف الخسارة:* {sl:.2f}\n"
            f"🎯 *الهدف:* {tp:.2f}"
        )
        send_telegram_alert(msg)
    else:
        st.info(
            "حالياً لا توجد تركيبة اكتمال سيولة (Sweep + FVG). يتم فحص الشموع تلقائياً كل 10 ثوانٍ لاقتناص أي فرصة انعكاسية قوية."
        )

    # عرض جدول البيانات الحية المحدثة
    with st.expander("📊 عرض آخر الشموع المحدثة"):
        cols_to_show = [
            c
            for c in ["datetime", "open", "high", "low", "close", "volume"]
            if c in df.columns
        ]
        st.dataframe(df[cols_to_show].tail(10))
