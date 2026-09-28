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
    page_title="Smart Reversal Trading Bot", page_icon="🔄", layout="wide"
)

TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY", "3a962e7a32d34a53839c028ca49ff7b5"
)

st_autorefresh(interval=10000, key="datarefresh")

# ==========================================
# 2. القائمة الجانبية (Sidebar)
# ==========================================
st.sidebar.title("🔄 محرك العكس الذكي (Reversal)")

if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = st.sidebar.text_input(
        "Telegram Bot Token", type="password"
    )
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = st.sidebar.text_input("Telegram Chat ID")

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
            st.error(f"فشل إرسال التنبيه: {e}")


st.sidebar.markdown("---")
if st.sidebar.button("🧪 اختبار تليجرام محرك العكس"):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        test_msg = "🔄 *محرك العكس الذكي وقلب الصفقات يعمل بكفاءة تامة!*"
        send_telegram_alert(test_msg)
        st.sidebar.success("تم إرسال رسالة تجريبية!")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")


# ==========================================
# 3. جلب البيانات وحساب المؤشرات
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

            df["rsi"] = ta.momentum.rsi(df["close"], window=14)
            df["atr"] = ta.volatility.average_true_range(
                df["high"], df["low"], df["close"], window=14
            )
            return df
        else:
            st.error(f"خطأ في جلب البيانات: {data.get('message', 'خطأ')}")
            return None
    except Exception as e:
        st.error(f"خطأ بالاتصال: {e}")
        return None


# ==========================================
# 4. الواجهة ومنطق محرك العكس الذكي
# ==========================================
st.title("🔄 مختبر التداول التكيفي (SMC + النماذج + قلب الصفقات تلقائياً)")
st.caption(
    f"الرمز: **{symbol_display}** | الفريم: **{timeframe}** | النظام يراقب الانعكاسات ويقترح الفرصة البديلة فوراً"
)

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 20:
    current_price = df["close"].iloc[-1]
    current_rsi = df["rsi"].iloc[-1]
    current_atr = df["atr"].iloc[-1]
    rsi_prev = df["rsi"].iloc[-2]
    rsi_diff = current_rsi - rsi_prev

    recent_high = df["high"].iloc[-15:-2].max()
    recent_low = df["low"].iloc[-15:-2].min()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("مؤشر الزخم RSI", f"{current_rsi:.1f}")
    col3.metric("مقياس التقلب ATR", f"{current_atr:.2f}")
    col4.metric("حالة محرك العكس", "يراقب الانعكاسات ⚡")

    st.markdown("---")
    st.subheader("📊 تحليل السوق ومراقبة نقاط التحول")

    last_c = df.iloc[-1]
    prev_c = df.iloc[-2]

    pattern_signal = None
    pattern_name = ""

    # شروط رصد الفرص الأساسية (شراء أو بيع)
    if (last_c["close"] > recent_high and prev_c["close"] <= recent_high) or (
        abs(last_c["low"] - recent_low) / recent_low < 0.0015
        and last_c["close"] > last_c["open"]
    ):
        if current_rsi < 75:
            pattern_signal = "BUY"
            pattern_name = "🟢 فرصة شراء رئيسية (Breakout / Double Bottom)"
            sl = current_price - (current_atr * 1.5)
            tp = current_price + (current_atr * 3.0)

    elif (last_c["close"] < recent_low and prev_c["close"] >= recent_low) or (
        abs(last_c["high"] - recent_high) / recent_high < 0.0015
        and last_c["close"] < last_c["open"]
    ):
        if current_rsi > 25:
            pattern_signal = "SELL"
            pattern_name = "🔴 فرصة بيع رئيسية (Breakdown / Double Top)"
            sl = current_price + (current_atr * 1.5)
            tp = current_price - (current_atr * 3.0)

    if pattern_signal:
        st.success(f"🚨 تم رصد وإرسال `{pattern_name}`!")
        st.write(f"**سعر الدخول:** {current_price:.2f}")
        st.write(f"**وقف الخسارة:** {sl:.2f}")
        st.write(f"**الهدف:** {tp:.2f}")

        msg = (
            f"🎯 *تنبيه صفقة جديدة*\n\n"
            f"📌 *الزوج:* {symbol_display}\n"
            f"⏱️ *الفريم:* {timeframe}\n"
            f"📊 *النمط:* {pattern_name}\n"
            f"⚖️ *الاتجاه:* {pattern_signal}\n"
            f"💵 *الدخول:* {current_price:.2f}\n"
            f"🛑 *الستوب:* {sl:.2f}\n"
            f"🎯 *الهدف:* {tp:.2f}"
        )
        send_telegram_alert(msg)

    # ==========================================
    # محرك العكس الذكي (Reversal Engine & Flip)
    # ==========================================
    st.markdown("---")
    st.subheader("🔄 رصد الانعكاس الحاد وقلب الصفقة (Reversal Flip)")

    # إذا حدث انقلاب حاد من الهبوط إلى الصعود (فشل نموذج البيع / انعكاس هيكلي للأعلى)
    if last_c["close"] > last_c["open"] and rsi_diff > 6 and current_rsi < 60:
        rev_sl = current_price - (current_atr * 1.5)
        rev_tp = current_price + (current_atr * 3.0)
        
        st.warning("🔄 تم رصد فشل نموذج البيع وانعكاس السوق صعوداً! (جاري إرسال إشارة قلب الصفقة للشراء)")
        
        reversal_msg = (
            f"🔄 *تنبيه انعكاس وقلب الصفقة (Reversal Alert)*\n\n"
            f"📌 *الزوج:* {symbol_display}\n"
            f"⏱️ *الفريم:* {timeframe}\n"
            f"❌ *الحالة:* فشل نموذج البيع / انعكاس الزخم بشكل حاد للأعلى.\n"
            f"💡 *الإجراء المطلوب:* أغلق صفقة البيع الحالية فوراً.\n\n"
            f"🚀 *الفرصة البديلة المقترحة (دخول صعود):*\n"
            f"⚖️ *الاتجاه الجديد:* `BUY (شراء)`\n"
            f"💵 *سعر الدخول البديل:* {current_price:.2f}\n"
            f"🛑 *وقف الخسارة الجديد:* {rev_sl:.2f}\n"
            f"🎯 *الهدف الجديد:* {rev_tp:.2f}"
        )
        send_telegram_alert(reversal_msg)

    # إذا حدث انقلاب حاد من الصعود إلى الهبوط (فشل نموذج الشراء / انهيار السعر للبيع)
    elif last_c["close"] < last_c["open"] and rsi_diff < -6 and current_rsi > 40:
        rev_sl = current_price + (current_atr * 1.5)
        rev_tp = current_price - (current_atr * 3.0)
        
        st.warning("🔄 تم رصد فشل نموذج الشراء وانعكاس السوق هبوطاً! (جاري إرسال إشارة قلب الصفقة للبيع)")
        
        reversal_msg = (
            f"🔄 *تنبيه انعكاس وقلب الصفقة (Reversal Alert)*\n\n"
            f"📌 *الزوج:* {symbol_display}\n"
            f"⏱️ *الفريم:* {timeframe}\n"
            f"❌ *الحالة:* فشل نموذج الشراء / انهيار الزخم بشكل حاد للأسفل.\n"
            f"💡 *الإجراء المطلوب:* أغلق صفقة الشراء الحالية فوراً.\n\n"
            f"📉 *الفرصة البديلة المقترحة (دخول هبوط):*\n"
            f"⚖️ *الاتجاه الجديد:* `SELL (بيع)`\n"
            f"💵 *سعر الدخول البديل:* {current_price:.2f}\n"
            f"🛑 *وقف الخسارة الجديد:* {rev_sl:.2f}\n"
            f"🎯 *الهدف الجديد:* {rev_tp:.2f}"
        )
        send_telegram_alert(reversal_msg)
    else:
        st.info("السوق يسير ضمن مساره الحالي، ولا توجد إشارات انقلاب أو قلب صفقات مطلوبة في هذه اللحظة.")

    with st.expander("📊 سجل بيانات المؤشرات والأسعار الحية"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "rsi", "atr"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
