import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import time

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 實時策略系統", layout="wide")

# 強制優化 UI 顏色 (確保深色/淺色模式都能看到字)
st.markdown("""
    <style>
    /* 全域文字顏色與背景強化 */
    .stApp { background-color: #0e1117; color: #ffffff; }
    h1, h2, h3, p, span, label { color: #ffffff !important; font-weight: 500 !important; }
    
    /* 側邊欄文字強化 */
    section[data-testid="stSidebar"] { background-color: #1e2130 !important; }
    section[data-testid="stSidebar"] * { color: #ffffff !important; }
    
    /* 表格字體加亮 */
    div[data-testid="stTable"] table { color: #ffffff !important; border: 1px solid #444; }
    div[data-testid="stTable"] th { background-color: #2c313c !important; color: #ff4b4b !important; }
    
    /* 修正勾選清單的文字顏色 */
    div[data-baseweb="select"] * { color: #000000 !important; } /* 下拉選單內部文字用黑色確保清晰 */
    
    .stButton>button { width: 100%; background-color: #ff4b4b; color: white !important; font-weight: bold; border-radius: 8px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 實時策略系統")

# ==============================================================================
# 【核心功能：抓取 Yahoo 排行榜】
# ==============================================================================
@st.cache_data(ttl=600)
def fetch_rankings():
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
            # 修正爬蟲正規表達式，確保抓到 .TW 或 .TWO
            links = soup.find_all('a', href=re.compile(r'/quote/\d{4}\.(TW|TWO)'))
            for link in links:
                m = re.search(r'(\d{4}\.(TW|TWO))', link.get('href'))
                if m: tickers.add(m.group(1))
        except: continue
    return sorted(list(tickers))

# ==============================================================================
# 【策略運算：精確寫入篩選邏輯】
# ==============================================================================
def run_strategy(ticker, mode):
    try:
        # 下載 60 天資料確保均線穩定
        raw_df = yf.download(ticker, period="60d", interval="1d", progress=False, timeout=10)
        if raw_df.empty or len(raw_df) < 25: return None
        
        # 修正 yfinance MultiIndex 問題，確保欄位名稱正確
        df = raw_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 計算均線
        df['MA5'] = trend.sma_indicator(df['Close'], window=5)
        df['MA10'] = trend.sma_indicator(df['Close'], window=10)
        df['MA20'] = trend.sma_indicator(df['Close'], window=20)
        
        curr = df.iloc[-1]   # 今日
        prev = df.iloc[-2]   # 昨日
        prev2 = df.iloc[-3]  # 前日
        
        is_match = False
        
        # 策略 1：守護生命線 (跌破 10MA 但反彈站上 20MA)
        if mode == "🛡️ 守護生命線":
            # 邏輯：今日收盤 > 20MA 且 (今日最低點 < 10MA 或 昨日收盤 < 10MA)
            if curr['Close'] > curr['MA20'] and (curr['Low'] < curr['MA10'] or prev['Close'] < prev['MA10']):
                is_match = True
        
        # 策略 2：高檔飛舞 (突破換手型態)
        elif mode == "👑 高檔飛舞":
            # 邏輯：前一日是爆量黑K (量增1.5倍) 且 今日收盤 > 前日收盤
            vol_spike = prev['Volume'] > (prev2['Volume'] * 1.5)
            is_black_k = prev['Close'] < prev['Open']
            today_rebound = curr['Close'] > prev['Close']
            if vol_spike and is_black_k and today_rebound:
                is_match = True
                
        if not is_match: return None

        return {
            "代號": ticker,
            "收盤價": round(float(curr['Close']), 2),
            "5MA": round(float(curr['MA5']), 2),
            "10MA": round(float(curr['MA10']), 2),
            "20MA": round(float(curr['MA20']), 2),
            "今日成交量": int(curr['Volume']),
            "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{ticker}/chart"
        }
    except Exception as e:
        return None

# ==============================================================================
# 【UI 介面設計】
# ==============================================================================
st.sidebar.markdown("### 📂 系統控制")
if st.sidebar.button("🚨 強制更新排行榜清單"):
    st.session_state['ticker_list'] = fetch_rankings()
    st.sidebar.success(f"已抓取 {len(st.session_state['ticker_list'])} 檔標的")

tickers = st.session_state.get('ticker_list', [])

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 策略選擇")
selected_mode = st.sidebar.radio("請選擇邏輯：", ["🛡️ 守護生命線", "👑 高檔飛舞"])

# 主程式掃描
if st.button("🚀 開始執行全量策略掃描"):
    if not tickers:
        st.error("請先點擊左側『強制更新排行榜清單』")
    else:
        results = []
        bar = st.progress(0)
        status = st.empty()
        
        for i, t in enumerate(tickers):
            bar.progress((i + 1) / len(tickers))
            status.markdown(f"**分析中:** `{t}`")
            data = run_strategy(t, selected_mode)
            if data: results.append(data)
            
        status.empty()
        st.session_state['scan_out'] = results
        if not results:
            st.warning("目前市場無符合此邏輯的標的，請稍後再試或更換策略。")

# 結果顯示與勾選帶出線圖
if 'scan_out' in st.session_state and st.session_state['scan_out']:
    df_res = pd.DataFrame(st.session_state['scan_out'])
    
    st.markdown("### ✅ 符合策略之標的 (請勾選要查看的股票)")
    
    # 使用 multiselect 讓用戶選擇
    selected_items = st.multiselect(
        "選擇要查看詳細價位與線圖的股票：",
        options=df_res['代號'].tolist(),
        default=df_res['代號'].tolist()[:3] if len(df_res) > 3 else df_res['代號'].tolist()
    )
    
    if selected_items:
        display_df = dfRes = df_res[df_res['代號'].isin(selected_items)]
        
        # 帶出收盤價及 5/10/20MA 均線價位
        st.markdown("#### 📊 關鍵均線價位表")
        st.table(display_df[['代號', '收盤價', '5MA', '10MA', '20MA', '今日成交量']])
        
        # 帶出線圖連結
        st.markdown("#### 📈 線圖快速通道")
        cols = st.columns(3)
        for idx, row in display_df.iterrows():
            with cols[idx % 3]:
                st.markdown(f"**[{row['代號']} 技術分析]({row['Yahoo連結']})**")
    else:
        st.info("請從上方下拉選單中勾選股票。")

st.sidebar.markdown("---")
st.sidebar.write("⌛ 系統狀態：正常運作")
