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
    page_title="Waterfall & LuxAlgo SMC Engine", page_icon="🎯", layout="wide"
)

TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY", "3a962e7a32d34a53839c028ca49ff7b5"
)

# تحديث تلقائي كل 10 ثوانٍ لمراقبة الشموع والصفقة المفتوحة
st_autorefresh(interval=10000, key="datarefresh")

if "active_trade" not in st.session_state:
    st.session_state.active_trade = None

# ==========================================
# 2. القائمة الجانبية (Sidebar)
# ==========================================
st.sidebar.title("🎯 SMC & Waterfall Risk Engine")

if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = st.sidebar.text_input("Telegram Bot Token", type="password")
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

timeframe = st.sidebar.selectbox("الإطار الزمني للتنفيذ", ["15min", "5min", "1h"])

if st.sidebar.button("🗑️ تصفير الذاكرة يدوياً"):
    st.session_state.active_trade = None
    st.sidebar.success("تم مسح الصفقة الحالية وإعادة تشغيل المراقبة.")

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

# ==========================================
# 3. جلب البيانات وحساب منطق SMC / Sweeps / HTF Bias
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
            df = df.sort_values("datetime").reset_index(drop=True)
            df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
            df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
            return df
        else:
            return None
    except Exception:
        return None

# ==========================================
# 4. غرفة العمليات والمراقبة الحية
# ==========================================
st.title("🎯 خوارزمية تحليل ومراقبة السيولة (LuxAlgo & SMC Engine)")
st.caption(f"الأصل: **{symbol_display}** | الإطار: **{timeframe}** | المراقبة الحية وتأمين الأرباح الآلي مفعل")

df = get_live_data(selected_symbol, timeframe)
df_htf = get_live_data(selected_symbol, "1h") # جلب اتجاه فريم الساعة الأكبر

if df is not None and len(df) >= 30 and df_htf is not None:
    current_price = df["close"].iloc[-1]
    current_atr = df["atr"].iloc[-1]
    last_candle = df.iloc[-1]
    
    # تحديد اتجاه الفريم الأكبر HTF Bias
    htf_bias = "BULLISH" if df_htf["close"].iloc[-1] > df_htf["ema50"].iloc[-1] else "BEARISH"

    # تحديد قمم وقيعان السيولة الحديثة (E:0 Zones Potential)
    recent_high = df["high"].iloc[-25:-2].max()
    recent_low = df["low"].iloc[-25:-2].min()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("اتجاه الفريم الأكبر HTF", htf_bias)
    col3.metric("مستوى سيولة القمة", f"{recent_high:.2f}")
    col4.metric("مستوى سيولة القاع", f"{recent_low:.2f}")

    st.markdown("---")

    # البحث عن الفرص الجديدة في حال عدم وجود صفقة مفتوحة
    if st.session_state.active_trade is None:
        st.subheader("🔍 البحث عن صفقات سحب سيولة عالية الجودة (Sweep Fakeouts)...")

        signal = None
        setup_name = ""
        sl = 0.0
        tp = 0.0

        # شرط الشراء: سحب سيولة القاع + اتجاه صاعد أو ارتداد من دعم قوي
        if last_candle["low"] < recent_low and last_candle["close"] > recent_low:
            signal = "BUY"
            setup_name = "🟢 Bullish Sweep Fakeout (سحب سيولة القاع)"
            sl = last_candle["low"] - (current_atr * 0.25)
            tp = recent_high

        # شرط البيع: سحب سيولة القمة + توافق مع الاتجاه الهابط HTF BEARISH
        elif last_candle["high"] > recent_high and last_candle["close"] < recent_high and htf_bias == "BEARISH":
            signal = "SELL"
            setup_name = "🔴 Bearish Sweep Fakeout (سحب سيولة القمة)"
            sl = last_candle["high"] + (current_atr * 0.25)
            tp = recent_low

        if signal:
            st.session_state.active_trade = {
                "type": signal,
                "entry": current_price,
                "sl": sl,
                "tp": tp,
                "name": setup_name,
                "be_notified": False # لم يتم نقل الستوب بعد
            }

            st.success(f"✅ تم اكتشاف وإرسال: {setup_name}")
            
            msg = (
                f"🚨 *تنبيه صفقة جديدة (SMC Sweep)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"⏱️ *الفريم:* {timeframe}\n"
                f"📊 *النموذج:* {setup_name}\n"
                f"⚖️ *الاتجاه:* {signal}\n"
                f"💵 *سعر الدخول:* {current_price:.2f}\n"
                f"🛑 *وقف الخسارة:* {sl:.2f}\n"
                f"🎯 *الهدف المستهدف:* {tp:.2f}\n"
                f"🌐 *HTF Bias:* {htf_bias}"
            )
            send_telegram_alert(msg)
        else:
            st.info("الرادار يعمل.. لا توجد عمليات سحب سيولة (Sweeps) مستوفية للشروط حالياً.")

    # ==========================================
    # المراقبة النشطة والمستمرة للصفقة المفتوحة
    # ==========================================
    else:
        active = st.session_state.active_trade
        entry_p = active["entry"]
        trade_type = active["type"]
        pips_diff = (current_price - entry_p) if trade_type == "BUY" else (entry_p - current_price)
        target_dist = abs(active["tp"] - entry_p)

        st.subheader(f"🛡️ المراقبة الحية للصفقة النشطة: ({trade_type})")
        st.write(f"**سعر الدخول:** {entry_p:.2f} | **السعر الحالي:** {current_price:.2f} | **الربح/الخسارة بالنقاط:** `{pips_diff:.2f}`")

        hit_tp = (trade_type == "BUY" and current_price >= active["tp"]) or (trade_type == "SELL" and current_price <= active["tp"])
        hit_sl = (trade_type == "BUY" and current_price <= active["sl"]) or (trade_type == "SELL" and current_price >= active["sl"])

        # 1. مراقبة تحقيق الهدف
        if hit_tp:
            st.balloons()
            st.success("🎉 تم تحقيق الهدف بنجاح!")
            send_telegram_alert(f"🎉 *مبروك!* تم تحقيق الهدف بالكامل لصفقة ({trade_type}) على {symbol_display} عند السعر {current_price:.2f}.")
            st.session_state.active_trade = None

        # 2. مراقبة ضرب الستوب
        elif hit_sl:
            st.error("🛑 تم ضرب وقف الخسارة.")
            send_telegram_alert(f"🛑 *تنبيه إغلاق:* تم ضرب الستوب لصفقة ({trade_type}) على {symbol_display}.")
            st.session_state.active_trade = None

        # 3. المراقبة الذكية: تنبيه نقل الستوب لنقطة الدخول (Break-Even) عند قطع 50% من الطريق
        elif pips_diff >= (target_dist * 0.5) and not active["be_notified"]:
            st.warning("⚡ الصفقة حققت +50% من الهدف! جاري إرسال تنبيه تأمين الصفقة.")
            be_msg = (
                f"🛡️ *تنبيه تأمين الصفقة (Break-Even)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💵 *السعر الحالي:* {current_price:.2f}\n"
                f"🛠️ *الإجراء المطلوب:* نقل وقف الخسارة (SL) فوراً إلى سعر الدخول ({entry_p:.2f}) وحجز جزء من الأرباح!"
            )
            send_telegram_alert(be_msg)
            st.session_state.active_trade["be_notified"] = True # تعليم التنبيه كمرسل لمنع التكرار
