import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 策略篩選系統", layout="wide")

# 強化文字可見度與 UI 顏色
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    [data-testid="stSidebar"] { background-color: #1e2130; border-right: 1px solid #333; }
    [data-testid="stSidebar"] .stMarkdown p { color: #ffffff !important; font-size: 16px; }
    .stButton>button { width: 100%; background-color: #ff4b4b; color: white !important; font-weight: bold; }
    .stDataFrame, .stTable { background-color: #1e2130; color: #ffffff !important; }
    /* 修正表格文字顏色 */
    div[data-testid="stTable"] th { color: #ff4b4b !important; }
    div[data-testid="stTable"] td { color: #ffffff !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 實時策略系統")

# ==============================================================================
# 【核心功能：資料抓取】
# ==============================================================================
@st.cache_data(ttl=600)
def fetch_yahoo_rankings():
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
# 【策略核心邏輯：寫入精確條件】
# ==============================================================================
def analyze_stock(ticker, main_strat):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if len(df) < 25: return None
        
        # 指標計算
        df['MA5'] = trend.sma_indicator(df['Close'], window=5)
        df['MA10'] = trend.sma_indicator(df['Close'], window=10)
        df['MA20'] = trend.sma_indicator(df['Close'], window=20)
        
        curr = df.iloc[-1]   # 今日
        prev = df.iloc[-2]   # 昨日
        prev2 = df.iloc[-3]  # 前日
        
        match = False
        
        # 1. 守護生命線：跌破 10MA 但反彈站上 20MA
        if main_strat == "🛡️ 守護生命線":
            # 條件：今日收盤在 20MA 之上，且今日最低點或昨日收盤曾跌破 10MA
            is_above_20 = curr['Close'] > curr['MA20']
            had_broken_10 = curr['Low'] < curr['MA10'] or prev['Close'] < prev['MA10']
            if is_above_20 and had_broken_10:
                match = True
            
        # 2. 高檔飛舞：前日爆量黑K + 今日換手
        elif main_strat == "👑 高檔飛舞":
            # 條件：昨日為黑K（收<開）且 昨日量 > 前日量 * 1.5
            is_black_k = prev['Close'] < prev['Open']
            vol_spike = prev['Volume'] > (prev2['Volume'] * 1.5)
            # 今日站穩昨日高點或呈現收紅突破
            today_stable = curr['Close'] > prev['Close']
            if is_black_k and vol_spike and today_stable:
                match = True

        if not match: return None

        return {
            "股票代號": ticker,
            "收盤價": round(float(curr['Close']), 2),
            "5MA": round(float(curr['MA5']), 2),
            "10MA": round(float(curr['MA10']), 2),
            "20MA": round(float(curr['MA20']), 2),
            "昨日量增": f"{round(prev['Volume']/prev2['Volume'], 2)}倍",
            "Yahoo線圖": f"https://tw.stock.yahoo.com/quote/{ticker}/chart"
        }
    except: return None

# ==============================================================================
# 【UI 介面設計】
# ==============================================================================
# 側邊欄
st.sidebar.markdown("### 📂 資料庫管理")
if st.sidebar.button("🚨 強制更新排行榜清單"):
    st.session_state['ticker_pool'] = fetch_yahoo_rankings()
    st.sidebar.success(f"已獲取 {len(st.session_state['ticker_pool'])} 檔標的")

ticker_pool = st.session_state.get('ticker_pool', [])

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 策略篩選")
selected_strat = st.sidebar.radio("請選擇邏輯：", ["🛡️ 守護生命線", "👑 高檔飛舞"])

# 主畫面
if st.button("🚀 開始執行全量策略掃描"):
    if not ticker_pool:
        st.error("請先更新排行榜清單")
    else:
        results = []
        pbar = st.progress(0)
        status = st.empty()
        
        for i, t in enumerate(ticker_pool):
            pbar.progress((i + 1) / len(ticker_pool))
            status.text(f"分析中: {t}")
            res = analyze_stock(t, selected_strat)
            if res: results.append(res)
        
        status.empty()
        st.session_state['scan_results'] = results
        if not results:
            st.warning("目前市場無符合此邏輯的標的。")

# 顯示與勾選
if 'scan_results' in st.session_state and st.session_state['scan_results']:
    df = pd.DataFrame(st.session_state['scan_results'])
    
    st.subheader("✅ 勾選標的以查看詳細均線價位")
    selected_tickers = st.multiselect("可多選：", options=df['股票代號'].tolist(), default=df['股票代號'].tolist()[:5])
    
    if selected_tickers:
        selected_df = df[df['股票代號'].isin(selected_tickers)]
        # 顯示詳細均線表
        st.table(selected_df[['股票代號', '收盤價', '5MA', '10MA', '20MA', '昨日量增']])
        
        # 線圖連結
        for _, row in selected_df.iterrows():
            st.markdown(f"🔗 [{row['股票代號']} 技術分析線圖]({row['Yahoo線圖']})")

st.sidebar.markdown("---")
st.sidebar.markdown("⌛ **系統運作正常 - 100%**")
