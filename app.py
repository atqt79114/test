import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 黑色專業版", layout="wide")

# --- UI 樣式優化 (黑色背景、白色字體) ---
st.markdown("""
    <style>
    /* 全域背景與文字 */
    .stApp { background-color: #000000; color: #ffffff; }
    h1, h2, h3, p, span, label, div { color: #ffffff !important; }
    
    /* 側邊欄樣式 */
    section[data-testid="stSidebar"] { background-color: #111111 !important; border-right: 1px solid #333; }
    section[data-testid="stSidebar"] .stMarkdown p { font-size: 16px; font-weight: bold; }

    /* 按鈕樣式 */
    .stButton>button { width: 100%; background-color: #ff4b4b; color: white !important; font-weight: bold; border: none; border-radius: 5px; }
    .stButton>button:hover { background-color: #ff3333; border: 1px solid #ffffff; }

    /* 表格樣式 */
    div[data-testid="stTable"] table { color: #ffffff !important; border-collapse: collapse; width: 100%; }
    div[data-testid="stTable"] th { background-color: #222222 !important; color: #00d1ff !important; border: 1px solid #444; }
    div[data-testid="stTable"] td { border: 1px solid #444; }

    /* 多選下拉選單文字顏色 */
    div[data-baseweb="select"] span { color: #000000 !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 實時策略系統")

# ==============================================================================
# 【核心功能：熱門清單抓取】
# ==============================================================================
@st.cache_data(ttl=600)
def fetch_hot_list():
    tickers = set()
    urls = [
       "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/volume?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/volume?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TWO"
    ]
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))
            for link in links:
                m = re.search(r'(\d{4}\.(TW|TWO))', link.get('href'))
                if m: tickers.add(m.group(1))
        except: continue
    return sorted(list(tickers))

# ==============================================================================
# 【策略運算：精確邏輯寫入】
# ==============================================================================
def analyze_strategy(ticker, mode):
    try:
        # 判斷是需要日線還是 5 分線
        interval = "5m" if "5分k" in mode.lower() else "1d"
        period = "5d" if interval == "5m" else "60d"
        
        raw_df = yf.download(ticker, period=period, interval=interval, progress=False, timeout=10)
        if raw_df.empty or len(raw_df) < 25: return None
        
        df = raw_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 1. 日線：盤整突破 5MA
        if mode == "🚀 日線盤整突破 5MA":
            df['MA5'] = trend.sma_indicator(df['Close'], window=5)
            # 盤整定義：近 10 日高低差 < 5%
            recent = df.iloc[-11:-1]
            price_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
            # 突破定義：今日收盤 > 近 10 日最高點 且 收盤 > 5MA
            if price_range < 0.05 and df['Close'].iloc[-1] > recent['High'].max() and df['Close'].iloc[-1] > df['MA5'].iloc[-1]:
                return df.iloc[-1], df['MA5'].iloc[-1], df['MA10'].iloc[-1], df['MA20'].iloc[-1]

        # 2. 5分k：爆量突破 20MA
        elif mode == "⚡ 5分k爆量突破 20MA":
            df['MA20'] = trend.sma_indicator(df['Close'], window=20)
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            # 爆量：當前量 > 前一根 2 倍；突破：收盤由下往上穿過 20MA
            if curr['Volume'] > (prev['Volume'] * 2) and prev['Close'] < curr['MA20'] and curr['Close'] > curr['MA20']:
                # 這裡 10MA/20MA 仍用日線概念或顯示當前 5 分線均線
                return curr, curr['Close'], curr['MA20'], curr['MA20'] 
        
        # 3. 守護生命線 (原邏輯保留)
        elif mode == "🛡️ 守護生命線":
            df['MA10'] = trend.sma_indicator(df['Close'], window=10)
            df['MA20'] = trend.sma_indicator(df['Close'], window=20)
            if df['Close'].iloc[-1] > df['MA20'].iloc[-1] and (df['Low'].iloc[-1] < df['MA10'].iloc[-1] or df['Close'].iloc[-2] < df['MA10'].iloc[-2]):
                return df.iloc[-1], df['Close'].iloc[-1], df['MA10'].iloc[-1], df['MA20'].iloc[-1]

        return None
    except: return None

# ==============================================================================
# 【介面佈局】
# ==============================================================================
st.sidebar.markdown("### 📂 資料庫管理")
if st.sidebar.button("🚨 強制更新排行榜清單"):
    st.session_state['ticker_list'] = fetch_hot_list()
    st.sidebar.success(f"已抓取 {len(st.session_state['ticker_list'])} 檔標的")

ticker_pool = st.session_state.get('ticker_list', [])

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 策略篩選模式")
selected_mode = st.sidebar.radio("請選擇邏輯：", 
    ["🚀 日線盤整突破 5MA", "⚡ 5分k爆量突破 20MA", "🛡️ 守護生命線"])

# --- 開始執行掃描 ---
if st.button("🔴 開始執行全量策略掃描"):
    if not ticker_pool:
        st.error("請先點擊左側『強制更新排行榜清單』")
    else:
        results = []
        bar = st.progress(0)
        status = st.empty()
        
        for i, t in enumerate(ticker_pool):
            bar.progress((i + 1) / len(ticker_pool))
            status.markdown(f"**分析中:** `{t}`")
            strat_data = analyze_strategy(t, selected_mode)
            if strat_data:
                curr_row, m5, m10, m20 = strat_data
                results.append({
                    "代號": t,
                    "現價": round(float(curr_row['Close']), 2),
                    "5MA": round(float(m5), 2),
                    "10MA": round(float(m10), 2),
                    "20MA": round(float(m20), 2),
                    "成交量": int(curr_row['Volume']),
                    "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{t}/chart"
                })
        
        status.empty()
        st.session_state['final_results'] = results
        if not results:
            st.warning("目前市場無符合標的，請更換策略或稍後再掃描。")

# --- 顯示結果與勾選 ---
if 'final_results' in st.session_state and st.session_state['final_results']:
    res_df = pd.DataFrame(st.session_state['final_results'])
    
    st.markdown("### ✅ 符合標的 (勾選後顯示明細與線圖)")
    selected_tickers = st.multiselect("選擇股票：", options=res_df['代號'].tolist(), default=res_df['代號'].tolist()[:3])
    
    if selected_tickers:
        detail_df = res_df[res_df['代號'].isin(selected_tickers)]
        st.table(detail_df[['代號', '現價', '5MA', '10MA', '20MA', '成交量']])
        
        # 快速線圖連結
        st.markdown("#### 📈 線圖快速通道")
        cols = st.columns(len(selected_tickers))
        for idx, row in detail_df.reset_index().iterrows():
            with cols[idx]:
                st.markdown(f"**[{row['代號']} 線圖]({row['Yahoo連結']})**")
    else:
        st.info("請從上方下拉選單勾選股票以檢視詳情。")

st.sidebar.markdown("---")
st.sidebar.write("⌛ 系統狀態：黑色專業版已啟動")
