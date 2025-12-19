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
st.set_page_config(page_title="股票策略篩選器（箱體戰法版）", layout="wide")
st.title("📈 股票策略篩選器（箱體戰法版）")
st.markdown("""
---
**策略邏輯說明：**
1. **🚀 箱體突破 (追高)**：整理結束，帶量突破箱頂 (MA60>120)。
2. **🛡️ 箱體底部 (低接)**：股價回測箱型底部 (距離箱底 < 4%)，長線趨勢仍偏多。
3. **🛁 爆量回檔 (洗盤)**：昨日爆量黑K守MA5，今日量縮續守。
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
# 策略一：箱體突破 (追強)
# -------------------------------------------------
def strategy_box_breakout(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 130: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        if volume.iloc[-1] < 500_000: return None

        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_now = float(close.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # 趨勢：60MA > 120MA
        if ma60_now <= ma120_now: return None
        if c_now < ma60_now: return None

        # 箱體計算 (過去 40 天)
        lookback = 40
        past_highs = high.iloc[-lookback-1:-1]
        past_lows = low.iloc[-lookback-1:-1]
        
        box_high = float(past_highs.max())
        box_low = float(past_lows.min())

        # 震幅限制 < 25%
        box_amplitude = (box_high - box_low) / box_low
        if box_amplitude > 0.25: return None

        # 突破箱頂
        if c_now <= box_high: return None

        # 帶量
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if volume.iloc[-1] < vol_ma5 * 1.3: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "箱頂": round(box_high, 2),
            "狀態": "突破箱頂 🚀"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略二：箱體底部 (低接 - 新增)
# -------------------------------------------------
def strategy_box_bottom(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 130: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        # 1. 流動性 (底部量可能縮，所以標準稍微放寬到 300 張，避免錯過)
        if volume.iloc[-1] < 300_000: return None

        # 2. 趨勢：MA60 > MA120 (確保是多頭回檔，不是空頭下跌)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        
        if ma60.iloc[-1] <= ma120.iloc[-1]: return None

        # 3. 箱體計算 (過去 40-60 天)
        lookback = 40
        past_highs = high.iloc[-lookback:] # 包含今天，因為今天可能就在底部
        past_lows = low.iloc[-lookback:]
        
        box_high = float(past_highs.max())
        box_low = float(past_lows.min())

        # 4. 震幅限制 (箱子不能太大，太大代表趨勢不明)
        box_amplitude = (box_high - box_low) / box_low
        if box_amplitude > 0.25: return None

        # 5. 位置判定：接近箱底
        c_now = float(close.iloc[-1])
        
        # 定義：股價距離箱底 4% 以內
        distance_from_low = (c_now - box_low) / box_low
        
        # 條件 A: 在箱底附近 ( < 4% )
        # 條件 B: 沒有跌破箱底太多 ( > -2% ) -> 避免接到已經崩盤的
        if distance_from_low <= 0.04 and distance_from_low >= -0.02:
            return {
                "股票": ticker,
                "現價": round(c_now, 2),
                "箱底": round(box_low, 2),
                "距離箱底": f"{round(distance_from_low * 100, 1)}%",
                "狀態": "回測箱底 🛡️"
            }
        else:
            return None

    except Exception:
        return None

# 策略一：爆量回檔 / 洗盤低接 (簡化版：量縮 + 嚴守MA5)
# -------------------------------------------------
import pandas as pd
import ta

def strategy_washout_rebound(ticker):
    try:
        # 假設 download_daily 是您用來下載資料的函數
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

        # 1-3. 守住 5 日線 (昨日還在MA5之上，確認不是真崩盤)
        if c_prev < ma5_prev: return None

        # ---------------------------------------------------------
        # 條件 2：今日狀態 (多頭排列 + 量縮 + 站穩MA5)
        # ---------------------------------------------------------
        # 2-1. 嚴格均線排列 (10 > 20 > 60 > 120)
        # 確保大趨勢是向上的
        if not (ma10_now > ma20_now > ma60_now > ma120_now):
            return None

        # 2-2. 今日量縮 (比昨天爆量少，代表賣壓減輕)
        if v_now >= v_prev: return None

        # 2-3. 【關鍵防守】嚴守 5日線
        # 只要今天收盤價 >= 5日均線，就符合
        if c_now < ma5_now: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "昨日狀態": "爆量黑K",
            "均線狀態": "多頭排列",
            "MA5": round(ma5_now, 2),
            "訊號": "量縮且站穩MA5"
        }

    except Exception as e:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🚀 箱體突破 (追高)": strategy_box_breakout,
    "🛡️ 箱體底部 (低接)": strategy_box_bottom,
    "🛁 爆量回檔 (洗盤)": strategy_washout_rebound,
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
