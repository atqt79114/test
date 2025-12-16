import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend  # 引入 ta.trend 用於 MA 計算

# --- 頁面設定 ---
st.set_page_config(page_title="股票策略篩選器 (自動抓榜版)", layout="wide")
st.title("📈 股票策略篩選器 + Yahoo 漲幅榜")
st.markdown("---")


# --- 功能函數：爬取 Yahoo 漲幅榜 ---
@st.cache_data(ttl=300)  # 設定快取，5分鐘內不會重複爬網頁，加快速度
def get_yahoo_top_gainers(limit=50):
    """
    爬取 Yahoo 股市上市與上櫃的漲幅排行榜
    """
    tickers = []

    # 定義要爬取的網址 (上市 + 上櫃)
    urls = [
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",  # 上市
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO"  # 上櫃
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        for url in urls:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, "html.parser")

            # 尋找所有符合股票連結格式的標籤
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.TW'))

            for link in links:
                href = link.get('href')
                match = re.search(r'(\d{4}\.TW[O]?)', href)
                if match:
                    ticker = match.group(1)
                    if ticker not in tickers:
                        tickers.append(ticker)

            if len(tickers) >= limit:
                break

        return tickers[:limit]

    except Exception as e:
        st.error(f"爬取失敗: {e}")
        return []


# --- 策略 1: 盤整突破 (日線) ---
def check_strategy_consolidation(ticker):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if len(df) < 21: return None

        current = df.iloc[-1]
        prev = df.iloc[-2]

        try:
            high_series = df['High']
            if isinstance(high_series, pd.DataFrame):
                high_series = high_series.iloc[:, 0]

            close_val = float(current['Close'])
            vol_current = float(current['Volume'])
            vol_prev = float(prev['Volume'])
        except:
            return None

        past_20_high = high_series[:-1].tail(20).max()

        cond_breakout = close_val > past_20_high
        cond_volume = vol_current > (vol_prev * 2)

        if cond_breakout and cond_volume:
            return {
                "股票": ticker,
                "現價": round(close_val, 2),
                "突破價": round(float(past_20_high), 2),
                "量增倍數": round(vol_current / vol_prev, 1),
                "訊號": "盤整突破"
            }
        return None
    except Exception:
        return None


# --- 策略 2: 5分K 帶量過 20MA ---
def check_strategy_5m_breakout(ticker):
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        if len(df) < 21: return None

        # 處理欄位
        close_series = df['Close']
        if isinstance(close_series, pd.DataFrame): close_series = close_series.iloc[:, 0]

        open_series = df['Open']
        if isinstance(open_series, pd.DataFrame): open_series = open_series.iloc[:, 0]

        vol_series = df['Volume']
        if isinstance(vol_series, pd.DataFrame): vol_series = vol_series.iloc[:, 0]

        # 計算 MA
        ma20 = ta.trend.sma_indicator(close_series, window=20)

        current_close = float(close_series.iloc[-1])
        current_open = float(open_series.iloc[-1])
        current_ma = float(ma20.iloc[-1])
        current_vol = float(vol_series.iloc[-1])
        prev_vol = float(vol_series.iloc[-2])

        # 條件：紅K穿過MA (開低收高於MA) + 量增
        cond_cross = (current_close > current_ma) and (current_open < current_ma)
        cond_volume = current_vol > (prev_vol * 2)

        if cond_cross and cond_volume:
            return {
                "股票": ticker,
                "時間": df.index[-1].strftime('%H:%M'),
                "現價": round(current_close, 2),
                "20MA": round(current_ma, 2),
                "量增倍數": round(current_vol / prev_vol, 1),
                "訊號": "5分K突破"
            }
        return None
    except Exception:
        return None


# --- 策略 3: 高檔飛舞回測不破5日線 (日線) ---
def check_strategy_high_level_dance(ticker):
    """
    策略：高檔飛舞回測不破5日線
    1. 近20日漲幅大於 10% (定義為高檔)。
    2. 今日收盤價較昨日收盤價回檔。
    3. 今日收盤價仍高於 MA5。
    """
    try:
        # 下載至少一個月資料來確保 MA5 穩定
        df = yf.download(ticker, period="1mo", interval="1d", progress=False)

        if len(df) < 21: return None

        # 計算 MA5
        df['MA5'] = trend.sma_indicator(close=df['Close'], window=5, fillna=False)

        # 確保 MA5 有計算值
        if df['MA5'].isnull().iloc[-1]: return None

        # 取最新數據
        today_close = df['Close'].iloc[-1]
        yesterday_close = df['Close'].iloc[-2]
        today_ma5 = df['MA5'].iloc[-1]

        # 條件 1: 近20日漲幅大於 10% (高檔定義)
        price_change_20d = (today_close / df['Close'].iloc[-20]) - 1
        is_high_level = price_change_20d > 0.10

        # 條件 2: 今日收盤價回檔 (今日收盤 < 昨日收盤)
        is_pullback = today_close < yesterday_close

        # 條件 3: 但仍未跌破 MA5 (今日收盤價 > MA5)
        is_above_ma5 = today_close > today_ma5

        if is_high_level and is_pullback and is_above_ma5:
            return {
                "股票": ticker,
                "現價": round(today_close, 2),
                "MA5": round(today_ma5, 2),
                "20日漲幅": f"{round(price_change_20d * 100, 1)}%",
                "訊號": "高檔飛舞"
            }
        return None

    except Exception:
        return None


# --- 側邊欄：設定來源 --- (保持不變)
# ... 側邊欄程式碼 ...


# --- 主程式邏輯 ---
# 這次分成三個欄位來顯示三種策略結果
col1, col2, col3 = st.columns(3)

if st.button("開始掃描策略", type="primary"):
    if not tickers:
        st.error("沒有股票代號！請先輸入或抓取漲幅榜。")
    else:
        st.write(f"正在掃描 {len(tickers)} 檔股票... (請耐心等候，每檔約需 1-2 秒)")

        # 初始化三個策略的結果清單
        results_strat1 = []  # 盤整突破
        results_strat2 = []  # 5分K突破
        results_strat3 = []  # 高檔飛舞

        my_bar = st.progress(0)

        for i, ticker in enumerate(tickers):
            my_bar.progress((i + 1) / len(tickers))

            # 檢查策略 1
            r1 = check_strategy_consolidation(ticker)
            if r1: results_strat1.append(r1)

            # 檢查策略 2
            r2 = check_strategy_5m_breakout(ticker)
            if r2: results_strat2.append(r2)

            # 檢查策略 3
            r3 = check_strategy_high_level_dance(ticker)
            if r3: results_strat3.append(r3)

        my_bar.empty()  # 清除進度條

        # 顯示結果
        with col1:
            st.subheader("🔥 策略 1: 日線盤整突破")
            if results_strat1:
                st.dataframe(pd.DataFrame(results_strat1), use_container_width=True)
            else:
                st.info("無符合條件股票")

        with col2:
            st.subheader("⚡ 策略 2: 5分K 帶量過 20MA")
            if results_strat2:
                st.dataframe(pd.DataFrame(results_strat2), use_container_width=True)
            else:
                st.info("無符合條件股票")

        with col3:
            st.subheader("💃 策略 3: 高檔飛舞 (不破 MA5)")
            if results_strat3:
                st.dataframe(pd.DataFrame(results_strat3), use_container_width=True)
            else:
                st.info("無符合條件股票")