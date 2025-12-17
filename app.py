import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend
import ta.momentum as momentum
import time

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 專業篩選系統", layout="wide")

# 自定義 CSS (深色模式與圖片風格 UI)
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    .stButton>button { width: 100%; background-color: #ff4b4b; color: white; border-radius: 10px; }
    [data-testid="stSidebar"] { background-color: #1e2130; border-right: 1px solid #333; }
    .stDataFrame { background-color: #1e2130; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 實時策略篩選")

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
# 【策略核心邏輯】
# ==============================================================================
def analyze_stock(ticker, main_strat, filters):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if len(df) < 20: return None
        
        # 指標計算
        df['MA5'] = trend.sma_indicator(df['Close'], window=5)
        df['MA10'] = trend.sma_indicator(df['Close'], window=10)
        df['MA20'] = trend.sma_indicator(df['Close'], window=20)
        df['VMA20'] = trend.sma_indicator(df['Volume'], window=20)
        kd = momentum.StochasticOscillator(df['High'], df['Low'], df['Close'], window=9)
        df['K'], df['D'] = kd.stoch(), kd.stoch_signal()
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        match = False
        
        # 1. 守護生命線 (回測 5MA 不破)
        if main_strat == "🛡️ 守護生命線 (回測/支撐)":
            # 股價低點碰到或接近 5MA，但收盤價站穩 5MA 以上
            at_support = curr['Low'] <= curr['MA5'] * 1.01 
            stay_above = curr['Close'] >= curr['MA5']
            if at_support and stay_above: match = True
            
        # 2. 浴火重生 (假跌破)
        elif main_strat == "🔥 浴火重生 (假跌破)":
            past_5 = df.iloc[-6:-1]
            if any(past_5['Close'] < past_5['MA5']) and curr['Close'] > curr['MA5']:
                match = True
                
        # 3. 高檔飛舞 (多頭排列 + 爆量)
        elif main_strat == "👑 高檔飛舞 (多頭排列)":
            is_bullish = curr['MA5'] > curr['MA10'] > curr['MA20']
            is_high_vol = curr['Volume'] > curr['VMA20'] * 1.2
            if is_bullish and is_high_vol: match = True

        if not match: return None

        # --- 細部過濾 ---
        if filters['kd_cross'] and not (prev['K'] < prev['D'] and curr['K'] > curr['D']): return None
        if filters['vol_up'] and not (curr['Volume'] > prev['Volume'] * 1.5): return None
        if filters['ma_up'] and not (curr['MA5'] > prev['MA5']): return None

        # --- 整理輸出數據 ---
        stock_id = ticker.split('.')[0]
        yahoo_link = f"https://tw.stock.yahoo.com/quote/{ticker}/chart"
        
        return {
            "代號": ticker,
            "現價": round(float(curr['Close']), 2),
            "漲跌幅": f"{round(((curr['Close']/prev['Close'])-1)*100, 2)}%",
            "成交量": int(curr['Volume']),
            "5MA位置": round(float(curr['MA5']), 2),
            "狀態": "回測不破" if curr['Low'] <= curr['MA5'] else "趨勢強勢",
            "Yahoo線圖": yahoo_link
        }
    except: return None

# ==============================================================================
# 【UI 側邊欄佈局】
# ==============================================================================
st.sidebar.header("📂 資料管理")
if st.sidebar.button("🚨 強制更新排行榜清單"):
    st.session_state['ticker_pool'] = fetch_yahoo_rankings()
    st.sidebar.success(f"已更新 {len(st.session_state['ticker_pool'])} 檔標的")

ticker_list = st.session_state.get('ticker_pool', [])

st.sidebar.markdown("---")
st.sidebar.header("🎯 策略設定")
selected_strat = st.sidebar.radio("選擇篩選策略：", 
    ["🛡️ 守護生命線 (回測/支撐)", "🔥 浴火重生 (假跌破)", "👑 高檔飛舞 (多頭排列)"])

st.sidebar.subheader("🔍 細部過濾條件")
filters = {
    "ma_up": st.sidebar.checkbox("5MA 均線方向向上"),
    "kd_cross": st.sidebar.checkbox("KD 黃金交叉 (當日)"),
    "vol_up": st.sidebar.checkbox("成交量 > 昨日 1.5 倍")
}

# ==============================================================================
# 【主畫面：執行篩選】
# ==============================================================================
if st.button("🚀 開始執行全量策略掃描"):
    if not ticker_list:
        st.error("請先點擊左側『強制更新排行榜清單』")
    else:
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, ticker in enumerate(ticker_list):
            progress_bar.progress((i + 1) / len(ticker_list))
            status_text.text(f"正在分析: {ticker}")
            res = analyze_stock(ticker, selected_strat, filters)
            if res: results.append(res)
            
        status_text.empty()
        
        if results:
            st.success(f"掃描完成！符合「{selected_strat}」標的共 {len(results)} 檔")
            
            # 轉換為 DataFrame 並顯示
            df_final = pd.DataFrame(results)
            
            # 使用可點擊連結渲染表格
            st.data_editor(
                df_final,
                column_config={
                    "Yahoo線圖": st.column_config.LinkColumn("點我看線圖", display_text="Open Chart")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning(f"目前盤勢中，查無符合「{selected_strat}」的標的。請嘗試取消部分過濾條件。")

st.sidebar.markdown("---")
st.sidebar.markdown("⏳ 系統運作正常 - 100%")
