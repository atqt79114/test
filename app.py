import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time
# 【非常重要！確保這段程式碼存在於檔案頂部】
import ssl
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    # 針對舊版 Python 的處理
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
# ==============================================================================
# 以下是您的函式定義和頁面設定
# --- 頁面設定 ---
st.set_page_config(page_title="股票策略篩選器 (全市場掃描版)", layout="wide")
st.title("📈 股票策略篩選器 (多策略選擇)")
st.markdown("---")


# ==============================================================================
# 【功能函式】爬取所有上市上櫃股票代號清單
# ==============================================================================
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    """
    從公開來源爬取所有台灣上市櫃股票代號清單
    """
    st.info("正在連線 TAI/OTC 網站抓取所有股票代號清單...")
    all_tickers = []

    # 爬取上市公司清單 (TSE)
    try:
        url_tse = 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=2'
        df_tse = pd.read_html(url_tse)[0]
        df_tse = df_tse.iloc[1:]

        for item in df_tse[0]:
            parts = item.split()
            if len(parts) > 0 and parts[0].isdigit() and len(parts[0]) == 4:
                all_tickers.append(f"{parts[0]}.TW")
    except Exception as e:
        st.error(f"爬取上市公司清單失敗: {e}")

    # 爬取上櫃公司清單 (OTC)
    try:
        url_otc = 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=4'
        df_otc = pd.read_html(url_otc)[0]
        df_otc = df_otc.iloc[1:]

        for item in df_otc[0]:
            parts = item.split()
            if len(parts) > 0 and parts[0].isdigit() and len(parts[0]) == 4:
                all_tickers.append(f"{parts[0]}.TW")
    except Exception as e:
        st.error(f"爬取上櫃公司清單失敗: {e}")

    unique_tickers = list(set(all_tickers))
    return unique_tickers


# ==============================================================================
# 【策略函式】
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
            vol_prev = prev['Volume'].iloc[0] if isinstance(prev['Volume'], pd.Series) else float(prev['Volume'])

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
# 【策略列表與側邊欄邏輯】
# ==============================================================================

# 定義策略字典
STRATEGIES = {
    "盤整突破": {"func": check_strategy_consolidation, "emoji": "🔥"},
    "5分K突破": {"func": check_strategy_5m_breakout, "emoji": "⚡"},
    "高檔飛舞": {"func": check_strategy_high_level_dance, "emoji": "💃"}
}

# --- 側邊欄：股票來源設定 ---
st.sidebar.header("🔍 股票來源設定")

source_option = st.sidebar.radio(
    "請選擇股票來源：",
    ["手動輸入代號", "自動抓取所有上市上櫃股"]
)

if 'all_tickers' not in st.session_state:
    st.session_state['all_tickers'] = []

if source_option == "手動輸入代號":
    default_tickers = "2330.TW, 2317.TW, 2454.TW, 3231.TW, 2603.TW"
    ticker_input = st.sidebar.text_area("輸入股票代碼 (逗號分隔)", default_tickers)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    st.sidebar.info(f"目前清單數量: {len(tickers)} 檔")

else:  # 自動抓取所有上市上櫃股模式
    if st.sidebar.button("🚀 取得所有股票清單"):
        with st.spinner("正在連線 TAI/OTC 抓取所有股票代號清單..."):
            all_list = get_all_tw_tickers()
        st.session_state['all_tickers'] = all_list
        st.success(f"成功抓到 {len(all_list)} 檔股票！")

    tickers = st.session_state.get('all_tickers', [])
    if tickers:
        scan_limit = st.sidebar.slider(
            "要掃描前幾檔？ (掃描越多越慢，請控制數量)",
            10,
            min(len(tickers), 100),
            30
        )
        st.sidebar.write(f"目前掃描清單數量：{scan_limit} 檔 (總清單數: {len(st.session_state['all_tickers'])})")
        tickers = tickers[:scan_limit]
    else:
        st.sidebar.warning("請點擊按鈕取得所有股票清單")

st.sidebar.markdown("---")

# --- 側邊欄：策略選擇 (Checkbox) ---
st.sidebar.header("🎯 策略篩選")
# 根據策略字典創建 Checkbox
selected_strategies = []
for name, details in STRATEGIES.items():
    if st.sidebar.checkbox(f"{details['emoji']} {name}", value=True if name == "高檔飛舞" else False):
        selected_strategies.append(name)

st.sidebar.info("請勾選您想掃描的策略")

# ==============================================================================
# 【主程式執行邏輯】
# ==============================================================================
if st.button("開始掃描策略", type="primary"):
    if not tickers:
        st.error("沒有股票代號！請先在左側輸入或抓取股票清單。")
    elif not selected_strategies:
        st.warning("請在左側勾選至少一個要執行的策略！")
    else:
        st.write(f"正在掃描 {len(tickers)} 檔股票，執行 {len(selected_strategies)} 個策略... (請耐心等候)")

        # 動態創建結果字典，只包含被選中的策略
        results = {name: [] for name in selected_strategies}

        my_bar = st.progress(0)

        for i, ticker in enumerate(tickers):
            my_bar.progress((i + 1) / len(tickers))

            for name in selected_strategies:
                # 動態呼叫對應的策略函式
                check_func = STRATEGIES[name]["func"]
                r = check_func(ticker)
                if r:
                    # 將股票代號加入結果字典中
                    r["策略名稱"] = name  # 新增策略名稱欄位
                    results[name].append(r)

            # 防鎖定機制：每掃描 5 檔股票，就暫停 1.5 秒
            if (i + 1) % 5 == 0:
                time.sleep(1.5)

        my_bar.empty()  # 清除進度條

        st.subheader("📊 掃描結果")

        # 動態顯示結果：創建欄位來顯示結果
        num_cols = len(selected_strategies)
        # 限制欄位數最多為 3，超過就用循環來排版
        cols = st.columns(min(num_cols, 3))

        col_index = 0

        for name in selected_strategies:
            current_col = cols[col_index % 3]  # 循環使用 col1, col2, col3

            with current_col:
                emoji = STRATEGIES[name]['emoji']
                st.markdown(f"### {emoji} {name} 訊號")

                if results[name]:
                    # 移除策略名稱欄位，因為標題已經有了
                    df_result = pd.DataFrame(results[name]).drop(columns=['策略名稱'], errors='ignore')
                    st.dataframe(df_result, use_container_width=True)
                else:
                    st.info("無符合條件股票")

            col_index += 1

# --- 結束 ---