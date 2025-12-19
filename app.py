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
st.set_page_config(page_title="股票策略篩選器（SMC 結構版）", layout="wide")
st.title("📈 股票策略篩選器（SMC 結構版）")
st.markdown("""
---
**策略邏輯說明 (SMC 思維結合均線)：**

1.  **💎 SMC 箱體突破 (BOS)**：
    * **結構**：均線 (5/10/20/60) 糾結代表主力吸籌 (Accumulation)。
    * **趨勢**：MA60 > MA120 (長線多頭)。
    * **訊號**：收盤價突破 **壓力區 (BSL)**，且爆量。
    
2.  **🛡️ SMC 訂單塊回測 (Return to OB)**：
    * **結構**：均線糾結，股價回到 **支撐區 (Order Block)**。
    * **訊號**：低接布局，防守糾結區的最低點。

3.  **🛁 爆量回檔 (洗盤)**：量縮一半守 MA5。

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
# 策略一：SMC 箱體突破 (Break of Structure)
# -------------------------------------------------
def strategy_smc_breakout(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 200: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        # 1. 流動性過濾
        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        # 2. 計算均線
        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_now = float(close.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # === 條件 A：趨勢濾網 (MA60 > MA120) ===
        if ma60_now <= ma120_now: return None
        
        # === 條件 B：SMC 結構定義 (均線糾結區) ===
        # 我們檢查過去 40 天內，是否有發生「均線糾結」
        # 這裡用 (MA5, MA10, MA20, MA60) 的乖離率來定義「吸籌區」
        
        # 過去 40 天的高低點 (作為 BSL 和 SSL)
        lookback = 40
        past_highs = high.iloc[-lookback-1:-1]
        past_lows = low.iloc[-lookback-1:-1]
        
        # 定義 SMC 關鍵位
        # 壓力 (BSL): 過去這段整理期間的最高價
        # 支撐 (OB/SSL): 過去這段整理期間的最低價
        resistance_bsl = float(past_highs.max())
        support_ssl = float(past_lows.min())

        # 檢查糾結度 (Consolidation)
        # 如果箱子太寬 (例如震幅 > 30%)，代表不是吸籌，是盤整或出貨
        amplitude = (resistance_bsl - support_ssl) / support_ssl
        if amplitude > 0.30: return None

        # === 條件 C：突破 (BOS - Break of Structure) ===
        # 1. 收盤價突破壓力區
        if c_now <= resistance_bsl: return None
        
        # 2. 倍量攻擊 (Confirmation)
        vol_prev = float(volume.iloc[-2])
        if vol_today <= vol_prev * 2: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "壓力 (BSL)": round(resistance_bsl, 2), # 突破了這個價位
            "支撐 (OB)": round(support_ssl, 2),     # 萬一跌破這裡要停損
            "成交量": int(vol_today / 1000),
            "型態": "SMC 結構突破"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：SMC 訂單塊回測 (Return to Order Block)
# -------------------------------------------------
def strategy_smc_support(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 200: return None

        close = df["Close"]
        volume = df["Volume"]
        high = df["High"]
        low = df["Low"]

        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        
        c_now = float(close.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # 趨勢：MA60 > MA120
        if ma60_now <= ma120_now: return None

        # SMC 結構定義
        lookback = 40
        past_highs = high.iloc[-lookback:] # 含今日
        past_lows = low.iloc[-lookback:]
        
        resistance_bsl = float(past_highs.max())
        support_ssl = float(past_lows.min())

        amplitude = (resistance_bsl - support_ssl) / support_ssl
        if amplitude > 0.30: return None

        # === 條件：回測支撐 (Mitigation) ===
        # 股價距離「支撐 (SSL)」很近 (< 5%)
        distance_from_support = (c_now - support_ssl) / support_ssl
        
        # 在支撐附近，且沒有跌破支撐超過 2% (掃停損可以，但實體不能破太遠)
        if distance_from_support <= 0.05 and distance_from_support >= -0.02:
            
            # 額外確認：均線必須有糾結跡象 (5/10/20/60 彼此靠近)
            ma_values = [
                float(ta.trend.sma_indicator(close, 5).iloc[-1]),
                float(ta.trend.sma_indicator(close, 10).iloc[-1]),
                float(ta.trend.sma_indicator(close, 20).iloc[-1]),
                float(ma60_now)
            ]
            ma_spread = (max(ma_values) - min(ma_values)) / min(ma_values)
            
            # 如果均線發散太嚴重(>10%)，代表不是糾結底，可能是下跌中繼
            if ma_spread > 0.10: return None

            return {
                "股票": ticker,
                "現價": round(c_now, 2),
                "壓力 (BSL)": round(resistance_bsl, 2), # 目標價
                "支撐 (OB)": round(support_ssl, 2),     # 買進防守價
                "距離支撐": f"{round(distance_from_support*100, 1)}%",
                "成交量": int(vol_today / 1000),
                "型態": "SMC 回測支撐"
            }
        else:
            return None

    except Exception:
        return None

# -------------------------------------------------
# 策略三：爆量回檔 (洗盤) - 保持不變
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

        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])
        v_prev = float(volume.iloc[-2])
        ma5_prev = float(ma5.iloc[-2])
        c_now = float(close.iloc[-1])
        ma5_now = float(ma5.iloc[-1])
        
        ma10_now = float(ma10.iloc[-1])
        ma20_now = float(ma20.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        if c_prev >= o_prev: return None
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.5: return None
        if c_prev < ma5_prev: return None
        if c_now < ma5_now: return None
        if v_prev <= vol_today * 2: return None
        if not (ma10_now > ma20_now > ma60_now > ma120_now): return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "成交量": int(vol_today / 1000),
            "MA5": round(ma5_now, 2),
            "狀態": "量縮洗盤"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "💎 SMC 箱體突破 (壓力/支撐)": strategy_smc_breakout,
    "🛡️ SMC 回測支撐 (壓力/支撐)": strategy_smc_support,
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
                # 顯示資料，包含壓力與支撐欄位
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
