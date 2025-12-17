import streamlit as st
import yfinance as yf
import pandas as pd
import re
import ta.trend as trend

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - 原始數據監控", layout="wide")

# --- UI 樣式 (極致黑底白字) ---
st.markdown("""
    <style>
    .stApp { background-color: #000000; color: #ffffff; }
    h1, h2, h3, p, span, label, div, li { color: #ffffff !important; }
    
    /* 側邊欄樣式 */
    section[data-testid="stSidebar"] { 
        background-color: #111111 !important; 
        border-right: 2px solid #333333 !important;
        min-width: 320px !important;
    }

    /* 亮紅色執行按鈕 */
    .stButton>button { 
        width: 100%; background-color: #ff4b4b; color: white !important; 
        font-weight: bold; border-radius: 8px; height: 3.5em; border: none;
    }

    /* 表格樣式 (全黑背景 + 亮藍色表頭) */
    div[data-testid="stTable"] table { color: #ffffff !important; background-color: #000000; border: 1px solid #444; }
    div[data-testid="stTable"] th { background-color: #222222 !important; color: #00d1ff !important; border: 1px solid #444; }
    div[data-testid="stTable"] td { border: 1px solid #444; text-align: center !important; }
    
    /* 修正下拉選單顯示 */
    div[data-baseweb="select"] * { color: #ffffff !important; background-color: #222222 !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 觀察名單即時分析")

# ==============================================================================
# 【數據核心：API 原始數值直帶】
# ==============================================================================
def analyze_stock(ticker, mode):
    try:
        interval = "5m" if "5分k" in mode else "1d"
        period = "5d" if interval == "5m" else "60d"
        
        # 下載 API 原始數據
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty or len(df) < 20: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- 數據直帶 (不做任何 /1000 換算) ---
        price = round(float(curr['Close']), 2)
        raw_volume = int(curr['Volume'])  # 原始成交股數
        
        m5 = round(float(trend.sma_indicator(df['Close'], 5).iloc[-1]), 2)
        m10 = round(float(trend.sma_indicator(df['Close'], 10).iloc[-1]), 2)
        m20 = round(float(trend.sma_indicator(df['Close'], 20).iloc[-1]), 2)

        # 策略過濾
        match = False
        if mode == "顯示清單所有標的":
            match = True
        elif mode == "🛡️ 守護生命線":
            if price > m20 and (curr['Low'] < m10 or prev['Close'] < m10): match = True
        elif mode == "🚀 日線盤整突破 5MA":
            recent = df.iloc[-11:-1]
            price_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
            if price_range < 0.05 and price > recent['High'].max() and price > m5: match = True
            
        if not match: return None

        return {
            "代號": ticker, "最新價": price,
            "5MA": m5, "10MA": m10, "20MA": m20,
            "原始成交量": raw_volume,
            "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{ticker}/technical-analysis"
        }
    except: return None

# ==============================================================================
# 【側邊欄：解析上傳的 Excel】
# ==============================================================================
with st.sidebar:
    st.markdown("### 📂 觀察名單上傳")
    uploaded_file = st.file_uploader("選擇您的 Excel 檔案", type=["xlsx", "csv"])
    
    if uploaded_file:
        # 讀取 Excel 或 CSV
        if uploaded_file.name.endswith('.xlsx'):
            df_input = pd.read_excel(uploaded_file)
        else:
            df_input = pd.read_csv(uploaded_file)
            
        # 提取代號邏輯：從第一欄提取前 4 位數字
        raw_codes = df_input.iloc[:, 0].astype(str).tolist()
        ticker_pool = []
        for c in raw_codes:
            m = re.search(r'(\d{4})', c)
            if m: ticker_pool.append(f"{m.group(1)}.TW")
            
        st.session_state['tickers'] = ticker_pool
        st.success(f"✅ 成功載入 {len(ticker_pool)} 檔標的")

    st.markdown("---")
    strategy = st.radio("篩選模式：", ["顯示清單所有標的", "🛡️ 守護生命線", "🚀 日線盤整突破 5MA"])

# ==============================================================================
# 【主畫面：執行分析】
# ==============================================================================
if st.button("🔴 啟動 API 數據同步分析"):
    if 'tickers' not in st.session_state:
        st.error("請先在左側上傳 Excel 觀察名單！")
    else:
        results = []
        p_bar = st.progress(0)
        status_msg = st.empty()
        pool = st.session_state['tickers']
        
        for i, t in enumerate(pool):
            p_bar.progress((i + 1) / len(pool))
            status_msg.markdown(f"🔍 掃描中: `{t}`")
            res = analyze_stock(t, strategy)
            if res: results.append(res)
            
        status_msg.empty()
        st.session_state['final_data'] = results

if 'final_data' in st.session_state and st.session_state['final_data']:
    df_res = pd.DataFrame(st.session_state['final_data'])
    st.markdown("### 📊 實時行情與策略結果")
    
    # 勾選功能
    selected = st.multiselect("勾選欲查看明細標的：", options=df_res['代號'].tolist(), default=df_res['代號'].tolist()[:5])
    
    if selected:
        display_df = df_res[df_res['代號'].isin(selected)]
        # 顯示原始數據：成交量完全不做換算
        st.table(display_df[['代號', '最新價', '5MA', '10MA', '20MA', '原始成交量']])
        
        st.markdown("#### 📈 線圖快速連動 (圖一超連結)")
        cols = st.columns(3)
        for idx, row in display_df.reset_index().iterrows():
            with cols[idx % 3]:
                st.markdown(f"🔗 **[{row['代號']} 技術分析]({row['Yahoo連結']})**")
