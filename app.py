import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time
import random

# --- 頁面設定 ---
st.set_page_config(page_title="股票策略篩選器 (穩定修正版)", layout="wide")
st.title("📈 股票策略篩選器 (Yahoo 多榜單全量掃描)")


# ==============================================================================
# 【清單抓取功能】Yahoo 股市排行榜 (增加防護與偵錯)
# ==============================================================================
@st.cache_data(ttl=600)
def get_yahoo_multi_rank_tickers():
    tickers = set()
    # 您指定的六個排行榜網址
    rank_urls = [
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",

        "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",

        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TAI",

        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TWO",

        "https://tw.stock.yahoo.com/rank/foreign-investor-buy?exchange=TAI",

        "https://tw.stock.yahoo.com/rank/foreign-investor-buy?exchange=TWO"
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }

    progress_text = st.empty()
    for i, url in enumerate(rank_urls):
        try:
            progress_text.text(f"正在抓取排行榜 ({i + 1}/{len(rank_urls)})...")
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                # 尋找包含股票代號的連結，通常格式為 /quote/2330.TW
                links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))
                for link in links:
                    href = link.get('href')
                    match = re.search(r'(\d{4}\.(TW|TWO))', href)
                    if match:
                        # 統一轉為 yfinance 格式 (.TW 或 .TWO)
                        tickers.add(match.group(1))
            time.sleep(random.uniform(1, 2))  # 隨機延遲防封鎖
        except Exception as e:
            st.warning(f"網址 {url} 抓取失敗: {e}")

    progress_text.empty()
    return sorted(list(tickers))


# ==============================================================================
# 【策略函式】
# ==============================================================================

# 策略 1: 盤整突破
def check_strategy_consolidation(ticker):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if len(df) < 22: return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        # 過去 20 天最高價 (不含今天)
        past_high = df['High'].iloc[:-1].tail(20).max()
        # 條件：收盤突破前高 且 量增 1.5 倍
        if curr['Close'] > past_high and curr['Volume'] > (prev['Volume'] * 1.5):
            return {"股票": ticker, "現價": round(float(curr['Close']), 2),
                    "量增": f"{round(float(curr['Volume'] / prev['Volume']), 1)}倍", "訊號": "盤整突破"}
    except:
        return None
    return None


# 策略 2: 5分K 帶量過 20MA
def check_strategy_5m_breakout(ticker):
    try:
        df = yf.download(ticker, period="2d", interval="5m", progress=False, timeout=10)
        if len(df) < 21: return None
        ma20 = trend.sma_indicator(df['Close'], window=20)
        curr_c = df['Close'].iloc[-1]
        curr_o = df['Open'].iloc[-1]
        curr_v = df['Volume'].iloc[-1]
        prev_v = df['Volume'].iloc[-2]
        if curr_c > ma20.iloc[-1] and curr_o < ma20.iloc[-1] and curr_v > (prev_v * 1.8):
            return {"股票": ticker, "現價": round(float(curr_c), 2), "時間": df.index[-1].strftime('%H:%M'),
                    "訊號": "5分K突破"}
    except:
        return None
    return None


# 策略 3: 高檔飛舞 (多頭排列 + 爆量黑K)
def check_strategy_high_level_dance(ticker):
    try:
        # 下載 3 個月資料確保均線穩定
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if len(df) < 25: return None

        df['MA5'] = trend.sma_indicator(df['Close'], window=5)
        df['MA10'] = trend.sma_indicator(df['Close'], window=10)
        df['MA20'] = trend.sma_indicator(df['Close'], window=20)
        df['Vol_MA20'] = trend.sma_indicator(df['Volume'], window=20)

        # 取昨日 (最後一筆完整交易日)
        yest = df.iloc[-1]

        # 條件 1: 多頭排列 MA5 > MA10 > MA20
        is_bullish = yest['MA5'] > yest['MA10'] and yest['MA10'] > yest['MA20']
        # 條件 2: 黑K (收 < 開)
        is_black_k = yest['Close'] < yest['Open']
        # 條件 3: 爆量 (昨日量 > 20日均量 1.5 倍)
        is_high_vol = yest['Volume'] > (yest['Vol_MA20'] * 1.5)

        if is_bullish and is_black_k and is_high_vol:
            return {
                "股票": ticker,
                "昨日收盤": round(float(yest['Close']), 2),
                "量增倍數": f"{round(float(yest['Volume'] / yest['Vol_MA20']), 1)}倍",
                "訊號": "高檔飛舞"
            }
    except:
        return None
    return None


# ==============================================================================
# 【側邊欄與介面邏輯】
# ==============================================================================
STRATEGIES = {
    "盤整突破": {"func": check_strategy_consolidation, "emoji": "🔥"},
    "5分K突破": {"func": check_strategy_5m_breakout, "emoji": "⚡"},
    "高檔飛舞": {"func": check_strategy_high_level_dance, "emoji": "💃"}
}

st.sidebar.header("🔍 股票來源")
source_option = st.sidebar.radio("來源選擇：", ["自動抓取 Yahoo 熱門榜單", "手動輸入代號"])

if 'all_tickers' not in st.session_state:
    st.session_state['all_tickers'] = []

if source_option == "自動抓取 Yahoo 熱門榜單":
    if st.sidebar.button("🚀 更新 Yahoo 排行榜清單"):
        with st.spinner("抓取中..."):
            st.session_state['all_tickers'] = get_yahoo_multi_rank_tickers()
        if st.session_state['all_tickers']:
            st.sidebar.success(f"成功抓取 {len(st.session_state['all_tickers'])} 檔")
        else:
            st.sidebar.error("抓取失敗，請檢查網路或稍後再試")

    current_tickers = st.session_state['all_tickers']
    if current_tickers:
        with st.sidebar.expander("查看目前清單"):
            st.write(", ".join(current_tickers))
else:
    ticker_input = st.sidebar.text_area("代號 (逗號分隔)", "2330.TW, 2317.TW, 2454.TW, 3231.TW")
    current_tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

st.sidebar.markdown("---")
st.sidebar.header("🎯 策略篩選")
active_strategies = []
for name, data in STRATEGIES.items():
    if st.sidebar.checkbox(f"{data['emoji']} {name}", value=(name == "高檔飛舞")):
        active_strategies.append(name)

# ==============================================================================
# 【執行掃描】
# ==============================================================================
if st.button("開始全量掃描策略", type="primary"):
    if not current_tickers:
        st.error("目前沒有股票清單，請先點擊左側『更新 Yahoo 排行榜清單』")
    elif not active_strategies:
        st.warning("請至少勾選一個策略")
    else:
        st.write(f"正在掃描 {len(current_tickers)} 檔股票...")
        results = {name: [] for name in active_strategies}
        pbar = st.progress(0)

        # 建立一個容器來顯示即時進度
        status_text = st.empty()

        for i, ticker in enumerate(current_tickers):
            pbar.progress((i + 1) / len(current_tickers))
            status_text.text(f"處理中: {ticker} ({i + 1}/{len(current_tickers)})")

            for name in active_strategies:
                res = STRATEGIES[name]["func"](ticker)
                if res:
                    results[name].append(res)

            # 每 10 檔稍作停頓，防止被 yfinance 封鎖 IP
            if (i + 1) % 10 == 0:
                time.sleep(0.5)

        status_text.empty()
        st.success("掃描完成！")

        # 顯示結果
        for name in active_strategies:
            st.subheader(f"{STRATEGIES[name]['emoji']} {name} 結果")
            if results[name]:
                st.dataframe(pd.DataFrame(results[name]), use_container_width=True)
            else:
                st.info(f"暫無符合「{name}」條件的股票")