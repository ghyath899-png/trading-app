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
    page_title="Professional Risk & Reversal Bot", page_icon="🎯", layout="wide"
)

TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_API_KEY = st.secrets.get(
    "TWELVE_DATA_API_KEY", "3a962e7a32d34a53839c028ca49ff7b5"
)

st_autorefresh(interval=10000, key="datarefresh")

# ذاكرة الحالة لتتبع الصفقة الحالية بشكل دقيق
if "active_trade" not in st.session_state:
    st.session_state.active_trade = None 

# ==========================================
# 2. القائمة الجانبية (Sidebar)
# ==========================================
st.sidebar.title("🎯 إدارة المخاطر والصفقات الاحترافية")

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
# 3. جلب البيانات وحساب المؤشرات الفنية بدقة
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

            # مؤشرات فنية متقدمة
            df["rsi"] = ta.momentum.rsi(df["close"], window=14)
            df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
            df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
            df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
            return df
        else:
            st.error(f"خطأ في جلب البيانات: {data.get('message', 'خطأ')}")
            return None
    except Exception as e:
        st.error(f"خطأ بالاتصال: {e}")
        return None


# ==========================================
# 4. الواجهة ومنطق الإدارة الذكية للفقات
# ==========================================
st.title("🎯 غرفة عمليات التداول الذكية والآمنة")
st.caption(f"الزوج: **{symbol_display}** | الفريم: **{timeframe}** | الفلترة الآلية وإدارة الأرباح والخسائر مفعلة")

df = get_live_data(selected_symbol, timeframe)

if df is not None and len(df) >= 30:
    current_price = df["close"].iloc[-1]
    current_rsi = df["rsi"].iloc[-1]
    current_atr = df["atr"].iloc[-1]
    ema_20 = df["ema20"].iloc[-1]
    ema_50 = df["ema50"].iloc[-1]
    
    last_c = df.iloc[-1]
    prev_c = df.iloc[-2]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("السعر الحالي", f"{current_price:.2f}")
    col2.metric("مؤشر الزخم RSI", f"{current_rsi:.1f}")
    col3.metric("مقياس التقلب ATR", f"{current_atr:.2f}")
    
    if st.session_state.active_trade:
        col4.metric("حالة الصفقة", f"مفتوحة ({st.session_state.active_trade['type']})")
    else:
        col4.metric("حالة الصفقة", "يبحث عن فرصة عالية النقاء")

    st.markdown("---")

    # الحالة الأولى: إذا لم تكن هناك صفقة مفتوحة، يبحث عن صفقة "مدروسة باحترافية"
    if st.session_state.active_trade is None:
        st.subheader("🔍 فلترة والبحث عن فرصة عالية الدقة...")
        
        recent_high = df["high"].iloc[-20:-2].max()
        recent_low = df["low"].iloc[-20:-2].min()

        signal = None
        setup_name = ""
        
        # شروط شراء صارمة (تأكيد الاتجاه مع المتوسطات والزخم)
        if last_c["close"] > recent_high and ema_20 > ema_50 and current_rsi > 50 and current_rsi < 70:
            signal = "BUY"
            setup_name = "🟢 فرصة شراء مؤكدة (Breakout مع توافق المتوسطات والزخم)"
            sl = current_price - (current_atr * 1.5)
            tp = current_price + (current_atr * 3.0)

        # شروط بيع صارمة (تأكيد الهبوط مع المتوسطات والزخم)
        elif last_c["close"] < recent_low and ema_20 < ema_50 and current_rsi < 50 and current_rsi > 30:
            signal = "SELL"
            setup_name = "🔴 فرصة بيع مؤكدة (Breakdown مع توافق المتوسطات والزخم)"
            sl = current_price + (current_atr * 1.5)
            tp = current_price - (current_atr * 3.0)

        if signal:
            st.session_state.active_trade = {
                "type": signal,
                "entry": current_price,
                "sl": sl,
                "tp": tp,
                "name": setup_name
            }
            
            st.success(f"✅ {setup_name} - تم إرسالها لتليجرام وتفعيل المراقبة النشطة!")
            
            msg = (
                f"🎯 *تنبيه صفقة مدروسة عالية النقاء*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"⏱️ *الفريم:* {timeframe}\n"
                f"📊 *النموذج:* {setup_name}\n"
                f"⚖️ *الاتجاه:* {signal}\n"
                f"💵 *سعر الدخول:* {current_price:.2f}\n"
                f"🛑 *وقف الخسارة (ATR):* {sl:.2f}\n"
                f"🎯 *الهدف المستهدف:* {tp:.2f}"
            )
            send_telegram_alert(msg)
        else:
            st.info("السوق تحت الفحص الدقيق.. لا توجد فرصة مستوفية لشروط النقاء والاحترافية حالياً.")

    # الحالة الثانية: إذا كانت الصفقة مفتوحة، البوت يراقبها لحظة بلحظة لإدارتها (خروج بربح أو تقليل خسارة أو قلب اتجاه)
    else:
        active = st.session_state.active_trade
        entry_p = active["entry"]
        trade_type = active["type"]
        
        # حساب الربح أو الخسارة الحالية بالنقاط
        pips_diff = (current_price - entry_p) if trade_type == "BUY" else (entry_p - current_price)
        
        st.subheader(f"🛡️ الإدارة النشطة للصفقة الحالية: ({trade_type})")
        st.write(f"**سعر الدخول:** {entry_p:.2f} | **السعر الحالي:** {current_price:.2f}")
        st.write(f"**الربح/الخسارة الحالية بالنقاط:** `{pips_diff:.2f}`")

        # 1. فحص الوصول للهدف النهائي
        hit_tp = (trade_type == "BUY" and current_price >= active["tp"]) or (trade_type == "SELL" and current_price <= active["tp"])
        
        # 2. فحص الوصول لوقف الخسارة
        hit_sl = (trade_type == "BUY" and current_price <= active["sl"]) or (trade_type == "SELL" and current_price >= active["sl"])

        # 3. فحص الخروج الذكي المبكر (إذا حققت جزءاً من الربح وبدأ الزخم ينعكس بشكل خطير لجني الربح أو تقليل الخسارة)
        early_exit = False
        exit_reason = ""
        
        if pips_diff > (current_atr * 1.0): # إذا دخلنا في ربح جيد وتغير الزخم فجأة
            if (trade_type == "BUY" and current_rsi < 45) or (trade_type == "SELL" and current_rsi > 55):
                early_exit = True
                exit_reason = "جني الأرباح المبكر (الزخم بدأ ينعكس بعد تحقيق مكاسب جيدة)"
        
        elif pips_diff < 0 and abs(pips_diff) > (current_atr * 0.8): # إذا بدأت الصفقة تعكس بخسارة معتبرة قبل ضرب الستوب الكامل
            if (trade_type == "BUY" and last_c["close"] < ema_20) or (trade_type == "SELL" and last_c["close"] > ema_20):
                early_exit = True
                exit_reason = "إيقاف الخسارة المبكر للحفاظ على رأس المال واختراق متوسط الحركة"

        if hit_tp:
            st.success("🎉 مبروك! تم تحقيق الهدف النهائي للصفقة.")
            send_telegram_alert(f"🎉 *نجاح تام! تم تحقيق الهدف بالكامل لصفقة الـ ({trade_type}) على {symbol_display}.*")
            st.session_state.active_trade = None

        elif hit_sl:
            st.error("🛑 تم ضرب وقف الخسارة (SL). تم إغلاق الصفقة.")
            send_telegram_alert(f"🛑 *تنبيه إغلاق:* تم ضرب الستوب لصفقة الـ ({trade_type}) على {symbol_display}.")
            st.session_state.active_trade = None

        elif early_exit:
            st.warning(f"⚠️ {exit_reason}! جاري إرسال تنبيه الإغلاق الآمن.")
            
            exit_msg = (
                f"⚠️ *تنبيه إدارة وتأمين الصفقة (Smart Exit)*\n\n"
                f"📌 *الزوج:* {symbol_display}\n"
                f"📊 *الصفقة:* {trade_type} من سعر {entry_p:.2f}\n"
                f"💡 *السبب:* {exit_reason}\n"
                f"💵 *السعر الحالي للخروج الآمن:* {current_price:.2f}\n"
                f"🛠️ *الإجراء:* أغلق الصفقة فوراً لحماية رصيدك."
            )
            send_telegram_alert(exit_msg)
            st.session_state.active_trade = None # تفريغ الذاكرة للبحث عن فرصة نظيفة جديدة

        else:
            st.info("الصفقة تسير في نطاقها الآمن، البوت يراقب مؤشرات الزخم والمتوسطات لحظة بلحظة.")

    with st.expander("📊 سجل بيانات المؤشرات والأسعار الحية"):
        cols = [c for c in ["datetime", "open", "high", "low", "close", "rsi", "atr", "ema20"] if c in df.columns]
        st.dataframe(df[cols].tail(10))
