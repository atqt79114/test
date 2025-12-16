import streamlit as st
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="股票策略篩選器", layout="wide")
st.title("📈 即時股票策略篩選器")
st.markdown("---")

# --- 側邊欄：輸入股票代碼 ---
st.sidebar.header("設定")
default_tickers = "2330.TW, 2317.TW, 2454.TW, 0050.TW, TSLA, AAPL"
ticker_input = st.sidebar.text_area("輸入股票代碼 (用逗號分隔)", default_tickers)
tickers = [t.strip().upper() for t in ticker_input.split(",")]

st.sidebar.info("注意：Yahoo Finance 資料通常有 15 分鐘延遲。台股代號請加上 .TW")


# --- 策略 1: 盤整突破 (日線) ---
def check_strategy_consolidation(ticker):
    try:
        # 取得日線資料 (取足夠的天數來計算盤整區間)
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)

        if len(df) < 21:
            return None

        # 取得最新一天與前一天的資料
        current = df.iloc[-1]
        prev = df.iloc[-2]

        # 定義盤整區間：過去 20 天(不含當天)的最高價
        past_20_days = df[:-1].tail(20)
        resistance_level = past_20_days['High'].max()

        # 策略條件
        # 1. 突破：今日收盤 > 過去20天最高價
        # 2. 爆量：今日成交量 > 昨天成交量 * 2

        cond_breakout = current['Close'] > resistance_level
        cond_volume = current['Volume'] > (prev['Volume'] * 2)

        if cond_breakout and cond_volume:
            return {
                "股票": ticker,
                "現價": round(float(current['Close']), 2),
                "突破價": round(float(resistance_level), 2),
                "成交量倍數": round(float(current['Volume'] / prev['Volume']), 1),
                "訊號": "盤整突破 🚀"
            }
        return None
    except Exception as e:
        return None


# --- 策略 2: 假跌破 (5分K 突破 20MA) ---
def check_strategy_5m_breakout(ticker):
    try:
        # 取得 5分K 資料 (Yahoo 最多取 60天內的 5分K，這裡取 1 天即可)
        df = yf.download(ticker, period="5d", interval="5m", progress=False)

        if len(df) < 21:
            return None

        # 計算 20MA
        df['MA20'] = ta.trend.sma_indicator(df['Close'], window=20)

        # 取得最新一根與前一根 K 棒
        current = df.iloc[-1]
        prev = df.iloc[-2]

        # 策略條件
        # 1. 價格突破：收盤價 > 20MA 且 (為了確認是剛突破，要求前一根在 20MA 下 或 開盤在下)
        #    這裡簡化為：目前收盤 > 20MA 且 開盤 < 20MA (實體紅K穿過)
        # 2. 爆量：當前成交量 > 前一根成交量 * 2

        cond_price = (current['Close'] > current['MA20']) and (current['Open'] < current['MA20'])
        cond_volume = current['Volume'] > (prev['Volume'] * 2)

        if cond_price and cond_volume:
            return {
                "股票": ticker,
                "時間": df.index[-1].strftime('%H:%M'),
                "現價": round(float(current['Close']), 2),
                "20MA": round(float(current['MA20']), 2),
                "成交量倍數": round(float(current['Volume'] / prev['Volume']), 1),
                "訊號": "5分K 帶量過 20MA (假跌破翻紅) ⚡"
            }
        return None
    except Exception as e:
        return None


# --- 主程式邏輯 ---

col1, col2 = st.columns(2)

if st.button("開始掃描"):
    st.write(f"正在掃描 {len(tickers)} 檔股票...")

    results_strat1 = []
    results_strat2 = []

    # 建立進度條
    progress_bar = st.progress(0)

    for i, ticker in enumerate(tickers):
        # 更新進度條
        progress_bar.progress((i + 1) / len(tickers))

        # 檢查策略 1
        res1 = check_strategy_consolidation(ticker)
        if res1:
            results_strat1.append(res1)

        # 檢查策略 2
        res2 = check_strategy_5m_breakout(ticker)
        if res2:
            results_strat2.append(res2)

    # --- 顯示結果 ---

    with col1:
        st.subheader("策略 1: 盤整突破 (日線 + 爆量)")
        if results_strat1:
            df_res1 = pd.DataFrame(results_strat1)
            st.dataframe(df_res1, use_container_width=True)
        else:
            st.info("目前清單中無符合條件股票")

    with col2:
        st.subheader("策略 2: 假跌破 (5分K + 20MA + 爆量)")
        st.markdown("*定義：5分K一根紅K穿過20MA且量增*")
        if results_strat2:
            df_res2 = pd.DataFrame(results_strat2)
            st.dataframe(df_res2, use_container_width=True)
        else:
            st.info("目前清單中無符合條件股票")

else:
    st.write("請點擊「開始掃描」按鈕來執行策略檢查。")