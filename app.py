import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（實戰量縮版）", layout="wide")
st.title("📈 股票策略篩選器（實戰量縮版）")

st.markdown("""
---
**策略邏輯說明：**

1. 🚀 **SMC 箱體突破**
   - 強勢多頭：股價站穩 60MA / 120MA
   - 倍量突破箱體壓力 (BSL)

2. 🛡️ **SMC 回測支撐**
   - 強勢多頭：股價站穩 60MA / 120MA
   - 回踩箱體支撐 (OB)，均線糾結

3. 🛁 **爆量回檔（洗盤）**
   - 多頭排列
   - 昨日爆量黑K
   - 今日量縮續守 MA5

※ 全策略：今日成交量 > 500 張
---
""")

# -------------------------------------------------
# 股票清單
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    for mode in ["2", "4"]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            for item in df[0]:
                code = str(item).split()[0]
                if code.isdigit() and len(code) == 4:
                    tickers.append(f"{code}.TW")
        except Exception:
            pass
    return sorted(set(tickers))

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 強勢半年線濾網（核心）
# -------------------------------------------------
def strong_half_year_trend(close, ma60, ma120):
    # 近 5 日不破 60 / 120 MA
    if (close.iloc[-5:] < ma60.iloc[-5:]).any():
        return False
    if (close.iloc[-5:] < ma120.iloc[-5:]).any():
        return False

    # 均線向上
    if ma60.iloc[-1] <= ma60.iloc[-6]:
        return False
    if ma120.iloc[-1] <= ma120.iloc[-6]:
        return False

    return True

# -------------------------------------------------
# 策略一：SMC 箱體突破
# -------------------------------------------------
def strategy_smc_breakout(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 200:
            return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000:
            return None

        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        if not strong_half_year_trend(close, ma60, ma120):
            return None

        lookback = 40
        resistance = high.iloc[-lookback-1:-1].max()
        support = low.iloc[-lookback-1:-1].min()

        if (resistance - support) / support > 0.30:
            return None

        c_now = float(close.iloc[-1])
        if c_now <= resistance:
            return None

        if vol_today <= float(volume.iloc[-2]) * 2:
            return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "壓力(BSL)": round(resistance, 2),
            "支撐(OB)": round(support, 2),
            "成交量(千)": int(vol_today / 1000),
            "狀態": "倍量突破 🚀"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：SMC 回測支撐
# -------------------------------------------------
def strategy_smc_support(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 200:
            return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000:
            return None

        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        if not strong_half_year_trend(close, ma60, ma120):
            return None

        lookback = 40
        resistance = high.iloc[-lookback:].max()
        support = low.iloc[-lookback:].min()

        if (resistance - support) / support > 0.30:
            return None

        c_now = float(close.iloc[-1])
        distance = (c_now - support) / support

        if not (-0.02 <= distance <= 0.05):
            return None

        ma_values = [
            ta.trend.sma_indicator(close, 5).iloc[-1],
            ta.trend.sma_indicator(close, 10).iloc[-1],
            ta.trend.sma_indicator(close, 20).iloc[-1],
            ma60.iloc[-1]
        ]

        if (max(ma_values) - min(ma_values)) / min(ma_values) > 0.10:
            return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "支撐(OB)": round(support, 2),
            "距離支撐": f"{round(distance*100,1)}%",
            "成交量(千)": int(vol_today / 1000),
            "狀態": "回測支撐 🛡️"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略三：爆量回檔（洗盤）
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 125:
            return None

        close, open_p, volume = df["Close"], df["Open"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000:
            return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_prev, o_prev = close.iloc[-2], open_p.iloc[-2]
        if c_prev >= o_prev:
            return None

        if volume.iloc[-2] < volume.rolling(5).mean().iloc[-2] * 1.5:
            return None

        if c_prev < ma5.iloc[-2] or close.iloc[-1] < ma5.iloc[-1]:
            return None

        if volume.iloc[-1] >= volume.iloc[-2] * 0.6:
            return None

        if not (ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]):
            return None

        return {
            "股票": ticker,
            "現價": round(close.iloc[-1], 2),
            "成交量(千)": int(vol_today / 1000),
            "狀態": "量縮洗盤 🛁"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🚀 SMC 箱體突破": strategy_smc_breakout,
    "🛡️ SMC 回測支撐": strategy_smc_support,
    "🛁 爆量回檔（洗盤）": strategy_washout_rebound,
}

# -------------------------------------------------
# UI
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW,2317.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        st.session_state["all"] = get_all_tw_tickers()

    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"已載入 {len(all_tickers)} 檔")
    limit = st.sidebar.slider("掃描數量", 50, 2000, 200)
    tickers = all_tickers[:limit]

selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

if st.button("開始掃描", type="primary"):
    result = {k: [] for k in selected}
    for t in tickers:
        for k in selected:
            r = STRATEGIES[k](t)
            if r:
                r["策略"] = k
                result[k].append(r)

    for k in result:
        if result[k]:
            st.subheader(k)
            st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
