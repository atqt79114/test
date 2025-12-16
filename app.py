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
st.set_page_config(page_title="股票策略篩選器 (Yahoo 多榜單全量掃描)", layout="wide")
st.title("📈 股票策略篩選器 (Yahoo 熱門榜單整合)")
st.markdown("---")


# ==============================================================================
# 【清單抓取功能】抓取 Yahoo 股市多個熱門排行榜的股票 (無數量限制)
# ==============================================================================
@st.cache_data(ttl=300)  # 設定快取，5分鐘內更新一次
def get_yahoo_multi_rank_tickers():
    """
    爬取 Yahoo 股市多個熱門排行榜的所有股票代號，並合併去重。
    """
    st.info("正在連線 Yahoo 股市，抓取指定的多個熱門排行榜股票清單...")
    tickers = set()  # 使用 set 避免重複

    # 整合所有您要求的排行榜網址：
    rank_urls = [
        "https://tw.stock.yahoo.com/rank/foreign_buy_sell?exchange=TAI",  # 外資當日買超/賣超 (上市)
        "https://tw.stock.yahoo.com/rank/foreign_buy_sell?exchange=TWO",  # 外資當日買超/賣超 (上櫃)
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",  # 台股漲幅排行 (上市)
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",  # 台股漲幅排行 (上櫃)
        "https://tw.stock.yahoo.com/rank/volume?exchange=TAI",  # 台股成交量排行 (上市)
        "https://tw.stock.yahoo.com/rank/volume?exchange=TWO"  # 台股成交量排行 (上櫃)
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        for url in rank_urls:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, "html.parser")

            # 尋找所有符合股票連結格式的標籤
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))

            for link in links:
                href = link.get('href')
                # 提取代號並統一為 .TW 格式
                match = re.search(r'(\d{4}\.(TW|TWO))', href)
                if match:
                    ticker = match.group(1).replace('.TWO', '.TW')
                    tickers.add(ticker)

        return list(tickers)

    except Exception as e:
        st.error(f"爬取 Yahoo 排行榜失敗: {e}")
        return []


# ==============================================================================


# ==============================================================================
# 【策略函式】(保持不變)
# ==============================================================================

# 策略 1: 盤整突破 (日線)
def check_strategy_consolidation(ticker):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if len(df) < 21: return None
        current = df.iloc[-1]
        prev = df.iloc[-2]

        try:
            high_series = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
            close_val = current['Close'].iloc[0] if isinstance(current['Close'], pd.Series) else float(current['Close'])
            vol_current = current['Volume'].iloc[0] if isinstance(current['Volume'], pd.Series) else float(
                current['Volume'])
            vol_prev = prev['Volume'].iloc[0] if isinstance(prev['Volume'], pd.Series) else float(current['Volume'])
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


# 策略 2: 5分K 帶量過 20MA
def check_strategy_5m_breakout(ticker):
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        if len(df) < 21: return None

        close_series = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
        open_series = df['Open'].iloc[:, 0] if isinstance(df['Open'], pd.DataFrame) else df['Open']
        vol_series = df['Volume'].iloc[:, 0] if isinstance(df['Volume'], pd.DataFrame) else df['Volume']

        ma20 = ta.trend.sma_indicator(close_series, window=20)

        current_close = float(close_series.iloc[-1])
        current_open = float(open_series.iloc[-1])
        current_ma = float(ma20.iloc[-1])
        current_vol = float(vol_series.iloc[-1])
        prev_vol = float(vol_series.iloc[-2])

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


# 策略 3: 高檔飛舞回測不破5日線
def check_strategy_high_level_dance(ticker):
    try:
        df = yf.download(ticker, period="1mo", interval="1d", progress=False)
        if len(df) < 21: return None

        df['MA5'] = trend.sma_indicator(close=df['Close'], window=5, fillna=False)
        if df['MA5'].isnull().iloc[-1]: return None

        today_close = df['Close'].iloc[-1]
        yesterday_close = df['Close'].iloc[-2]
        today_ma5 = df['MA5'].iloc[-1]

        price_change_20d = (today_close / df['Close'].iloc[-20]) - 1
        is_high_level = price_change_20d > 0.10

        is_pullback = today_close < yesterday_close
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


# ==============================================================================


# ==============================================================================
# 【策略列表與側邊欄邏輯】
# ==============================================================================
STRATEGIES = {
    "盤整突破": {"func": check_strategy_consolidation, "emoji": "🔥"},
    "5分K突破": {"func": check_strategy_5m_breakout, "emoji": "⚡"},
    "高檔飛舞": {"func": check_strategy_high_level_dance, "emoji": "💃"}
}

# --- 側邊欄：股票來源設定 ---
st.sidebar.header("🔍 股票來源設定")

source_option = st.sidebar.radio(
    "請選擇股票來源：",
    ["手動輸入代號", "自動抓取 Yahoo 熱門榜單"]
)

if 'yahoo_tickers' not in st.session_state:
    st.session_state['yahoo_tickers'] = []

if source_option == "手動輸入代號":
    default_tickers = "2330.TW, 2317.TW, 2454.TW, 3231.TW, 2603.TW"
    ticker_input = st.sidebar.text_area("輸入股票代碼 (逗號分隔)", default_tickers)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    st.sidebar.info(f"目前清單數量: {len(tickers)} 檔")

else:  # 自動抓取 Yahoo 熱門榜單模式
    # 移除 scan_limit 滑桿，執行全量掃描

    if st.sidebar.button("🚀 立即抓取並準備全量掃描"):
        with st.spinner("正在連線 Yahoo 股市抓取資料..."):
            # 執行無數量限制的抓取
            scraped_tickers = get_yahoo_multi_rank_tickers()
        st.session_state['yahoo_tickers'] = scraped_tickers
        st.success(f"成功抓到 {len(scraped_tickers)} 檔熱門股！")

    # 讀取抓到的清單
    tickers = st.session_state.get('yahoo_tickers', [])
    if tickers:
        # **這裡執行全量掃描：tickers 保持不變**
        st.sidebar.markdown(f"**💡 即將掃描清單：** **{len(tickers)}** 檔")
    else:
        st.sidebar.warning("請點擊按鈕抓取股票")

st.sidebar.markdown("---")

# --- 側邊欄：策略選擇 (Checkbox) ---
st.sidebar.header("🎯 策略篩選")
selected_strategies = []
for name, details in STRATEGIES.items():
    if st.sidebar.checkbox(f"{details['emoji']} {name}", value=False):
        selected_strategies.append(name)

st.sidebar.info("請勾選您想掃描的策略")

# ==============================================================================
# 【主程式執行邏輯】
# ==============================================================================
if st.button("開始掃描策略", type="primary"):
    if not tickers:
        st.error("沒有股票代號！請先在左側輸入或點擊按鈕抓取股票清單。")
    elif not selected_strategies:
        st.warning("請在左側勾選至少一個要執行的策略！")
    else:
        st.write(f"正在執行全量掃描 **{len(tickers)}** 檔股票，共 **{len(selected_strategies)}** 個策略... (請耐心等候)")

        results = {name: [] for name in selected_strategies}
        my_bar = st.progress(0)

        for i, ticker in enumerate(tickers):
            my_bar.progress((i + 1) / len(tickers))

            for name in selected_strategies:
                check_func = STRATEGIES[name]["func"]
                r = check_func(ticker)
                if r:
                    r["策略名稱"] = name
                    results[name].append(r)

            # 防鎖定機制：每掃描 5 檔股票，就暫停 1.5 秒
            if (i + 1) % 5 == 0:
                time.sleep(1.5)

        my_bar.empty()
        st.subheader("📊 掃描結果")

        # 動態顯示結果
        num_cols = len(selected_strategies)
        cols = st.columns(min(num_cols, 3))
        col_index = 0

        for name in selected_strategies:
            current_col_index = col_index % 3
            current_col = cols[current_col_index]

            with current_col:
                emoji = STRATEGIES[name]['emoji']
                st.markdown(f"### {emoji} {name} 訊號")

                if results[name]:
                    df_result = pd.DataFrame(results[name]).drop(columns=['策略名稱'], errors='ignore')
                    st.dataframe(df_result, use_container_width=True)
                else:
                    st.info("無符合條件股票")

            col_index += 1