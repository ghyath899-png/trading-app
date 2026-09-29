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
    page_title="Fast SMC & Price Action Bot", page_icon="⚡", layout="wide"
)

TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY", "3a962e7a32d34a53839c028ca49ff7b5"
)

st_autorefresh(interval=10000, key="datarefresh")

if "active_trade" not in st.session_state:
    st.session_state.active_trade = None

# ==========================================
# 2. القائمة الجانبية (Sidebar)
# ==========================================
st.sidebar.title("🎯 إدارة الصفقات والسجل")

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

timeframe = st.sidebar.selectbox("الإطار الزمني", ["5min", "15min", "1h"], index=0)

if st.sidebar.button("🗑️ تصفير الذاكرة يدوياً"):
    st.session_state.active_trade = None
    st.sidebar.success("تم مسح الصفقة الحالية وبدء مراقبة جديدة.")

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
if st.sidebar.button("🧪 اختبار تليجرام الاحترافي"):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        send_telegram_alert("⚡ *البوت السريع لمناطق الاهتمام والسعر اللحظي جاهز!*")
        st.sidebar.success("تم إرسال رسالة تجريبية!")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")

# ==========================================
# 3. جلب البيانات وحساب مناطق الاهتمام (POI)
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
            df["ema9"] = ta.trend.ema_indicator(df["close"], window=9)
            df["ema21"] = ta.trend.ema_indicator(df["close"], window=21)
            return df
        else:
            return None
    except Exception:
        return None

# ==========================================
# 4. محرك الفرص السريعة (Fast Action Engine)
# ==========================================
st.title("⚡ رادار الصفقات السريعة (Premium/Discount & POI)")
st.caption(f"الزوج: **{symbol_display}** | الفريم: **{timeframe}** | اقتناص ارتدادات الدعم/المقاومة وكسر وإعادة الاختبار")

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 30:
    current_price = df["close"].iloc[-1]
    current_atr = df["atr"].iloc[-1]
    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]

    # حساب النطاق المحلي القريب (Local Range)
    local_high = df["high"].iloc[-15:-1].max()
    local_low = df["low"].iloc[-15:-1].min()
    range_mid = (local_high + local_low) / 2.0

    # تحديد منطقة السعر (Premium / Discount)
    market_zone = "DISCOUNT (منطقة شراء)" if current_price < range_mid else "PREMIUM (منطقة بيع)"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("حالة المنطقة", market_zone)
    col3.metric("مقاومة/قمة سريعة", f"{local_high:.2f}")
    col4.metric("دعم/قاع سريع", f"{local_low:.2f}")

    st.markdown("---")

    # 1. البحث عن الصفقات عند عدم وجود صفقة نشطة
    if st.session_state.active_trade is None:
        st.subheader("🔍 فحص السلوك السعري السريع (Price Action & POI)...")

        signal = None
        setup_name = ""
        sl = 0.0
        tp = 0.0

        # أ) صفقة شراء: ارتداد من دعم قريب / منطقة الخصم Discount
        if current_price < range_mid and last_candle["low"] <= local_low and last_candle["close"] > last_candle["open"]:
            signal = "BUY"
            setup_name = "🟢 ارتداد من دعم سريع / منطقة خصم (Discount POI Bounce)"
            sl = last_candle["low"] - (current_atr * 0.3)
            tp = local_high

        # ب) صفقة شراء: كسر مقاومة وإعادة اختبار (Break & Retest)
        elif prev_candle["close"] > local_high and last_candle["low"] <= local_high and last_candle["close"] > local_high:
            signal = "BUY"
            setup_name = "🚀 كسر وإعادة اختبار للقمة (Bullish Break & Retest)"
            sl = local_high - (current_atr * 0.4)
            tp = current_price + (abs(current_price - sl) * 2.0)

        # ج) صفقة بيع: ارتداد من مقاومة قريبة / منطقة الغلاء Premium
        elif current_price > range_mid and last_candle["high"] >= local_high and last_candle["close"] < last_candle["open"]:
            signal = "SELL"
            setup_name = "🔴 ارتداد من مقاومة سريعة / منطقة غلاء (Premium POI Rejection)"
            sl = last_candle["high"] + (current_atr * 0.3)
            tp = local_low

        # د) صفقة بيع: كسر دعم وإعادة اختبار (Break & Retest)
        elif prev_candle["close"] < local_low and last_candle["high"] >= local_low and last_candle["close"] < local_low:
            signal = "SELL"
            setup_name = "📉 كسر وإعادة اختبار للقاع (Bearish Break & Retest)"
            sl = local_low + (current_atr * 0.4)
            tp = current_price - (abs(sl - current_price) * 2.0)

        if signal:
            st.session_state.active_trade = {
                "type": signal,
                "entry": current_price,
                "sl": sl,
                "tp": tp,
                "name": setup_name,
                "be_notified": False,
                "reversal_notified": False
            }
            
            st.success(f"✅ تم رصد فرصة سريعة: {setup_name}")
            
            msg = (
                f"⚡ *تنبيه فرصة سريعة (Fast POI / Break & Retest)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"⏱️ *الفريم:* {timeframe}\n"
                f"📊 *النموذج:* {setup_name}\n"
                f"🏛️ *المنطقة:* {market_zone}\n"
                f"⚖️ *النوع:* {signal}\n"
                f"💵 *سعر الدخول:* {current_price:.2f}\n"
                f"🛑 *وقف الخسارة:* {sl:.2f}\n"
                f"🎯 *الهدف المستهدف:* {tp:.2f}"
            )
            send_telegram_alert(msg)
        else:
            st.info("جاري مراقبة الشموع الحالية لتشكل كسر/إعادة اختبار أو ارتداد مباشر من مستويات الاهتمام القريبة..")

    # 2. المراقبة النشطة الحية للصفقة
    else:
        active = st.session_state.active_trade
        entry_p = active["entry"]
        trade_type = active["type"]
        pips_diff = (current_price - entry_p) if trade_type == "BUY" else (entry_p - current_price)
        target_dist = abs(active["tp"] - entry_p)

        st.subheader(f"🛡️ المراقبة الحية للصفقة: ({trade_type}) | {active['name']}")
        st.write(f"**الدخول:** {entry_p:.2f} | **السعر الحالي:** {current_price:.2f} | **نتيجة النقاط:** `{pips_diff:.2f}`")

        hit_tp = (trade_type == "BUY" and current_price >= active["tp"]) or (trade_type == "SELL" and current_price <= active["tp"])
        hit_sl = (trade_type == "BUY" and current_price <= active["sl"]) or (trade_type == "SELL" and current_price >= active["sl"])

        is_reversal_buy = (trade_type == "BUY") and (last_candle["close"] < prev_candle["low"] or current_price < df["ema9"].iloc[-1])
        is_reversal_sell = (trade_type == "SELL") and (last_candle["close"] > prev_candle["high"] or current_price > df["ema9"].iloc[-1])

        if hit_tp:
            st.balloons()
            st.success("🎉 مبروك! تم تحقيق الهدف.")
            send_telegram_alert(f"🎉 *نجاح!* تم وصول الهدف لصفقة ({trade_type}) على {symbol_display} عند {current_price:.2f}.")
            st.session_state.active_trade = None

        elif hit_sl:
            st.error("🛑 تم ضرب وقف الخسارة.")
            send_telegram_alert(f"🛑 *تنبيه:* تم ضرب الستوب لصفقة ({trade_type}) على {symbol_display}.")
            st.session_state.active_trade = None

        elif (is_reversal_buy or is_reversal_sell) and not active.get("reversal_notified", False):
            st.error("⚠️ إشارة ارتداد معاكس!")
            status_text = "على أرباح جزئية" if pips_diff > 0 else "بأقل خسارة"
            
            rev_msg = (
                f"⚠️ *تنبيه ارتداد سريع للسوق!*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💵 *السعر الحالي:* {current_price:.2f}\n"
                f"💡 *يُفضل الخروج اليدوي:* {status_text} لتجنب انزلاق السعر."
            )
            send_telegram_alert(rev_msg)
            st.session_state.active_trade["reversal_notified"] = True

        elif pips_diff >= (target_dist * 0.5) and not active.get("be_notified", False):
            st.warning("⚡ الصفقة حققت منتصف الهدف! جاري إرسال تنبيه التأمين.")
            be_msg = (
                f"🛡️ *تنبيه Break-Even*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type}\n"
                f"💵 *السعر الحالي:* {current_price:.2f}\n"
                f"🛠️ *الإجراء:* انقل الستوب لسعر الدخول ({entry_p:.2f})."
            )
            send_telegram_alert(be_msg)
            st.session_state.active_trade["be_notified"] = True

    with st.expander("📊 سجل حركة الأسعار (5Min)"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "atr"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
