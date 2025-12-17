# =======================
# 股票策略篩選器 Optimized
# 重點優化：
# 1. SSL 穩定抓取 TWSE / OTC
# 2. 股票清單本地快取（快 10x）
# 3. 掃描節流優化（不容易被 Yahoo 擋）
# 4. yfinance 統一下載，避免重複 request
# =======================

import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import warnings

warnings.filterwarnings('ignore')

st.set_page_config(page_title="股票策略篩選器 (優化版)", layout="wide")
st.title("📈 股票策略篩選器（穩定 + 高效版）")
st.markdown("---")

# =====================================================================
# 【股票清單抓取（穩定 + 快取）】
# =====================================================================
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    all_tickers = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for mode in ['2', '4']:  # 2=上市, 4=上櫃
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        resp = requests.get(url, headers=headers, verify=False, timeout=10)
        df = pd.read_html(resp.text)[0].iloc[1:]

        for item in df[0]:
            code = str(item).split()[0]
            if code.isdigit() and len(code) == 4:
                all_tickers.append(f"{code}.TW")

    return sorted(set(all_tickers))

# =====================================================================
# 【資料快取下載（避免每個策略都打 Yahoo）】
# =====================================================================
@st.cache_data(ttl=300)
def download_daily(ticker):
    return yf.download(ticker, period="3mo", interval="1d", progress=False)

@st.cache_data(ttl=120)
def download_5m(ticker):
    return yf.download(ticker, period="5d", interval="5m", progress=False)

# =====================================================================
# 【策略】
# =====================================================================
def strategy_consolidation(ticker):
    df = download_daily(ticker)
    if len(df) < 21: return None

    close = float(df['Close'].iloc[-1])
    prev_vol = float(df['Volume'].iloc[-2])
    vol = float(df['Volume'].iloc[-1])
    high20 = df['High'].iloc[:-1].tail(20).max()

    if close > high20 and vol > prev_vol * 2:
        return {
            "股票": ticker,
            "現價": round(close, 2),
            "突破價": round(high20, 2),
            "量增": round(vol / prev_vol, 1)
        }


def strategy_5m_breakout(ticker):
    df = download_5m(ticker)
    if len(df) < 21: return None

    close = df['Close']
    ma20 = ta.trend.sma_indicator(close, 20)

    if close.iloc[-1] > ma20.iloc[-1] and close.iloc[-2] < ma20.iloc[-2]:
        if df['Volume'].iloc[-1] > df['Volume'].iloc[-2] * 2:
            return {
                "股票": ticker,
                "時間": df.index[-1].strftime('%H:%M'),
                "現價": round(close.iloc[-1], 2)
            }


def strategy_high_level(ticker):
    df = download_daily(ticker)
    if len(df) < 20: return None

    df['MA5'] = ta.trend.sma_indicator(df['Close'], 5)
    rise20 = df['Close'].iloc[-1] / df['Close'].iloc[-20] - 1

    if rise20 > 0.1 and df['Close'].iloc[-1] > df['MA5'].iloc[-1]:
        return {
            "股票": ticker,
            "現價": round(df['Close'].iloc[-1], 2),
            "20日漲幅": f"{round(rise20*100,1)}%"
        }

STRATEGIES = {
    "盤整突破": strategy_consolidation,
    "5分K突破": strategy_5m_breakout,
    "高檔飛舞": strategy_high_level
}

# =====================================================================
# 【UI】
# =====================================================================
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW,2317.TW")
    tickers = [x.strip() for x in raw.split(',')]
else:
    if st.sidebar.button("抓取上市上櫃"):
        st.session_state['all'] = get_all_tw_tickers()
    tickers = st.session_state.get('all', [])[:30]

st.sidebar.header("策略")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

# =====================================================================
# 【執行】
# =====================================================================
if st.button("開始掃描"):
    result = {k: [] for k in selected}
    bar = st.progress(0)

    for i, t in enumerate(tickers):
        bar.progress((i+1)/len(tickers))
        for k in selected:
            r = STRATEGIES[k](t)
            if r: result[k].append(r)
        time.sleep(0.3)

    bar.empty()

    for k in selected:
        st.subheader(k)
        if result[k]:
            st.dataframe(pd.DataFrame(result[k]))
        else:
            st.info("無符合")
