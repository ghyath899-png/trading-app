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
    page_title="Multi-Strategy Pure Structure Bot", page_icon="🧩", layout="wide"
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
st.sidebar.title("🎯 إدارة الصفقات والمراقبة")

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
        send_telegram_alert("🧩 *البوت الشامل متعدد الاستراتيجيات (بدون EMA) جاهز!*")
        st.sidebar.success("تم إرسال رسالة تجريبية!")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")

# ==========================================
# 3. جلب البيانات بحسابات الهيكل النقي
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
            
            # حساب نطاق التقلب بدون الاعتماد على متوسطات متقاطعة
            df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
            df["rsi"] = ta.momentum.rsi(df["close"], window=14)
            return df
        else:
            return None
    except Exception:
        return None

# ==========================================
# 4. محرك التحليل المتعدد بدون EMA
# ==========================================
st.title("🧩 المحلل المتعدد (SMC + Pure Price Action)")
st.caption(f"الزوج: **{symbol_display}** | الفريم: **{timeframe}** | التحليل التداخلي بين المدارس وإلغاء الإنذارات الكاذبة")

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 30:
    closed_candle = df.iloc[-2]
    prev_closed_candle = df.iloc[-3]
    current_price = df["close"].iloc[-1]
    current_atr = df["atr"].iloc[-1]

    # حساب النطاق المحلي القريب بناءً على الشموع المغلقة فقط
    local_high = df["high"].iloc[-16:-2].max()
    local_low = df["low"].iloc[-16:-2].min()
    range_mid = (local_high + local_low) / 2.0

    market_zone = "DISCOUNT (منطقة شراء)" if current_price < range_mid else "PREMIUM (منطقة بيع)"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("حالة المنطقة", market_zone)
    col3.metric("مقاومة سريعة", f"{local_high:.2f}")
    col4.metric("دعم سريع", f"{local_low:.2f}")

    st.markdown("---")

    # 1. البحث عن الصفقات بشجرة الاستراتيجيات المتسلسلة
    if st.session_state.active_trade is None:
        st.subheader("🔍 فحص السلوك السعري بالاستراتيجيات البديلة...")

        signal = None
        setup_name = ""
        sl = 0.0
        tp = 0.0

        # --- أ) الاستراتيجية 1: SMC Liquidity Sweeps ---
        if closed_candle["low"] < local_low and closed_candle["close"] > local_low:
            signal = "BUY"
            setup_name = "🟢 SMC Sweep (سحب سيولة القاع)"
            sl = closed_candle["low"] - (current_atr * 0.25)
            tp = local_high

        elif closed_candle["high"] > local_high and closed_candle["close"] < local_high:
            signal = "SELL"
            setup_name = "🔴 SMC Sweep (سحب سيولة القمة)"
            sl = closed_candle["high"] + (current_atr * 0.25)
            tp = local_low

        # --- ب) الاستراتيجية 2: Break & Retest (في حال غياب الـ Sweep) ---
        if signal is None:
            if closed_candle["close"] > local_high and current_price > local_high:
                signal = "BUY"
                setup_name = "🚀 Price Action (كسر وإعادة اختبار القمة)"
                sl = local_high - (current_atr * 0.35)
                tp = current_price + (abs(current_price - sl) * 2.0)

            elif closed_candle["close"] < local_low and current_price < local_low:
                signal = "SELL"
                setup_name = "📉 Price Action (كسر وإعادة اختبار القاع)"
                sl = local_low + (current_atr * 0.35)
                tp = current_price - (abs(sl - current_price) * 2.0)

        # --- ج) الاستراتيجية 3: Wyckoff Spring / UTAD (في حال غياب الاستراتيجيتين) ---
        if signal is None:
            if current_price < range_mid and closed_candle["close"] > closed_candle["open"] and df["rsi"].iloc[-2] < 35:
                signal = "BUY"
                setup_name = "🏦 Wyckoff Spring (تجميع عند منطقة الخصم)"
                sl = closed_candle["low"] - (current_atr * 0.3)
                tp = local_high

            elif current_price > range_mid and closed_candle["close"] < closed_candle["open"] and df["rsi"].iloc[-2] > 65:
                signal = "SELL"
                setup_name = "🏦 Wyckoff UTAD (توزيع عند منطقة الغلاء)"
                sl = closed_candle["high"] + (current_atr * 0.3)
                tp = local_low

        # --- د) الاستراتيجية 4: Support / Resistance Rejection ---
        if signal is None:
            if current_price < range_mid and closed_candle["low"] <= local_low and closed_candle["close"] > closed_candle["open"]:
                signal = "BUY"
                setup_name = "📊 POI Rejection (ارتداد من منطقة دعم ملامسة)"
                sl = closed_candle["low"] - (current_atr * 0.3)
                tp = range_mid

            elif current_price > range_mid and closed_candle["high"] >= local_high and closed_candle["close"] < closed_candle["open"]:
                signal = "SELL"
                setup_name = "📊 POI Rejection (ارتداد من منطقة مقاومة ملامسة)"
                sl = closed_candle["high"] + (current_atr * 0.3)
                tp = range_mid

        if signal:
            st.session_state.active_trade = {
                "type": signal,
                "entry": current_price,
                "sl": sl,
                "tp": tp,
                "name": setup_name,
                "candles_passed": 0,
                "last_candle_time": closed_candle["datetime"],
                "be_notified": False,
                "reversal_notified": False
            }
            
            st.success(f"✅ تم رصد صفقة عبر: {setup_name}")
            
            msg = (
                f"⚡ *تنبيه صفقة جديدة من المحلل المتعدد*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"⏱️ *الفريم:* {timeframe}\n"
                f"📊 *الاستراتيجية المعتمدة:* {setup_name}\n"
                f"🏛️ *المنطقة:* {market_zone}\n"
                f"⚖️ *النوع:* {signal}\n"
                f"💵 *سعر الدخول:* {current_price:.2f}\n"
                f"🛑 *وقف الخسارة:* {sl:.2f}\n"
                f"🎯 *الهدف المستهدف:* {tp:.2f}"
            )
            send_telegram_alert(msg)
        else:
            st.info("جاري تحليل السلوك السعري والهيكلي عبر كل الاستراتيجيات المتاحة..")

    # 2. المراقبة النشطة الذكية وحماية الصفقة
    else:
        active = st.session_state.active_trade
        entry_p = active["entry"]
        trade_type = active["type"]
        pips_diff = (current_price - entry_p) if trade_type == "BUY" else (entry_p - current_price)
        target_dist = abs(active["tp"] - entry_p)

        # تحديث عداد الشموع المغلقة لمنع تنبيه نفس الدقيقة
        if closed_candle["datetime"] != active.get("last_candle_time"):
            active["candles_passed"] += 1
            active["last_candle_time"] = closed_candle["datetime"]

        st.subheader(f"🛡️ المراقبة النشطة للصفقة: ({trade_type}) | {active['name']}")
        st.write(f"**الدخول:** {entry_p:.2f} | **السعر الحالي:** {current_price:.2f} | **عدد الشموع المكتملة:** `{active['candles_passed']}` | **النتيجة:** `{pips_diff:.2f}`")

        hit_tp = (trade_type == "BUY" and current_price >= active["tp"]) or (trade_type == "SELL" and current_price <= active["tp"])
        hit_sl = (trade_type == "BUY" and current_price <= active["sl"]) or (trade_type == "SELL" and current_price >= active["sl"])

        # شرط الانعكاس الهيكلي فقط (بدون EMA):
        # 1. إكمال شمعتين مغطيتين على الأقل
        # 2. إغلاق شمعة كاملة بتجاوز هبوطي/صعودي واضح لهيكل الشمعة السابقة مع تراجع بالنقاط
        is_real_reversal_buy = (
            trade_type == "BUY" and 
            active["candles_passed"] >= 2 and 
            closed_candle["close"] < prev_closed_candle["low"] and 
            pips_diff < -(current_atr * 0.4)
        )
        
        is_real_reversal_sell = (
            trade_type == "SELL" and 
            active["candles_passed"] >= 2 and 
            closed_candle["close"] > prev_closed_candle["high"] and 
            pips_diff < -(current_atr * 0.4)
        )

        if hit_tp:
            st.balloons()
            st.success("🎉 مبروك! تم تحقيق الهدف.")
            send_telegram_alert(f"🎉 *نجاح!* تم وصول الهدف لصفقة ({trade_type}) على {symbol_display} عند {current_price:.2f}.")
            st.session_state.active_trade = None

        elif hit_sl:
            st.error("🛑 تم ضرب وقف الخسارة.")
            send_telegram_alert(f"🛑 *تنبيه:* تم ضرب الستوب لصفقة ({trade_type}) على {symbol_display}.")
            st.session_state.active_trade = None

        elif (is_real_reversal_buy or is_real_reversal_sell) and not active.get("reversal_notified", False):
            st.error("⚠️ إشارة ارتداد هيكلية حقيقية!")
            
            rev_msg = (
                f"⚠️ *تنبيه خروج مبكر (تأكيد هيكلي بعد إغلاق شمعتين)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💵 *السعر الحالي:* {current_price:.2f}\n"
                f"💡 *السبب:* كسر هيكل الشمعة المغلقة عكس اتجاهك، ينصح بالخروج بأقل خسارة."
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

    with st.expander("📊 سجل بيانات الأسعار المغلقة والمؤشرات"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "atr", "rsi"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
