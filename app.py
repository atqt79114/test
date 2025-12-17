import streamlit as st
import yfinance as yf
import pandas as pd
import re
import ta.trend as trend

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 原始數據版", layout="wide")

# --- UI 樣式 (黑底白字、強化側邊欄) ---
st.markdown("""
    <style>
    .stApp { background-color: #000000; color: #ffffff; }
    h1, h2, h3, p, span, label, div, li { color: #ffffff !important; }
    
    /* 側邊欄強化 */
    section[data-testid="stSidebar"] { 
        background-color: #111111 !important; 
        border-right: 2px solid #333333 !important;
        min-width: 300px !important;
    }

    /* 按鈕樣式 */
    .stButton>button { 
        width: 100%; background-color: #ff4b4b; color: white !important; 
        font-weight: bold; border-radius: 5px; height: 3.5em; border: none;
    }

    /* 表格樣式 */
    div[data-testid="stTable"] table { color: #ffffff !important; background-color: #000000; border: 1px solid #444; }
    div[data-testid="stTable"] th { background-color: #222222 !important; color: #00d1ff !important; border: 1px solid #444; }
    div[data-testid="stTable"] td { border: 1px solid #444; }
    
    /* 下拉選單修正 */
    div[data-baseweb="select"] * { color: #ffffff !important; background-color: #222222 !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 原始數據即時監控")

# ==============================================================================
# 【核心：API 數據分析 - 原始數字直帶】
# ==============================================================================
def analyze_stock(ticker, mode):
    try:
        interval = "5m" if "5分k" in mode else "1d"
        period = "5d" if interval == "5m" else "60d"
        
        # 直接抓取 API 資料
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty or len(df) < 20: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- 數據直帶 (不進行任何換算) ---
        price = round(float(curr['Close']), 2)
        raw_volume = int(curr['Volume'])  # API 抓到什麼就帶什麼
        
        m5 = round(float(trend.sma_indicator(df['Close'], 5).iloc[-1]), 2)
        m10 = round(float(trend.sma_indicator(df['Close'], 10).iloc[-1]), 2)
        m20 = round(float(trend.sma_indicator(df['Close'], 20).iloc[-1]), 2)

        # 策略過濾邏輯
        match = False
        if mode == "🛡️ 守護生命線":
            if price > m20 and (curr['Low'] < m10 or prev['Close'] < m10): match = True
        elif mode == "🚀 日線盤整突破 5MA":
            recent = df.iloc[-11:-1]
            price_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
            if price_range < 0.05 and price > recent['High'].max() and price > m5: match = True
        elif mode == "⚡ 5分k爆量突破 20MA":
            if curr['Volume'] > (prev['Volume'] * 2) and prev['Close'] < m20 and price > m20: match = True
            
        if not match: return None

        return {
            "代號": ticker, "現價": price,
            "5MA": m5, "10MA": m10, "20MA": m20,
            "原始成交量": raw_volume,
            "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{ticker}/technical-analysis"
        }
    except: return None

# ==============================================================================
# 【側邊欄：處理 Excel】
# ==============================================================================
with st.sidebar:
    st.markdown("### 📂 觀察名單上傳")
    uploaded_file = st.file_uploader("請上傳股票 Excel", type=["xlsx", "csv"])
    
    if uploaded_file:
        df_input = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
        raw_codes = df_input.iloc[:, 0].astype(str).tolist()
        ticker_pool = []
        for c in raw_codes:
            m = re.search(r'(\d{4})', c)
            if m: ticker_pool.append(f"{m.group(1)}.TW")
        st.session_state['tickers'] = ticker_pool
        st.success(f"已載入 {len(ticker_pool)} 檔標的")

    st.markdown("---")
    strategy = st.radio("選擇策略：", ["🛡️ 守護生命線", "🚀 日線盤整突破 5MA", "⚡ 5分k爆量突破 20MA"])

# ==============================================================================
# 【主畫面：執行與顯示】
# ==============================================================================
if st.button("🔴 開始執行全量 API 掃描"):
    if 'tickers' not in st.session_state:
        st.error("請先上傳 Excel 檔案！")
    else:
        results = []
        p_bar = st.progress(0)
        pool = st.session_state['tickers']
        
        for i, t in enumerate(pool):
            p_bar.progress((i + 1) / len(pool))
            res = analyze_stock(t, strategy)
            if res: results.append(res)
            
        st.session_state['final_res'] = results

if 'final_res' in st.session_state and st.session_state['final_res']:
    df_res = pd.DataFrame(st.session_state['final_res'])
    st.markdown("### ✅ 篩選結果")
    selected = st.multiselect("勾選查看明細：", options=df_res['代號'].tolist(), default=df_res['代號'].tolist()[:5])
    
    if selected:
        display_df = df_res[df_res['代號'].isin(selected)]
        # 顯示原始數據：包含 API 的原始成交量
        st.table(display_df[['代號', '現價', '5MA', '10MA', '20MA', '原始成交量']])
        
        st.markdown("#### 📈 線圖快速通道")
        for idx, row in display_df.iterrows():
            st.markdown(f"🔗 **[{row['代號']} 技術分析連結]({row['Yahoo連結']})**")
