import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（實戰修正版）", layout="wide")
st.title("📈 股票策略篩選器（實戰修正版）")

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
   - **完美多頭排列 (5 > 10 > 20 > 60 > 120)**
   - **昨日**：出量黑K (**量 > 前日**) + 守住 MA5
   - **今日**：量縮 (**量 < 昨日**) + 續守 MA5

4. 📦 **盤整突破 (均線糾結)**
   - 均線糾結 + 帶量突破 20日高點

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
                    if mode == "4":
                        tickers.append(f"{code}.TWO")
                    else:
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
        
        if df.empty: return pd.DataFrame()
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 強勢半年線濾網（核心）
# -------------------------------------------------
def strong_half_year_trend(close, ma60, ma120):
    if len(close) < 125: return False
    
    # 近 5 日不破 60 / 120 MA
    if (close.iloc[-5:] < ma60.iloc[-5:]).any(): return False
    if (close.iloc[-5:] < ma120.iloc[-5:]).any(): return False

    # 均線向上
    if ma60.iloc[-1] <= ma60.iloc[-6]: return False
    if ma120.iloc[-1] <= ma120.iloc[-6]: return False

    return True

# -------------------------------------------------
# 策略一：SMC 箱體突破
# -------------------------------------------------
def strategy_smc_breakout(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        if not strong_half_year_trend(close, ma60, ma120): return None

        lookback = 40
        resistance = high.iloc[-lookback-1:-1].max()
        support = low.iloc[-lookback-1:-1].min()

        if (resistance - support) / support > 0.30: return None

        c_now = float(close.iloc[-1])
        if c_now <= resistance: return None
        if vol_today <= float(volume.iloc[-2]) * 2: return None

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
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        if not strong_half_year_trend(close, ma60, ma120): return None

        lookback = 40
        resistance = high.iloc[-lookback:].max()
        support = low.iloc[-lookback:].min()

        if (resistance - support) / support > 0.30: return None

        c_now = float(close.iloc[-1])
        distance = (c_now - support) / support

        if not (-0.02 <= distance <= 0.05): return None

        ma_values = [
            ta.trend.sma_indicator(close, 5).iloc[-1],
            ta.trend.sma_indicator(close, 10).iloc[-1],
            ta.trend.sma_indicator(close, 20).iloc[-1],
            ma60.iloc[-1]
        ]
        
        if (max(ma_values) - min(ma_values)) / min(ma_values) > 0.10: return None

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
# 策略三：爆量回檔（洗盤）- 修正成交量條件
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 125: return None

        close, open_p, volume = df["Close"], df["Open"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        
        # 1. 基本量能
        if vol_today < 500_000: return None

        # 2. 計算均線
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        # 變數準備 (T=今日, T-1=昨日, T-2=前日)
        c_prev = close.iloc[-2]
        o_prev = open_p.iloc[-2]
        v_prev = float(volume.iloc[-2])
        v_prev_2 = float(volume.iloc[-3])
        
        c_now = float(close.iloc[-1])
        ma5_now = float(ma5.iloc[-1])
        ma10_now = float(ma10.iloc[-1])
        ma20_now = float(ma20.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # === 條件 A: 昨日出量黑K 且 守住MA5 ===
        # 1. 必須是黑K
        if c_prev >= o_prev: return None 
        
        # 2. 【修正】昨日量 > 前日量 (只要有增量即可)
        if v_prev <= v_prev_2: return None 
        
        # 3. 昨收要守 MA5
        if c_prev < ma5.iloc[-2]: return None 

        # === 條件 B: 今日量縮 且 續守MA5 ===
        # 1. 今收要守 MA5
        if c_now < ma5_now: return None 
        
        # 2. 【修正】今日量 < 昨日量 (只要量縮即可)
        if vol_today >= v_prev: return None 

        # === 條件 C: 完美多頭排列 ===
        # 5 > 10 > 20 > 60 > 120
        if not (ma5_now > ma10_now > ma20_now > ma60_now > ma120_now):
            return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "成交量(千)": int(vol_today / 1000),
            "昨日量(千)": int(v_prev / 1000),
            "縮量比": f"{round((vol_today/v_prev)*100, 1)}%",
            "狀態": "增量黑K後量縮 🛁"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略四：日線盤整突破
# -------------------------------------------------
def strategy_consolidation(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 120: return None

        close, open_p, high, volume = df["Close"], df["Open"], df["High"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        
        if vol_today < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        
        if c_now < ma60.iloc[-1]: return None

        ma_vals = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]]
        if (max(ma_vals) - min(ma_vals)) / c_now > 0.06: return None

        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        # 放量 1.5 倍
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if vol_today < vol_ma5 * 1.5: return None
        
        if c_now < float(open_p.iloc[-1]): return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "突破價": round(resistance, 2),
            "狀態": "帶量突破 📦"
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
    "📦 盤整突破 (均線糾結)": strategy_consolidation,
}

# -------------------------------------------------
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"目前快取: {len(all_tickers)} 檔")
    
    if st.sidebar.button("重抓上市上櫃清單"):
        with st.spinner("更新清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
            st.rerun()

    limit = st.sidebar.slider("掃描數量", 50, 2000, 200)
    tickers = all_tickers[:limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if source == "全市場" and not tickers:
        with st.spinner("初次執行，正在抓取全市場清單..."):
            st.session_state["all"] = get_all_tw_tickers()
            tickers = st.session_state["all"][:limit]

    if not tickers:
        st.error("沒有股票代碼可以掃描！請檢查來源設定。")
    else:
        result = {k: [] for k in selected}
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total = len(tickers)
        for i, t in enumerate(tickers):
            progress_bar.progress((i + 1) / total)
            status_text.text(f"掃描中 ({i+1}/{total}): {t}")
            
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    r["策略"] = k
                    result[k].append(r)
        
        progress_bar.empty()
        status_text.empty()

        has_data = False
        for k in selected:
            if result[k]:
                has_data = True
                st.subheader(f"📊 {k}")
                st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
        
        if not has_data:
            st.info("掃描完成，但沒有符合條件的股票。（建議放寬濾網或檢查掃描數量）")
