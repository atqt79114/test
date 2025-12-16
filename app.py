import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
from bs4 import BeautifulSoup
import re

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

            # 尋找所有符合股票連結格式的標籤 (Yahoo 網頁結構常變，抓連結最穩)
            # 連結通常長這樣: /quote/2330.TW
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.TW'))

            for link in links:
                # 從 href 中提取代號
                href = link.get('href')
                match = re.search(r'(\d{4}\.TW[O]?)', href)
                if match:
                    ticker = match.group(1)
                    if ticker not in tickers:
                        tickers.append(ticker)

            # 為了演示速度，每個榜單只抓一部分，如果不夠會繼續抓
            if len(tickers) >= limit:
                break

        return tickers[:limit]

    except Exception as e:
        st.error(f"爬取失敗: {e}")
        return []


# --- 側邊欄：設定來源 ---
st.sidebar.header("🔍 股票來源設定")
source_option = st.sidebar.radio("請選擇股票來源：", ["手動輸入代號", "自動抓取 Yahoo 漲幅榜"])

if source_option == "手動輸入代號":
    default_tickers = "2330.TW, 2317.TW, 2454.TW, 3231.TW, 2603.TW"
    ticker_input = st.sidebar.text_area("輸入股票代碼 (逗號分隔)", default_tickers)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    st.sidebar.info(f"目前清單數量: {len(tickers)} 檔")

else:  # 自動抓取模式
    scan_limit = st.sidebar.slider("要掃描前幾名？(建議 30-50 以免太久)", 10, 100, 30)
    if st.sidebar.button("🚀 立即抓取最新漲幅榜"):
        with st.spinner("正在連線 Yahoo 股市抓取資料..."):
            scraped_tickers = get_yahoo_top_gainers(limit=scan_limit)
        st.session_state['auto_tickers'] = scraped_tickers
        st.success(f"成功抓到 {len(scraped_tickers)} 檔熱門股！")

    # 讀取抓到的清單
    tickers = st.session_state.get('auto_tickers', [])
    if tickers:
        st.sidebar.write("目前掃描清單：", tickers)
    else:
        st.sidebar.warning("請點擊按鈕抓取股票")

st.sidebar.markdown("---")
st.sidebar.info("注意：Yahoo Finance 報價有延遲。自動抓取功能依賴 Yahoo 網頁結構，若失效請切回手動。")


# --- 策略 1: 盤整突破 (日線) ---
def check_strategy_consolidation(ticker):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if len(df) < 21: return None

        current = df.iloc[-1]
        prev = df.iloc[-2]

        # 修正：直接取 High 欄位計算最大值
        # 處理 MultiIndex 欄位問題 (新版 yfinance 可能會有雙層標題)
        try:
            high_series = df['High']
            if isinstance(high_series, pd.DataFrame):
                high_series = high_series.iloc[:, 0]  # 取第一欄

            close_val = float(current['Close'])
            vol_current = float(current['Volume'])
            vol_prev = float(prev['Volume'])
        except:
            return None

        # 定義盤整：過去 20 天最高價
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


# --- 主程式邏輯 ---
col1, col2 = st.columns(2)

if st.button("開始掃描策略", type="primary"):
    if not tickers:
        st.error("沒有股票代號！請先輸入或抓取漲幅榜。")
    else:
        st.write(f"正在掃描 {len(tickers)} 檔股票... (請耐心等候，每檔約需 1-2 秒)")

        results_strat1 = []
        results_strat2 = []

        my_bar = st.progress(0)

        for i, ticker in enumerate(tickers):
            my_bar.progress((i + 1) / len(tickers))

            r1 = check_strategy_consolidation(ticker)
            if r1: results_strat1.append(r1)

            r2 = check_strategy_5m_breakout(ticker)
            if r2: results_strat2.append(r2)

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