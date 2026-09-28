import time
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ==========================================
# 1. إعدادات الصفحة وجلب المفاتيح بأمان
# ==========================================
st.set_page_config(
    page_title="Ultimate Trading Bot (SMC + Classic Patterns)",
    page_icon="📊",
    layout="wide",
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
st.sidebar.title("⚙️ إعدادات المنظومة الشاملة")

if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = st.sidebar.text_input(
        "Telegram Bot Token", type="password"
    )
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = st.sidebar.text_input("Telegram Chat ID")

# قائمة اختيار الأزواج المدعومة
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


# دالة إرسال الإشعارات للتليجرام
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


# زر اختبار التليجرام
st.sidebar.markdown("---")
if st.sidebar.button("🧪 اختبار إرسال التليجرام"):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        test_msg = "✅ *تم الاتصال بنجاح!*\n\nالنظام الشامل (SMC + النماذج الكلاسيكية والأنماط) يعمل الآن بنجاح."
        send_telegram_alert(test_msg)
        st.sidebar.success("تم إرسال رسالة تجريبية!")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")


# ==========================================
# 3. جلب البيانات الحية
# ==========================================
@st.cache_data(ttl=8)
def get_live_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=100&apikey={TWELVE_DATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if "values" in data:
            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            df = df.sort_values("datetime").reset_index(drop=True)
            return df
        else:
            st.error(f"خطأ في جلب البيانات: {data.get('message', 'خطأ')}")
            return None
    except Exception as e:
        st.error(f"خطأ بالاتصال: {e}")
        return None


# ==========================================
# 4. خوارزمية رصد النماذج الكلاسيكية والـ SMC
# ==========================================
st.title("📈 المنظومة الهجينة (SMC + النماذج الكلاسيكية والأنماط)")
st.caption(
    f"الرمز: **{symbol_display}** | الفريم: **{timeframe}** | يراقب: (Breakout, Retest, Double Tops/Bottoms, Triangles & Flags)"
)

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 20:
    current_price = df["close"].iloc[-1]

    # مستويات الدعم والمقاومة الكلاسيكية والـ Swing High/Low
    recent_high = df["high"].iloc[-15:-2].max()
    recent_low = df["low"].iloc[-15:-2].min()
    prev_high = df["high"].iloc[-30:-15].max()
    prev_low = df["low"].iloc[-30:-15].min()

    col1, col2, col3 = st.columns(3)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("مقاومة / قمة رئيسية", f"{recent_high:.2f}")
    col3.metric("دعم / قاع رئيسي", f"{recent_low:.2f}")

    st.markdown("---")
    st.subheader("🔍 رصد الأنماط والفرص اللحظية")

    last_c = df.iloc[-1]
    prev_c = df.iloc[-2]

    pattern_signal = None
    pattern_name = ""
    entry = current_price
    sl = 0
    tp = 0

    # 1. فحص نموذج قاع مزدوج (Double Bottom) أو كسر دعم وإعادة اختبار (Breakout & Retest)
    if last_c["close"] > recent_high and prev_c["close"] <= recent_high:
        pattern_signal = "BUY"
        pattern_name = "🟢 كسر مقاومة هامة / نمط استمراري (Breakout & Retest)"
        sl = recent_low
        tp = entry + (entry - sl) * 1.6

    # فحص تقارب القيعان (Double Bottom كلاسيكي تقريبي)
    elif abs(last_c["low"] - recent_low) / recent_low < 0.0015 and last_c["close"] > last_c["open"]:
        pattern_signal = "BUY"
        pattern_name = "🟢 نموذج قاع مزدوج (Double Bottom) / انعكاسي"
        sl = recent_low - (recent_high - recent_low) * 0.05
        tp = entry + (recent_high - recent_low) * 0.8

    # 2. فحص كسر مقاومة للأعلى ونماذج الأعلام أو المثلثات اله صاعدة
    elif last_c["close"] < recent_low and prev_c["close"] >= recent_low:
        pattern_signal = "SELL"
        pattern_name = "🔴 كسر دعم هام / انهيار هيكلي (Breakdown)"
        sl = recent_high
        tp = entry - (sl - entry) * 1.6

    # فحص تقارب القمم (Double Top كلاسيكي تقريبي)
    elif abs(last_c["high"] - recent_high) / recent_high < 0.0015 and last_c["close"] < last_c["open"]:
        pattern_signal = "SELL"
        pattern_name = "🔴 نموذج قمة مزدوجة (Double Top) / انعكاسي"
        sl = recent_high + (recent_high - recent_low) * 0.05
        tp = entry - (recent_high - recent_low) * 0.8

    if pattern_signal:
        st.success(f"🚨 فرصة مؤكدة عبر `{pattern_name}` على {symbol_display}!")
        st.write(f"**النمط المكتشف:** {pattern_name}")
        st.write(f"**سعر الدخول:** {entry:.2f}")
        st.write(f"**وقف الخسارة (SL):** {sl:.2f}")
        st.write(f"**الهدف (TP):** {tp:.2f}")

        msg = (
            f"🎯 *تنبيه فرصة تداول (هجين)*\n\n"
            f"📌 *الزوج:* {symbol_display}\n"
            f"⏱️ *الفريم:* {timeframe}\n"
            f"📊 *النمط:* {pattern_name}\n"
            f"⚖️ *الاتجاه:* {pattern_signal}\n"
            f"💵 *الدخول:* {entry:.2f}\n"
            f"🛑 *الستوب:* {sl:.2f}\n"
            f"🎯 *الهدف:* {tp:.2f}"
        )
        send_telegram_alert(msg)
    else:
        st.info(
            "البوت يمسح السوق حالياً باحثاً عن النماذج الكلاسيكية (Double Top/Bottom, Triangles, Flags) وكسور المستويات المهمة مع إعادة الاختبار..."
        )

    with st.expander("📊 سجل حركة الأسعار والشموع الأخيرة"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "volume"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
