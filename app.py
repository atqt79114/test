import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import datetime
import warnings
warnings.filterwarnings("ignore")

# ----------------------------
# Streamlit 設定
# ----------------------------
st.set_page_config(page_title="策略選股 + 回測整合版", layout="wide")
st.title("📈 股票策略掃描＋回測（含停利 1:1.5）")

MIN_VOL = 500_000  
RR = 1.5  # 停利 RR 比率 1:1.5

# ----------------------------
# 停利 / 停損模型
# ----------------------------

def compute_sl_tp(entry_price, ma_value, rr=1.5):
    sl = ma_value
    risk = entry_price - sl

    if risk <= 0:
        risk = entry_price * 0.003  # fallback

    tp = entry_price + rr * risk
    return sl, tp


# ----------------------------
# 回測引擎（核心）
# ----------------------------
def run_backtest(df, strategy_func, months=6):

    df = df.copy()

    if len(df) < 200:
        return {"勝率": "N/A", "平均%": "N/A", "次數": 0}

    start_i = len(df) - int(months * 22)
    if start_i < 150:
        start_i = 150

    close = df["Close"]
    high = df["High"]
    volume = df["Volume"]
    ma5 = ta.trend.sma_indicator(close, 5)

    in_pos = False
    entry = sl = tp = None
    pnl_list = []

    for i in range(start_i, len(df)):

        c = close.iloc[i]
        h = high.iloc[i]
        m5 = ma5.iloc[i]

        # --- 出場邏輯 ---
        if in_pos:

            # 停利：今日最高 >= TP
            if h >= tp:
                profit_pct = (tp - entry) / entry * 100
                pnl_list.append(profit_pct)
                in_pos = False
                continue

            # 停損：收盤跌破 5MA
            if c < m5:
                profit_pct = (c - entry) / entry * 100
                pnl_list.append(profit_pct)
                in_pos = False
                continue

            continue

        # --- 入場邏輯（依你策略） ---
        try:
            signal = strategy_func(df.iloc[: i+1])
        except:
            signal = False

        if signal:
            entry = c
            sl, tp = compute_sl_tp(entry, m5)
            in_pos = True
            continue

    if len(pnl_list) == 0:
        return {"勝率": "0%", "平均%": "0%", "次數": 0}

    wins = sum(1 for x in pnl_list if x > 0)
    win_rate = round(wins / len(pnl_list) * 100, 1)
    avg = round(np.mean(pnl_list), 2)

    return {
        "勝率": f"{win_rate}%",
        "平均%": f"{avg}%",
        "次數": len(pnl_list)
    }


# ----------------------------
# 你的四大策略（完整保留）
# ----------------------------

def strategy_smc_breakout(df):
    if len(df) < 60:
        return False

    close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].iloc[-1]

    hh = high.rolling(20).max().iloc[-2]
    ll = low.rolling(20).min().iloc[-2]

    if volume < MIN_VOL:
        return False

    cond_break = prev_close <= hh and close > hh
    cond_retest = low.iloc[-1] > hh * 0.995

    return cond_break or cond_retest


def strategy_smc_support(df):
    if len(df) < 60:
        return False

    close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2]
    low = df["Low"]
    volume = df["Volume"].iloc[-1]

    ll = low.rolling(20).min().iloc[-2]

    if volume < MIN_VOL:
        return False

    cond_hit = low.iloc[-1] <= ll * 1.005
    cond_reject = close > prev_close

    return cond_hit and cond_reject


def strategy_washout(df):
    if len(df) < 80:
        return False

    close = df["Close"].iloc[-1]
    low = df["Low"].iloc[-1]
    open_ = df["Open"].iloc[-1]
    volume = df["Volume"].iloc[-1]

    ma20 = ta.trend.sma_indicator(df["Close"], 20).iloc[-1]

    cond_down = open_ > close
    cond_recover = close > ma20
    cond_vol = volume > df["Volume"].rolling(20).mean().iloc[-1] * 1.5

    return cond_down and cond_recover and cond_vol


def strategy_consolidation(df):
    if len(df) < 150:
        return False

    close = df["Close"].iloc[-1]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].iloc[-1]

    hh = high.rolling(40).max().iloc[-1]
    ll = low.rolling(40).min().iloc[-1]

    cond_range = (hh - ll) / ll < 0.08
    cond_break = close > hh

    if volume < MIN_VOL:
        return False

    return cond_range and cond_break


STRATEGY_MAP = {
    "SMC Breakout": strategy_smc_breakout,
    "SMC Support": strategy_smc_support,
    "Washout": strategy_washout,
    "Consolidation": strategy_consolidation
}


# ----------------------------
# UI
# ----------------------------
st.sidebar.header("設定")

strategy_name = st.sidebar.selectbox(
    "選擇策略", list(STRATEGY_MAP.keys())
)

months = st.sidebar.radio(
    "回測期間", [3, 6, 12], index=1, format_func=lambda x: f"{x} 個月"
)

user_input = st.sidebar.text_area("輸入股票代碼（用逗號）", "2330.TW, 2317.TW")
tickers = [x.strip() for x in user_input.split(",") if x.strip()]

if st.button("開始執行 🚀"):

    result_list = []
    progress = st.progress(0)

    df_batch = yf.download(tickers, period="2y", group_by="ticker", progress=False)

    for i, t in enumerate(tickers):
        progress.progress((i+1)/len(tickers))

        try:
            df = df_batch[t].copy()
        except:
            continue

        df = df.rename(columns=lambda x: x.capitalize())

        strat_func = STRATEGY_MAP[strategy_name]

        r = run_backtest(df, strat_func, months=months)
        r["股票"] = t
        result_list.append(r)

    st.subheader("結果")
    df_show = pd.DataFrame(result_list)
    st.dataframe(df_show, use_container_width=True)
