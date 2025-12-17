import streamlit as st
import yfinance as yf
import pandas as pd
import re
import ta.trend as trend

# --- 頁面設定 ---
st.set_page_config(page_title="量化投生命 - API 原始數據版", layout="wide")

# --- UI 樣式設定 (極黑底白字) ---
st.markdown("""
    <style>
    .stApp { background-color: #000000; color: #ffffff; }
    h1, h2, h3, p, span, label, div, li { color: #ffffff !important; }
    section[data-testid="stSidebar"] { 
        background-color: #111111 !important; 
        border-right: 2px solid #333333 !important;
        min-width: 320px !important;
    }
    .stButton>button { 
        width: 100%; background-color: #ff4b4b; color: white !important; 
        font-weight: bold; border-radius: 8px; height: 3.5em; border: none;
    }
    div[data-testid="stTable"] table { color: #ffffff !important; background-color: #000000; border: 1px solid #444; }
    div[data-testid="stTable"] th { background-color: #222222 !important; color: #00d1ff !important; border: 1px solid #444; }
    div[data-testid="stTable"] td { border: 1px solid #444; text-align: center !important; }
    div[data-baseweb="select"] * { color: #ffffff !important; background-color: #222222 !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ 量化投生命 - 原始數據監控系統")

# ==============================================================================
# 【核心：API 策略分析邏輯】
# ==============================================================================
def analyze_stock(ticker, mode):
    try:
        # 根據策略決定 K 線週期
        is_5m = "5分k" in mode
        df = yf.download(ticker, period="60d" if not is_5m else "5d", 
                         interval="1d" if not is_5m else "5m", progress=False)
        
        if df.empty or len(df) < 25: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 原始數值直帶
        price = round(float(curr['Close']), 2)
        raw_volume = int(curr['Volume']) 
        
        # 均線計算
        m5 = round(float(trend.sma_indicator(df['Close'], 5).iloc[-1]), 2)
        m10 = round(float(trend.sma_indicator(df['Close'], 10).iloc[-1]), 2)
        m20 = round(float(trend.sma_indicator(df['Close'], 20).iloc[-1]), 2)

        match = False
        if mode == "全部顯示":
            match = True
        elif mode == "🚀 日線盤整突破 5MA":
            # 盤整定義：近 10 日高低差 < 5%
            recent = df.iloc[-11:-1]
            price_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
            if price_range < 0.05 and price > recent['High'].max() and price > m5: match = True
        elif mode == "⚡ 5分k爆量突破 20MA":
            # 爆量定義：當前 5m 量 > 前一根 2 倍，且收盤站上 20MA
            if curr['Volume'] > (prev['Volume'] * 2) and prev['Close'] < m20 and price > m20: match = True
        elif mode == "🛡️ 守護生命線":
            if price > m20 and (curr['Low'] < m10 or prev['Close'] < m10): match = True
            
        if not match: return None

        return {
            "代號": ticker, "最新價": price, "5MA": m5, "10MA": m10, "20MA": m20,
            "原始成交量": raw_volume,
            "Yahoo連結": f"https://tw.stock.yahoo.com/quote/{ticker}/technical-analysis"
        }
    except: return None

# ==============================================================================
# 【側邊欄：Excel 解析】
# ==============================================================================
with st.sidebar:
    st.markdown("### 📂 名單上傳")
    uploaded_file = st.file_uploader("請上傳股票 Excel", type=["xlsx", "csv", "xls"])
    
    if uploaded_file:
        df_input = pd.read_excel(uploaded_file) if not uploaded_file.name.endswith('.csv') else pd.read_csv(uploaded_file)
        raw_codes = df_input.iloc[:, 0].astype(str).tolist()
        ticker_pool = []
        for c in raw_codes:
            m = re.search(r'(\d{4})', c)
            if m:
                # 這裡統一補上 .TW，若要精細區分上櫃可增加 .TWO 判斷
                ticker_pool.append(f"{m.group(1)}.TW")
        st.session_state['tickers'] = ticker_pool
        st.success(f"✅ 已讀取 {len(ticker_pool)} 檔標的")

    st.markdown("---")
    strategy = st.radio("篩選策略：", ["全部顯示", "🚀 日線盤整突破 5MA", "⚡ 5分k爆量突破 20MA", "🛡️ 守護生命線"])

# ==============================================================================
# 【主畫面：執行分析】
# ==============================================================================
if st.button("🔴 開始全量 API 數據掃描"):
    if 'tickers' not in st.session_state:
        st.error("請先在左側上傳 Excel 檔案！")
    else:
        results = []
        p_bar = st.progress(0)
        status_msg = st.empty()
        pool = st.session_state['tickers']
        
        for i, t in enumerate(pool):
            p_bar.progress((i + 1) / len(pool))
            status_msg.markdown(f"🔍 API 同步中: `{t}`")
            res = analyze_stock(t, strategy)
            if res: results.append(res)
            
        status_msg.empty()
        st.session_state['final_results'] = results

if 'final_results' in st.session_state and st.session_state['final_results']:
    df_res = pd.DataFrame(st.session_state['final_results'])
    st.markdown("### 📊 分析結果")
    selected = st.multiselect("勾選查看詳細價位與連結：", options=df_res['代號'].tolist(), default=df_res['代號'].tolist()[:10])
    
    if selected:
        display_df = df_res[df_res['代號'].isin(selected)]
        st.table(display_df[['代號', '最新價', '5MA', '10MA', '20MA', '原始成交量']])
        
        st.markdown("#### 📈 技術分析快速通道")
        cols = st.columns(3)
        for idx, row in display_df.reset_index().iterrows():
            with cols[idx % 3]:
                st.markdown(f"🔗 **[{row['代號']} K線圖]({row['Yahoo連結']})**")
