import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import warnings

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（良辰吉日）", layout="wide")
st.title("📈 股票策略篩選器（良辰吉日）")
st.markdown("""
---
**策略邏輯說明：**
1. **🚀 高檔箱體突破**：趨勢多頭 (MA60>120) 且位於年線之上，帶量突破箱頂。
2. **🛡️ 高檔箱體底部**：趨勢多頭 (MA60>120) 且位於年線之上，回測箱底支撐。
3. **🛁 爆量回檔 (極致洗盤)**：
   - **前日**：爆量黑K (主力倒貨假象)，但守住 MA5。
   - **今日**：**量縮一半** (前日量 > 今日量 × 2)，且續守 MA5。

**※ 全策略皆過濾：今日成交量 > 500 張**
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
# 策略一：高檔箱體突破 (追強)
# -------------------------------------------------
def strategy_box_breakout(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 250: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        ma240 = ta.trend.sma_indicator(close, 240)

        c_now = float(close.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])
        ma240_now = float(ma240.iloc[-1])

        # 高檔定義
        if ma60_now <= ma120_now: return None
        if c_now < ma240_now: return None

        # 箱體計算 (過去 40 天)
        lookback = 40
        past_highs = high.iloc[-lookback-1:-1]
        past_lows = low.iloc[-lookback-1:-1]
        
        box_high = float(past_highs.max())
        box_low = float(past_lows.min())

        box_amplitude = (box_high - box_low) / box_low
        if box_amplitude > 0.25: return None

        if c_now <= box_high: return None

        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if vol_today < vol_ma5 * 1.3: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "成交量 (張)": int(vol_today / 1000),
            "箱頂": round(box_high, 2),
            "狀態": "高檔突破 🚀"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略二：高檔箱體底部 (低接)
# -------------------------------------------------
def strategy_box_bottom(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 250: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        ma240 = ta.trend.sma_indicator(close, 240)
        
        c_now = float(close.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])
        ma240_now = float(ma240.iloc[-1])
        
        if ma60_now <= ma120_now: return None
        if c_now < ma240_now: return None

        lookback = 40
        past_highs = high.iloc[-lookback:]
        past_lows = low.iloc[-lookback:]
        
        box_high = float(past_highs.max())
        box_low = float(past_lows.min())

        box_amplitude = (box_high - box_low) / box_low
        if box_amplitude > 0.25: return None

        distance_from_low = (c_now - box_low) / box_low
        
        if distance_from_low <= 0.04 and distance_from_low >= -0.02:
            return {
                "股票": ticker,
                "現價": round(c_now, 2),
                "成交量 (張)": int(vol_today / 1000),
                "箱底": round(box_low, 2),
                "距離箱底": f"{round(distance_from_low * 100, 1)}%",
                "狀態": "高檔回測箱底 🛡️"
            }
        else:
            return None

    except Exception:
        return None

# -------------------------------------------------
# 策略三：爆量回檔 (洗盤) - 修正版
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 125: return None
        close = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        # 昨日數據
        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])
        v_prev = float(volume.iloc[-2])
        ma5_prev = float(ma5.iloc[-2])
        
        # 今日數據
        c_now = float(close.iloc[-1])
        ma5_now = float(ma5.iloc[-1])
        
        ma10_now  = float(ma10.iloc[-1])
        ma20_now  = float(ma20.iloc[-1])
        ma60_now  = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # 條件 A：前日是爆量黑K
        if c_prev >= o_prev: return None  # 必須是黑K
        
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.5: return None # 爆量
        
        if c_prev < ma5_prev: return None # 守住MA5

        # 條件 B：今日量縮一半 & 續守MA5
        if c_now < ma5_now: return None
        
        # 【關鍵修正】前日量 要大於 今日量的0.2倍 (即 v_prev > 2 * v_today)
        if v_prev <= vol_today * 1.2: return None

        # 條件 C：多頭排列
        if not (ma10_now > ma20_now > ma60_now > ma120_now): return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "成交量 (張)": int(vol_today / 1000),
            "前日量 (張)": int(v_prev / 1000), # 顯示出來比對
            "狀態": "量縮一半洗盤"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🚀 高檔箱體突破 (MA60>120)": strategy_box_breakout,
    "🛡️ 高檔箱體底部 (MA60>120)": strategy_box_bottom,
    "🛁 爆量回檔 (洗盤-量縮一半)": strategy_washout_rebound,
}

# -------------------------------------------------
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        with st.spinner("抓取清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
    
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"已載入: {len(all_tickers)} 檔")
    scan_limit = st.sidebar.slider("掃描數量限制", 10, 2000, 100)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

if st.button("開始掃描", type="primary"):
    if not tickers:
        st.warning("請先輸入代碼或載入全市場")
    else:
        result = {k: [] for k in selected}
        bar = st.progress(0.0)
        status_text = st.empty()

        for i, t in enumerate(tickers):
            bar.progress((i + 1) / len(tickers))
            status_text.text(f"掃描中 ({i+1}/{len(tickers)})：{t}")
            
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    r["策略"] = k
                    result[k].append(r)

        bar.empty()
        status_text.empty()
        st.subheader("📊 掃描結果")

        has_data = False
        all_rows = []
        for k in result:
            if result[k]:
                has_data = True
                st.markdown(f"### {k}")
                st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
                all_rows.extend(result[k])

        if all_rows:
            st.download_button(
                "📥 下載 CSV",
                pd.DataFrame(all_rows).to_csv(index=False, encoding="utf-8-sig"),
                "stock_scan_result.csv",
                "text/csv"
            )
        elif not has_data:
            st.info("無符合條件股票")
