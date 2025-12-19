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
st.set_page_config(page_title="股票策略篩選器（強勢洗盤版）", layout="wide")
st.title("📈 股票策略篩選器（強勢洗盤版）")
st.markdown("""
---
**策略邏輯修正：**
1. **爆量回檔（洗盤）**：
   - **昨日**：爆量黑K，但收盤**守住 5 日線**。
   - **今日**：量縮整理，且**必須繼續站在 5 日線之上** (MA10>MA20>MA60>MA120)。
2. **盤整突破**：均線糾結後突破，且成交量需 > 500 張。
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
        df = yf.download(ticker, period="1y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 策略一：爆量回檔 / 洗盤低接 (修正版)
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 125: return None

        close = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        # 0. 流動性過濾
        if volume.iloc[-2] < 500_000: return None

        # 1. 計算均線
        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        # === 昨日數據 (T-1) ===
        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])
        v_prev = float(volume.iloc[-2])
        ma5_prev = float(ma5.iloc[-2])
        
        # === 今日數據 (T) ===
        c_now = float(close.iloc[-1])
        v_now = float(volume.iloc[-1])
        ma5_now = float(ma5.iloc[-1]) # 今天的 5日線數值
        
        # 均線數值 (用於判斷排列)
        ma10_now  = float(ma10.iloc[-1])
        ma20_now  = float(ma20.iloc[-1])
        ma60_now  = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # ---------------------------------------------------------
        # 條件 A：昨日狀態 (爆量黑K + 守住5日線)
        # ---------------------------------------------------------
        # A-1. 黑K
        if c_prev >= o_prev: return None
        
        # A-2. 爆量 (昨日量 > 5日均量 * 1.5)
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.5: return None

        # A-3. 昨日守住 5 日線
        if c_prev < ma5_prev: return None

        # ---------------------------------------------------------
        # 條件 B：今日狀態 (守住5日線 + 多頭排列 + 量縮)
        # ---------------------------------------------------------
        # B-1. 【關鍵修正】今日必須繼續站在 5 日線之上
        if c_now < ma5_now: return None

        # B-2. 嚴格均線排列 (10 > 20 > 60 > 120)
        if not (ma10_now > ma20_now > ma60_now > ma120_now):
            return None

        # B-3. 今日量縮 (成交量 < 昨日)
        if v_now >= v_prev: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "MA5": round(ma5_now, 2),
            "昨日狀態": "爆量守MA5",
            "今日狀態": "量縮續守MA5"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：日線盤整突破 (已含 500 張過濾)
# -------------------------------------------------
def strategy_consolidation(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 120: return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        volume = df["Volume"]

        # === 【確認】流動性過濾 (< 500 張剔除) ===
        # 檢查最近一天的成交量
        if volume.iloc[-1] < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        
        # 生命線之上
        if c_now < ma60.iloc[-1]: return None

        # 均線糾結 (5, 10, 20)
        ma_vals = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]]
        ma_spread = (max(ma_vals) - min(ma_vals)) / c_now
        if ma_spread > 0.06: return None

        # 突破 20 日高點
        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        # 放量
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if volume.iloc[-1] < vol_ma5 * 1.5: return None
        
        # 收紅
        if c_now < float(open_p.iloc[-1]): return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "突破價": round(resistance, 2),
            "狀態": "帶量突破"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "爆量回檔 (洗盤換手)": strategy_washout_rebound,
    "盤整突破 (均線糾結)": strategy_consolidation,
}

# -------------------------------------------------
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW, 3231.TW")
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
            st.info("無符合條件股票 (條件較嚴格，建議擴大掃描範圍)")
