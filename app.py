import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import ta.trend as trend

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 黑色專業版", layout="wide")

# --- UI 樣式優化 (極致黑白對比) ---
st.markdown("""
    <style>
    /* 全域背景與文字 */
    .stApp { background-color: #000000; color: #ffffff; }
    
    /* 強制所有文字為白色 */
    h1, h2, h3, p, span, label, div, li { color: #ffffff !important; }
    
    /* 側邊欄：確保不會消失，增加右邊框 */
    section[data-testid="stSidebar"] { 
        background-color: #111111 !important; 
        border-right: 2px solid #333333 !important;
        min-width: 300px !important;
    }
    
    /* 側邊欄內的輸入項標籤顏色 */
    section[data-testid="stSidebar"] label p {
        color: #ffffff !important;
        font-weight: bold !important;
    }

    /* 按鈕樣式：亮紅色更醒目 */
    .stButton>button { 
        width: 100%; 
        background-color: #ff4b4b; 
        color: white !important; 
        font-weight: bold; 
        border-radius: 5px;
        height: 3em;
    }

    /* 表格樣式優化 */
    div[data-testid="stTable"] table { 
        color: #ffffff !important; 
        background-color: #111111;
        border: 1px solid #444; 
    }
    div[data-testid="stTable"] th { 
        background-color: #222222 !important; 
        color: #00d1ff !important; 
        border: 1px solid #444;
    }
    div[data-testid="stTable"] td { 
        border: 1px solid #444; 
    }

    /* 修正下拉選單勾選後的文字顏色 (避免反白看不見) */
    div[data-baseweb="select"] * { color: #000000 !important; }
    div[role="listbox"] * { color: #000000 !important; }
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
# 【策略運算邏輯】
# ==============================================================================
def analyze_strategy(ticker, mode):
    try:
        # 判斷時間維度
        interval = "5m" if "5分k" in mode.lower() else "1d"
        period = "5d" if interval == "5m" else "60d"
        
        df = yf.download(ticker, period=period, interval=interval, progress=False, timeout=10)
        if df.empty or len(df) < 25: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. 日線：盤整突破 5MA
        if mode == "🚀 日線盤整突破 5MA":
            df['MA5'] = trend.sma_indicator(df['Close'], window=5)
            recent = df.iloc[-11:-1]
            price_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
            # 盤整幅度 < 5% 且 今日收盤突破區間最高點與 MA5
            if price_range < 0.05 and curr['Close'] > recent['High'].max() and curr['Close'] > df['MA5'].iloc[-1]:
                return curr, df['MA5'].iloc[-1], trend.sma_indicator(df['Close'], 10).iloc[-1], trend.sma_indicator(df['Close'], 20).iloc[-1]

        # 2. 5分k：爆量突破 20MA
        elif mode == "⚡ 5分k爆量突破 20MA":
            df['MA20_5m'] = trend.sma_indicator(df['Close'], window=20)
            if curr['Volume'] > (prev['Volume'] * 2) and prev['Close'] < df['MA20_5m'].iloc[-2] and curr['Close'] > df['MA20_5m'].iloc[-1]:
                return curr, curr['Close'], curr['Close'], df['MA20_5m'].iloc[-1]
        
        # 3. 守護生命線：跌破 10MA 但反彈站上 20MA
        elif mode == "🛡️ 守護生命線":
            ma10 = trend.sma_indicator(df['Close'], 10)
            ma20 = trend.sma_indicator(df['Close'], 20)
            if curr['Close'] > ma20.iloc[-1] and (curr['Low'] < ma10.iloc[-1] or prev['Close'] < ma10.iloc[-2]):
                return curr, trend.sma_indicator(df['Close'], 5).iloc[-1], ma10.iloc[-1], ma20.iloc[-1]

        # 4. 高檔飛舞：前日爆量黑K突破換手
        elif mode == "👑 高檔飛舞":
            prev2 = df.iloc[-3]
            vol_spike = prev['Volume'] > (prev2['Volume'] * 1.5)
            is_black_k = prev['Close'] < prev['Open']
            if vol_spike and is_black_k and curr['Close'] > prev['Close']:
                return curr, trend.sma_indicator(df['Close'], 5).iloc[-1], trend.sma_indicator(df['Close'], 10).iloc[-1], trend.sma_indicator(df['Close'], 20).iloc[-1]

        return None
    except: return None

# ==============================================================================
# 【Sidebar 側邊欄控制】
# ==============================================================================
with st.sidebar:
    st.markdown("## 📂 系統控制")
    if st.button("🚨 強制更新排行榜"):
        st.session_state['ticker_pool'] = fetch_hot_list()
        st.success(f"已獲取 {len(st.session_state['ticker_pool'])} 檔標的")
    
    ticker_pool = st.session_state.get('ticker_pool', [])
    
    st.markdown("---")
    st.markdown("## 🎯 策略選擇")
    selected_mode = st.radio("選擇邏輯：", 
        ["🚀 日線盤整突破 5MA", "⚡ 5分k爆量突破 20MA", "🛡️ 守護生命線", "👑 高檔飛舞"])
    
    st.markdown("---")
    st.write("⌛ 系統狀態：黑色專業版")

# ==============================================================================
# 【Main 主頁面】
# ==============================================================================
if st.button("🔴 開始執行全量掃描"):
    if not ticker_pool:
        st.error("請點擊左側『強制更新排行榜』")
    else:
        results = []
        bar = st.progress(0)
        status = st.empty()
        
        for i, t in enumerate(ticker_pool):
            bar.progress((i + 1) / len(ticker_pool))
            status.markdown(f"**分析中:** `{t}`")
            data = analyze_strategy(t, selected_mode)
            if data:
                c, m5, m10, m20 = data
                results.append({
                    "代號": t, "現價": round(float(c['Close']), 2),
                    "5MA": round(float(m5), 2), "10MA": round(float(m10), 2), "20MA": round(float(m20), 2),
                    "成交量": int(c['Volume']), "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{t}/chart"
                })
        
        status.empty()
        st.session_state['results'] = results
        if not results:
            st.warning("查無符合標的。")

# --- 結果呈現 ---
if 'results' in st.session_state and st.session_state['results']:
    df_res = pd.DataFrame(st.session_state['results'])
    
    st.markdown("### ✅ 符合標的 (請勾選標的以顯示線圖)")
    selected = st.multiselect("選擇標的：", options=df_res['代號'].tolist(), default=df_res['代號'].tolist()[:3])
    
    if selected:
        final_df = df_res[df_res['代號'].isin(selected)]
        st.table(final_df[['代號', '現價', '5MA', '10MA', '20MA', '成交量']])
        
        st.markdown("#### 📈 線圖快速通道")
        cols = st.columns(3)
        for idx, row in final_df.reset_index().iterrows():
            with cols[idx % 3]:
                st.markdown(f"🔗 **[{row['代號']} 技術分析]({row['Yahoo連結']})**")
