import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
import numpy as np

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（強化進階版）", layout="wide")
st.title("📈 股票策略篩選器（強化速度 + 回測 + 成本版）")

# -------------------------------------------------
# ▼ 全域參數
# -------------------------------------------------
MIN_VOL = 500_000      
TX_FEE = 0.001425      
TX_TAX = 0.003         
RR = 1.5              


# -------------------------------------------------
# 股票清單
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    
    for mode in ["2", "4"]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        r = requests.get(url, headers=headers, verify=False, timeout=10)
        df = pd.read_html(r.text)[0].iloc[1:]
        
        for item in df[0]:
            code = str(item).split()[0]
            if code.isdigit() and len(code) == 4:
                if mode == "4":
                    tickers.append(f"{code}.TWO")
                else:
                    tickers.append(f"{code}.TW")
            
    return sorted(set(tickers))


# -------------------------------------------------
# ★ Yahoo Finance 批次下載加速
# -------------------------------------------------
@st.cache_data(ttl=600)
def batch_download(tickers):
    df = yf.download(
        tickers, 
        period="2y", 
        interval="1d", 
        group_by="ticker",
        progress=False
    )
    return df


def extract(df_batch, ticker):
    df = df_batch[ticker].copy()
    df.columns = df.columns.str.capitalize()
    return df


# -------------------------------------------------
# ★ 停損停利計算函式
# -------------------------------------------------
def get_exit_prices(entry, ma5_val):

    sl = ma5_val                                    # 停損位
    risk = entry - sl                               # 風險

    if risk <= 0:
        risk = entry * 0.005                        # 0.5% safety

    tp = entry + RR * risk                          # 目標價

    return sl, tp


# -------------------------------------------------
# ★ 回測引擎（加入 TP、成本、真實化）
# -------------------------------------------------
def run_backtest(df, strategy_key, months):

    size = months * 22                              
    if len(df) < size + 200:
        return None

    close = df["Close"]
    high = df["High"]
    low  = df["Low"]
    vol  = df["Volume"]

    ma5  = ta.trend.sma_indicator(close, 5)
    ma10 = ta.trend.sma_indicator(close, 10)
    ma20 = ta.trend.sma_indicator(close, 20)
    ma60 = ta.trend.sma_indicator(close, 60)
    ma120= ta.trend.sma_indicator(close, 120)

    trades = []
    in_pos = False
    entry = None
    sl = None
    tp = None

    start = len(df) - size
    if start < 150:
        start = 150

    for i in range(start, len(df)):

        c = close.iloc[i]
        m5 = ma5.iloc[i]

        if in_pos:

            fee_cost = (entry * TX_FEE) + (entry * TX_TAX)
            now_profit = (c - entry) / entry

            # 出場—停損
            if c < m5:
                trades.append(now_profit*100 - fee_cost*100)
                in_pos = False
                continue

            # 出場—停利
            if c >= tp:
                pp = ((tp-entry)/entry)*100
                trades.append(pp - fee_cost*100)
                in_pos = False
                continue

            continue

        # 進場
        cond = (
            c > ma5.iloc[i] and
            c > ma10.iloc[i] and
            c > ma20.iloc[i] and
            c > ma60.iloc[i] and
            c > ma120.iloc[i]
        )
        if not cond: 
            continue

        if vol.iloc[i] < MIN_VOL:
            continue

        sl, tp = get_exit_prices(c, m5)

        in_pos = True
        entry = c

    if not trades:
        return {"回測勝率":"無訊號","平均獲利":"0%","總交易":0}

    wins = sum(1 for x in trades if x > 0)

    return {
        "回測勝率":f"{round(wins/len(trades)*100,1)}%",
        "平均獲利":f"{round(np.mean(trades),2)}%",
        "總交易":len(trades)
    }



# -------------------------------------------------
# ★ 單股票策略
# -------------------------------------------------
def check_stock(df, months):

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    ma5  = ta.trend.sma_indicator(close, 5).iloc[-1]
    ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
    ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
    ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
    ma120= ta.trend.sma_indicator(close, 120).iloc[-1]

    c    = close.iloc[-1]
    v    = vol.iloc[-1]

    if v < MIN_VOL: 
        return None

    if not (c > ma5 and c > ma10 and c > ma20 and c > ma60 and c > ma120):
        return None

    sl, tp = get_exit_prices(c, ma5)
    back   = run_backtest(df, "", months)

    return {
        "現價":round(c,2),
        "停損":round(sl,2),
        "停利":round(tp,2),
        **back
    }



# -------------------------------------------------
# UI
# -------------------------------------------------
st.sidebar.header("股票來源")
mode = st.sidebar.radio("選擇方式",["手動","全市場"])

if mode=="手動":
    raw = st.sidebar.text_area("輸入股票代碼：","2330.TW, 2317.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]

else:
    if "ALL" not in st.session_state:
        st.session_state["ALL"] = get_all_tw_tickers()

    st.sidebar.write(f"快取清單：{len(st.session_state['ALL'])} 檔")
    limit = st.sidebar.slider("掃描數量",50,2000,300)
    tickers = st.session_state["ALL"][:limit]


period = st.sidebar.radio("回測期間",[3,6],format_func=lambda x:f"{x}個月")


# -------------------------------------------------
# 主程式執行
# -------------------------------------------------
if st.button("開始掃描 🚀"):

    df_batch = batch_download(tickers)

    results = []

    progress = st.progress(0)
    status   = st.empty()
    total    = len(tickers)

    for i,t in enumerate(tickers):

        progress.progress((i+1)/total)
        status.text(f"掃描： {i+1}/{total} → {t}")

        try:
            df = extract(df_batch,t)
            r  = check_stock(df,period)
            if r:
                r["股票"]=t
                results.append(r)
        except:
            continue

    progress.empty()
    status.empty()

    if results:
        df_show = pd.DataFrame(results)
        st.dataframe(df_show,use_container_width=True)

    else:
        st.warning("沒有符合條件股票。")
