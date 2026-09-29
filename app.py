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
    page_title="Professional Risk & Dynamic Reversal Bot", page_icon="🎯", layout="wide"
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
st.sidebar.title("🎯 إدارة المخاطر والصفقات الاحترافية")

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

timeframe = st.sidebar.selectbox("الإطار الزمني (Interval)", ["15min", "5min", "1h"])

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
        send_telegram_alert("🎯 *البوت الاحترافي لإدارة الصفقات جاهز ويعمل بكفاءة!*")
        st.sidebar.success("تم إرسال رسالة تجريبية!")
    else:
        st.sidebar.error("يرجى التأكد من إضافة المفاتيح في Secrets أولاً.")

# ==========================================
# 3. جلب البيانات والحسابات الفنية
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
            df["ema9"] = ta.trend.ema_indicator(df["close"], window=9)
            return df
        else:
            return None
    except Exception:
        return None

# ==========================================
# 4. الواجهة ومنطق التحليل والمراقبة الذكية
# ==========================================
st.title("🎯 غرفة عمليات التداول الذكية (Waterfall & Dynamic Trail)")
st.caption(f"الزوج: **{symbol_display}** | الفريم: **{timeframe}** | المراقبة الحية ورصد الانعكاس المبكر مفعلة")

df = get_live_data(selected_symbol, timeframe)
df_htf = get_live_data(selected_symbol, "1h")

if df is not None and len(df) >= 30 and df_htf is not None:
    current_price = df["close"].iloc[-1]
    current_atr = df["atr"].iloc[-1]
    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]

    htf_bias = "BEARISH" if df_htf["close"].iloc[-1] < df_htf["ema50"].iloc[-1] else "BULLISH"

    recent_high = df["high"].iloc[-25:-2].max()
    recent_low = df["low"].iloc[-25:-2].min()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("اتجاه الفريم الأكبر HTF", htf_bias)
    col3.metric("سيولة القمة", f"{recent_high:.2f}")
    col4.metric("سيولة القاع", f"{recent_low:.2f}")

    st.markdown("---")

    # 1. حالة عدم وجود صفقة (البحث عن صفقات جديدة)
    if st.session_state.active_trade is None:
        st.subheader("🔍 خوارزمية الفلترة: رصد عمليات سحب السيولة (Sweeps)...")
        
        signal = None
        setup_name = ""
        sl = 0.0
        tp = 0.0

        if last_candle["low"] < recent_low and last_candle["close"] > recent_low:
            signal = "BUY"
            setup_name = "🟢 Bullish Sweep Fakeout (سحب سيولة القاع)"
            sl = last_candle["low"] - (current_atr * 0.25)
            tp = recent_high

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
                "be_notified": False,
                "reversal_notified": False
            }
            
            st.success(f"✅ تم اكتشاف وإرسال: {setup_name}")
            
            msg = (
                f"🚨 *تنبيه صفقة سيولة جديدة (SMC Sweep)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"⏱️ *الفريم:* {timeframe}\n"
                f"📊 *النموذج:* {setup_name}\n"
                f"⚖️ *الاتجاه:* {signal}\n"
                f"💵 *سعر الدخول:* {current_price:.2f}\n"
                f"🛑 *وقف الخسارة:* {sl:.2f}\n"
                f"🎯 *الهدف المستهدف:* {tp:.2f}\n"
                f"🌐 *الاتجاه العام HTF:* {htf_bias}"
            )
            send_telegram_alert(msg)
        else:
            st.info("السوق تحت الفحص الدقيق.. لا توجد عمليات سحب سيولة مستوفية للشروط حالياً.")

    # 2. حالة المراقبة المستمرة والمتقدمة للصفقة النشطة
    else:
        active = st.session_state.active_trade
        entry_p = active["entry"]
        trade_type = active["type"]
        pips_diff = (current_price - entry_p) if trade_type == "BUY" else (entry_p - current_price)
        target_dist = abs(active["tp"] - entry_p)

        st.subheader(f"🛡️ المراقبة والرادار الحصري للصفقة الحالية: ({trade_type})")
        st.write(f"**سعر الدخول:** {entry_p:.2f} | **السعر الحالي:** {current_price:.2f} | **الربح/الخسارة بالنقاط:** `{pips_diff:.2f}`")

        hit_tp = (trade_type == "BUY" and current_price >= active["tp"]) or (trade_type == "SELL" and current_price <= active["tp"])
        hit_sl = (trade_type == "BUY" and current_price <= active["sl"]) or (trade_type == "SELL" and current_price >= active["sl"])

        # شروط الانعكاس المبكر (Reversal Detection)
        # للشراء: كسر قاع الشمعة السابقة أو إغلاق تحت متوسط EMA9
        is_reversal_buy = (trade_type == "BUY") and (last_candle["close"] < prev_candle["low"] or current_price < df["ema9"].iloc[-1])
        # للبيع: كسر قمة الشمعة السابقة أو إغلاق فوق متوسط EMA9
        is_reversal_sell = (trade_type == "SELL") and (last_candle["close"] > prev_candle["high"] or current_price > df["ema9"].iloc[-1])

        # أ) تحقق الهدف
        if hit_tp:
            st.balloons()
            st.success("🎉 مبروك! تم تحقيق الهدف بالكامل.")
            send_telegram_alert(f"🎉 *نجاح تام!* تم تحقيق الهدف لصفقة ({trade_type}) على {symbol_display} عند {current_price:.2f}.")
            st.session_state.active_trade = None

        # ب) ضرب وقف الخسارة الكامل
        elif hit_sl:
            st.error("🛑 تم ضرب وقف الخسارة.")
            send_telegram_alert(f"🛑 *تنبيه إغلاق:* تم ضرب الستوب لصفقة ({trade_type}) على {symbol_display}.")
            st.session_state.active_trade = None

        # ج) التنبيه برصد إشارة انعكاس واضحة في الشموع (للخروج بأقل خسارة أو ربح جزئي)
        elif (is_reversal_buy or is_reversal_sell) and not active.get("reversal_notified", False):
            st.error("⚠️ تم رصد إشارة انعكاسية ضد اتجاه الصفقة!")
            status_text = "على أرباح جزئية" if pips_diff > 0 else "بأقل خسارة ممكنة"
            
            rev_msg = (
                f"⚠️ *تنبيه تحذيري: خطر انعكاس السوق!*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة الحالية:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💵 *السعر الحالي:* {current_price:.2f} (النتيجة: {pips_diff:.2f} نقطة)\n"
                f"🚨 *السبب:* ظهرت شمعة انعكاسية معاكسة للاتجاه ومخترقة للمعدل السعري القريب.\n"
                f"💡 *المنصح به:* يُفضل الخروج اليدوي الآن {status_text} لتجنب ضرب الستوب الكامل."
            )
            send_telegram_alert(rev_msg)
            st.session_state.active_trade["reversal_notified"] = True # تعليم التنبيه لمنع تكراره

        # د) تنبيه الـ Break-Even عند الوصول لمنتصف الهدف
        elif pips_diff >= (target_dist * 0.5) and not active.get("be_notified", False):
            st.warning("⚡ الصفقة حققت +50% من الهدف! جاري إرسال تنبيه تأمين الصفقة.")
            be_msg = (
                f"🛡️ *تنبيه تأمين الصفقة (Break-Even)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💵 *السعر الحالي:* {current_price:.2f}\n"
                f"🛠️ *الإجراء المطلوب:* نقل الستوب فوراً لسعر الدخول ({entry_p:.2f}) وحجز جزء من الأرباح!"
            )
            send_telegram_alert(be_msg)
            st.session_state.active_trade["be_notified"] = True

    with st.expander("📊 سجل بيانات الأسعار والمؤشرات الحية"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "atr"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
