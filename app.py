import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time  # 引入 time 以便顯示進度

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
# 股票清單 (修正 .TW / .TWO 問題)
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    
    # Mode 2 = 上市 (.TW), Mode 4 = 上櫃 (.TWO)
    for mode in ["2", "4"]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            
            for item in df[0]:
                code = str(item).split()[0]
                if code.isdigit() and len(code) == 4:
                    # 【修正點】上櫃股票 yfinance 需用 .TWO
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
        # 下載 2 年資料
        df = yf.download(ticker, period="2y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        # 基本檢查：資料長度不足回傳 Empty
        if df.empty:
            return pd.DataFrame()
            
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 強勢半年線濾網（核心）
# -------------------------------------------------
def strong_half_year_trend(close, ma60, ma120):
    # 資料長度防呆
    if len(close) < 125: return False
    
    # 近 5 日不破 60 / 120 MA
    if (close.iloc[-5:] < ma60.iloc[-5:]).any():
        return False
    if (close.iloc[-5:] < ma120.iloc[-5:]).any():
        return False

    # 均線向上 (目前 > 5天前)
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
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

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
        # 必須突破壓力
        if c_now <= resistance:
            return None

        # 倍量 (今日 > 昨日 * 2)
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
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

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

        # 距離支撐 -2% ~ +5%
        if not (-0.02 <= distance <= 0.05):
            return None

        ma_values = [
            ta.trend.sma_indicator(close, 5).iloc[-1],
            ta.trend.sma_indicator(close, 10).iloc[-1],
            ta.trend.sma_indicator(close, 20).iloc[-1],
            ma60.iloc[-1]
        ]
        
        # 均線糾結度 (10%)
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
        if len(df) < 125: return None

        close, open_p, volume = df["Close"], df["Open"], df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        # 昨日變數
        c_prev = close.iloc[-2]
        o_prev = open_p.iloc[-2]
        v_prev = float(volume.iloc[-2])
        v_prev_2 = float(volume.iloc[-3])

        # 1. 昨日黑K
        if c_prev >= o_prev: return None

        # 2. 昨日爆量 (大於 5日均量1.5倍 OR 大於前日 1.2倍 -> 放寬條件)
        # 這樣才不會因為沒有爆巨量而漏掉
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.3 and v_prev < v_prev_2 * 1.2:
            return None

        # 3. 昨日守 MA5
        if c_prev < ma5.iloc[-2] or close.iloc[-1] < ma5.iloc[-1]:
            return None

        # 4. 今日量縮 ( < 昨日 0.7 倍，放寬至 70%)
        # 0.6 有點太嚴格 (窒息量)，0.7~0.8 比較符合實戰
        if vol_today >= v_prev * 0.7:
            return None

        # 5. 均線排列 (稍微放寬，只要求生命線之上且長多)
        # 完美排列 10>20>60>120 在洗盤時很容易 10 跌破 20，導致篩不到
        # 這裡改為：股價 > 20MA 且 20MA > 60MA > 120MA
        if not (close.iloc[-1] > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]):
             return None

        return {
            "股票": ticker,
            "現價": round(close.iloc[-1], 2),
            "成交量(千)": int(vol_today / 1000),
            "縮量比": f"{round((vol_today/v_prev)*100, 1)}%",
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
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    # 這裡顯示目前快取的數量，但不需要強制先按按鈕
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"目前快取: {len(all_tickers)} 檔")
    
    if st.sidebar.button("重抓上市上櫃清單"):
        with st.spinner("更新清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
            st.rerun()

    limit = st.sidebar.slider("掃描數量", 50, 2000, 200)
    
    # 邏輯修正：如果還沒抓過清單，tickers 會是空的，執行時要自動抓
    tickers = all_tickers[:limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    # 自動抓取防呆
    if source == "全市場" and not tickers:
        with st.spinner("初次執行，正在抓取全市場清單..."):
            st.session_state["all"] = get_all_tw_tickers()
            tickers = st.session_state["all"][:limit]

    if not tickers:
        st.error("沒有股票代碼可以掃描！請檢查來源設定。")
    else:
        result = {k: [] for k in selected}
        
        # 進度條
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total = len(tickers)
        for i, t in enumerate(tickers):
            # 更新進度
            progress_bar.progress((i + 1) / total)
            status_text.text(f"掃描中 ({i+1}/{total}): {t}")
            
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    r["策略"] = k
                    result[k].append(r)
        
        progress_bar.empty()
        status_text.empty()

        # 顯示結果
        has_data = False
        for k in selected:
            if result[k]:
                has_data = True
                st.subheader(f"📊 {k}")
                st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
        
        if not has_data:
            st.info("掃描完成，但沒有符合條件的股票。（建議放寬濾網或檢查掃描數量）")
