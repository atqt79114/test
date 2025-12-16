import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time

# --- 頁面設定 ---
st.set_page_config(page_title="股票策略篩選器 (多頭排列版)", layout="wide")
st.title("📈 股票策略篩選器 (Yahoo 多榜單全量掃描)")


# ==============================================================================
# 【清單抓取功能】Yahoo 股市多個熱門排行榜 (無數量限制)
# ==============================================================================
@st.cache_data(ttl=300)
def get_yahoo_multi_rank_tickers():
    tickers = set()
    rank_urls = [
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/foreign-investor-buy?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-buy?exchange=TWO"
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        for url in rank_urls:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, "html.parser")
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))
            for link in links:
                match = re.search(r'(\d{4}\.(TW|TWO))', link.get('href'))
                if match: tickers.add(match.group(1).replace('.TWO', '.TW'))
        return list(tickers)
    except Exception:
        return []


# ==============================================================================
# 【策略 3: 高檔飛舞 (修正：高檔 = 均線多頭排列)】
# ==============================================================================
def check_strategy_high_level_dance(ticker):
    try:
        # 下載至少 40 天資料以計算 MA20
        df = yf.download(ticker, period="2mo", interval="1d", progress=False)
        if len(df) < 30: return None

        # 計算均線
        df['MA5'] = trend.sma_indicator(df['Close'], window=5)
        df['MA10'] = trend.sma_indicator(df['Close'], window=10)
        df['MA20'] = trend.sma_indicator(df['Close'], window=20)
        df['Vol_MA20'] = trend.sma_indicator(df['Volume'], window=20)

        # 取昨日數據 (排除今日判斷)
        yest = df.iloc[-1]

        # --- 條件 1: 高檔確認 (均線多頭排列) ---
        # 定義：MA5 > MA10 > MA20
        is_bullish = yest['MA5'] > yest['MA10'] and yest['MA10'] > yest['MA20']

        # --- 條件 2: 爆量黑 K ---
        is_black_k = yest['Close'] < yest['Open']
        # 爆量：昨日量 > 20日均量 * 1.5
        is_high_vol = yest['Volume'] > (yest['Vol_MA20'] * 1.5)

        if is_bullish and is_black_k and is_high_vol:
            return {
                "股票": ticker,
                "昨日收盤": round(float(yest['Close']), 2),
                "昨日量": int(yest['Volume']),
                "MA5/10/20": f"{round(float(yest['MA5']), 1)}/{round(float(yest['MA10']), 1)}/{round(float(yest['MA20']), 1)}",
                "量增倍數": f"{round(float(yest['Volume'] / yest['Vol_MA20']), 1)}倍",
                "訊號": "高檔飛舞 (爆量黑K+多頭排列)"
            }
        return None
    except:
        return None


# ==============================================================================
# 其餘策略及主程式邏輯
# ==============================================================================
def check_strategy_consolidation(ticker):
    # 簡化版盤整突破...
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if len(df) < 21: return None
        curr, prev = df.iloc[-1], df.iloc[-2]
        past_high = df['High'].iloc[:-1].tail(20).max()
        if curr['Close'] > past_high and curr['Volume'] > (prev['Volume'] * 2):
            return {"股票": ticker, "現價": round(float(curr['Close']), 2), "訊號": "盤整突破"}
    except:
        return None
    return None


def check_strategy_5m_breakout(ticker):
    # 5分K突破邏輯...
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        if len(df) < 21: return None
        ma20 = trend.sma_indicator(df['Close'], window=20)
        if df['Close'].iloc[-1] > ma20.iloc[-1] and df['Open'].iloc[-1] < ma20.iloc[-1] and df['Volume'].iloc[-1] > (
                df['Volume'].iloc[-2] * 2):
            return {"股票": ticker, "現價": round(float(df['Close'].iloc[-1]), 2), "訊號": "5分K突破"}
    except:
        return None
    return None


STRATEGIES = {
    "盤整突破": {"func": check_strategy_consolidation, "emoji": "🔥"},
    "5分K突破": {"func": check_strategy_5m_breakout, "emoji": "⚡"},
    "高檔飛舞": {"func": check_strategy_high_level_dance, "emoji": "💃"}
}

# --- 側邊欄與執行 ---
st.sidebar.header("🔍 股票來源設定")
source_option = st.sidebar.radio("來源：", ["手動輸入", "自動抓取 Yahoo 熱門榜單"])

if 'yahoo_tickers' not in st.session_state: st.session_state['yahoo_tickers'] = []

if source_option == "手動輸入":
    ticker_input = st.sidebar.text_area("代碼 (逗號分隔)", "2330.TW, 2317.TW")
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
else:
    if st.sidebar.button("🚀 抓取 Yahoo 排行榜"):
        st.session_state['yahoo_tickers'] = get_yahoo_multi_rank_tickers()
        st.success(f"已抓取 {len(st.session_state['yahoo_tickers'])} 檔")
    tickers = st.session_state['yahoo_tickers']

st.sidebar.header("🎯 策略篩選")
selected_strategies = [name for name, details in STRATEGIES.items() if
                       st.sidebar.checkbox(f"{details['emoji']} {name}")]

if st.button("開始全量掃描"):
    if not tickers or not selected_strategies:
        st.error("請確保已抓取清單且勾選策略")
    else:
        results = {name: [] for name in selected_strategies}
        pbar = st.progress(0)
        for i, ticker in enumerate(tickers):
            pbar.progress((i + 1) / len(tickers))
            for name in selected_strategies:
                res = STRATEGIES[name]["func"](ticker)
                if res: results[name].append(res)
            if (i + 1) % 5 == 0: time.sleep(1)

        for name in selected_strategies:
            st.subheader(f"{STRATEGIES[name]['emoji']} {name}")
            if results[name]:
                st.dataframe(pd.DataFrame(results[name]))
            else:
                st.info("無符合標的")