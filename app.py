import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import ta.momentum as momentum
import time
import random
from datetime import datetime, timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 策略篩選器", layout="wide")

# 套用簡易自定義 CSS 模擬圖片 UI
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: white; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #ff4b4b; color: white; }
    .reportview-container .sidebar-content { background-color: #262730; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 實時策略篩選系統")

# ==============================================================================
# 【資料抓取與技術指標計算】
# ==============================================================================
@st.cache_data(ttl=600)
def get_yahoo_multi_rank_tickers():
    tickers = set()
    rank_urls = [
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/volume?exchange=TWO",
        "https://tw.stock.yahoo.com/rank/volume?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TAI",
        "https://tw.stock.yahoo.com/rank/foreign-investor-sell?exchange=TWO"
    ]
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in rank_urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))
            for link in links:
                match = re.search(r'(\d{4}\.(TW|TWO))', link.get('href'))
                if match: tickers.add(match.group(1))
        except: continue
    return sorted(list(tickers))

def get_indicators(df):
    # 均線
    df['MA5'] = trend.sma_indicator(df['Close'], window=5)
    df['MA10'] = trend.sma_indicator(df['Close'], window=10)
    df['MA20'] = trend.sma_indicator(df['Close'], window=20)
    # KD
    kd = momentum.StochasticOscillator(df['High'], df['Low'], df['Close'], window=9, smooth_window=3)
    df['K'] = kd.stoch()
    df['D'] = kd.stoch_signal()
    return df

# ==============================================================================
# 【策略核心邏輯】
# ==============================================================================

def check_strategy(ticker, strategy_name, filters):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if len(df) < 30: return None
        df = get_indicators(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        match_strat = False
        
        # 1. 假跌破策略：7日內曾跌破 5MA 且今日站回
        if strategy_name == "浴火重生 (假跌破)":
            past_7 = df.iloc[-8:-1]
            had_broken = any(past_7['Close'] < past_7['MA5'])
            currently_above = curr['Close'] > curr['MA5']
            if had_broken and currently_above: match_strat = True

        # 2. 高檔飛舞：多頭排列 + 爆量黑K
        elif strategy_name == "高檔飛舞":
            is_bullish = curr['MA5'] > curr['MA10'] > curr['MA20']
            is_black_k = curr['Close'] < curr['Open']
            if is_bullish and is_black_k: match_strat = True

        # 3. 均線排列策略 (皇冠特選)
        elif strategy_name == "皇冠特選 (多頭排列)":
            if curr['MA5'] > curr['MA10'] > curr['MA20']: match_strat = True

        if not match_strat: return None

        # --- 細部條件過濾 ---
        if filters['kd_cross'] and not (prev['K'] < prev['D'] and curr['K'] > curr['D']): return None
        if filters['vol_up'] and not (curr['Volume'] > prev['Volume'] * 1.5): return None
        if filters['ma_up'] and not (curr['MA5'] > prev['MA5']): return None
        if filters['ma_down'] and not (curr['MA5'] < prev['MA5']): return None

        return {
            "代號": ticker,
            "今日收盤": round(float(curr['Close']), 2),
            "漲跌幅": f"{round(((curr['Close']/prev['Close'])-1)*100, 2)}%",
            "成交量": int(curr['Volume']),
            "K/D": f"{round(float(curr['K']),1)}/{round(float(curr['D']),1)}",
            "均線狀態": "向上" if curr['MA5'] > prev['MA5'] else "向下"
        }
    except: return None

# ==============================================================================
# 【UI 側邊欄設定】
# ==============================================================================
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/2583/2583118.png", width=100)
st.sidebar.header("2. 即時篩選器")

min_vol = st.sidebar.number_input("最低成交量 (張)", value=1000)
source_option = st.sidebar.selectbox("股票來源", ["自動抓取排行榜", "手動輸入"])

st.sidebar.markdown("### 策略選擇")
selected_strategy = st.sidebar.radio("選擇主要策略：", 
    ["高檔飛舞", "浴火重生 (假跌破)", "皇冠特選 (多頭排列)"])

st.sidebar.markdown("### 細部條件")
filters = {
    "ma_up": st.sidebar.checkbox("均線向上 (5MA > 昨日)"),
    "ma_down": st.sidebar.checkbox("均線向下 (5MA < 昨日)"),
    "kd_cross": st.sidebar.checkbox("KD 黃金交叉"),
    "vol_up": st.sidebar.checkbox("出量 (今日 > 昨日 x1.5)")
}

if source_option == "自動抓取排行榜":
    if st.sidebar.button("🔄 更新股價資料 (開市請按我)"):
        st.session_state['tickers'] = get_yahoo_multi_rank_tickers()
    tickers = st.session_state.get('tickers', [])
else:
    t_in = st.sidebar.text_area("代號", "2330.TW, 2317.TW")
    tickers = [x.strip() for x in t_in.split(",")]

# ==============================================================================
# 【主畫面執行與回測數據】
# ==============================================================================
col1, col2 = st.columns([2, 1])

with col1:
    if st.button("🔍 開始全量掃描策略"):
        if not tickers: st.error("清單為空，請先點擊更新按鈕")
        else:
            results = []
            pbar = st.progress(0)
            for i, t in enumerate(tickers):
                pbar.progress((i+1)/len(tickers))
                res = check_strategy(t, selected_strategy, filters)
                if res: results.append(res)
            
            st.subheader(f"📊 {selected_strategy} - 篩選結果")
            if results: st.dataframe(pd.DataFrame(results), use_container_width=True)
            else: st.info("查無符合條件之標的")

with col2:
    st.markdown("### 📜 歷史驗證數據 (模擬)")
    # 模擬圖片中的回測 UI
    st.metric("09月 獲利機率", "96%", "10.16%")
    st.metric("結算次數", "117 次")
    
    mock_data = {
        "月份": ["09月"]*5,
        "代號": ["1314", "1316", "1340", "1712", "1795"],
        "名稱": ["中石化", "上曜", "勝悅-KY", "興農", "美時"],
        "損益": ["+12.5%", "+8.2%", "-2.1%", "+15.3%", "+5.4%"]
    }
    st.table(pd.DataFrame(mock_data))

st.sidebar.markdown("---")
st.sidebar.write("系統正在努力挖掘寶藏中... (100%)")
st.sidebar.progress(100)
