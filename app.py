import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（股價強勢版）", layout="wide")
st.title("📈 股票策略篩選器（股價強勢版）")

st.markdown("""
---
**💎 全策略共同核心：股價站上所有均線**
**判斷標準：現價 > 5MA、10MA、20MA、60MA、120MA**
*(不需均線排列，只要股價在所有均線之上即可)*

**策略邏輯說明：**

1. 🚀 **SMC 箱體突破**
   - 趨勢：現價 > 所有均線
   - 訊號：倍量突破箱體壓力 (BSL)

2. 🛡️ **SMC 回測支撐**
   - 趨勢：現價 > 所有均線
   - 訊號：回踩箱體支撐 (OB)

3. 🛁 **爆量回檔（洗盤）**
   - 趨勢：現價 > 所有均線
   - 昨日：增量黑K + 守 MA5
   - 今日：量縮 ( < 昨日) + 續守 MA5

4. 📦 **盤整突破 (均線糾結)**
   - 趨勢：現價 > 所有均線
   - 訊號：帶量突破 20日高點

※ 全策略皆過濾：今日成交量 > 500 張
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
def download_daily(ticker, period="2y"):
    try:
        df = yf.download(ticker, period=period, interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty: return pd.DataFrame()
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 訊號後績效計算函式
# -------------------------------------------------
def calc_signal_performance(df, signal_idx, days=10, target=0.03):
    """
    訊號後 N 日內是否達標 (target = 0.03 代表 +3%)
    """
    entry_price = df["Close"].iloc[signal_idx]
    future = df["Close"].iloc[signal_idx+1 : signal_idx+1+days]
    if future.empty:
        return None
    max_gain = (future.max() - entry_price) / entry_price
    return max_gain >= target

# -------------------------------------------------
# 策略函式 (略與原本一致)
# -------------------------------------------------
# 這裡省略策略一到四的程式碼，你可以直接沿用你現有的
# 例如：strategy_smc_breakout, strategy_smc_support, strategy_washout_rebound, strategy_consolidation

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
# 回測期間選擇
# -------------------------------------------------
period_option = st.sidebar.radio("回測區間", ["3M", "6M"])
SELECTED_PERIOD = "6mo" if period_option == "6M" else "3mo"

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
                    # 回測資料
                    df_bt = download_daily(t, period=SELECTED_PERIOD)
                    signal_idx = len(df_bt) - 1
                    success = calc_signal_performance(df_bt, signal_idx)
                    
                    r["策略"] = k
                    r["成功"] = success
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

        # -------------------------------------------------
        # 策略績效統計
        # -------------------------------------------------
        st.subheader("📊 策略績效統計（訊號後 10 日 +3%）")
        all_rows = []
        for k in selected:
            all_rows.extend(result[k])

        if all_rows:
            df_all = pd.DataFrame(all_rows)
            stats = (
                df_all.groupby("策略")["成功"]
                .agg(
                    出手次數="count",
                    成功次數="sum",
                    勝率=lambda x: f"{(x.mean()*100):.1f}%"
                )
                .reset_index()
            )
            st.dataframe(stats, use_container_width=True)
