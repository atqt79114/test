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
st.set_page_config(page_title="股票策略篩選器（洗盤換手版）", layout="wide")
st.title("📈 股票策略篩選器（洗盤換手版）")
st.markdown("""
---
**策略邏輯說明：**
1. **爆量回檔（主力洗盤）**：
   - **昨日**：爆量黑K（主力倒貨假象），但收盤強勢**守住 5 日線**。
   - **今日**：量縮回穩，且均線維持強勢排列 (**10MA > 20MA > 60MA > 120MA**)。
2. **盤整突破**：均線糾結後，第一根帶量突破。
---
""")

# -------------------------------------------------
# 股票清單
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    for mode in ["2", "4"]: # 2=上市, 4=上櫃
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
        # 下載 1 年份資料以計算 MA120
        df = yf.download(ticker, period="1y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 策略一：爆量回檔 / 洗盤低接 (依據圖片邏輯)
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 125: return None # 至少要有 120MA 的資料

        close = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        # === 流動性過濾 ===
        if volume.iloc[-2] < 500_000: return None # 昨天至少500張

        # === 計算均線 ===
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
        
        # 均線數值 (今日)
        ma5_now   = float(ma5.iloc[-1])
        ma10_now  = float(ma10.iloc[-1])
        ma20_now  = float(ma20.iloc[-1])
        ma60_now  = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # ---------------------------------------------------------
        # 條件 1：昨日狀態 (爆量黑K + 守住5日線)
        # ---------------------------------------------------------
        # 1-1. 黑K (收盤 < 開盤)
        if c_prev >= o_prev: return None
        
        # 1-2. 爆量 (昨日量 > 5日均量 * 1.5)
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.5: return None

        # 1-3. 守住 5 日線 (這是關鍵，代表不是真崩盤)
        if c_prev < ma5_prev: return None

        # ---------------------------------------------------------
        # 條件 2：今日狀態 (多頭排列 + 量縮)
        # ---------------------------------------------------------
        # 2-1. 嚴格均線排列 (10 > 20 > 60 > 120)
        # 這是你指定的多頭強勢結構
        if not (ma10_now > ma20_now > ma60_now > ma120_now):
            return None

        # 2-2. 今日量縮 (比昨天爆量少，代表籌碼沉澱)
        if v_now >= v_prev: return None

        # 2-3. 股價位置確認
        # 雖然是低接，但今日收盤不應跌破 10日線太遠，確保趨勢還在
        # 這裡設定今日收盤最好還在 10MA 或 5MA 附近
        if c_now < ma10_now * 0.98: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "昨日狀態": "爆量黑K守MA5",
            "均線狀態": "10>20>60>120",
            "量能": "今日量縮",
            "MA5": round(ma5_now, 2)
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：日線盤整突破
# -------------------------------------------------
def strategy_consolidation(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 120: return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        volume = df["Volume"]

        if volume.iloc[-1] < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        
        if c_now < ma60.iloc[-1]: return None

        # 均線糾結 (5, 10, 20)
        ma_vals = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]]
        ma_spread = (max(ma_vals) - min(ma_vals)) / c_now
        if ma_spread > 0.06: return None # 放寬到 6%

        # 突破 20 日高點
        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        # 放量
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if volume.iloc[-1] < vol_ma5 * 1.5: return None
        
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
# Sidebar 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW, 3231.TW, 2618.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        with st.spinner("抓取清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
    
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"已載入: {len(all_tickers)} 檔")
    
    # 【修改】上限調整為 2000
    scan_limit = st.sidebar.slider("掃描數量限制", 10, 2000, 100)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

# -------------------------------------------------
# 執行按鈕
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if not tickers:
        st.warning("請先輸入代碼或載入全市場")
    else:
        result = {k: [] for k in selected}
        bar = st.progress(0.0)
        status_text = st.empty()

        for i, t in enumerate(tickers):
            # 更新進度
            progress = (i + 1) / len(tickers)
            bar.progress(progress)
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
            st.info("無符合條件股票 (建議：盤中執行效果較佳)")
