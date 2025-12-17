# =======================
# 股票策略篩選器（整合可用最終版）
# - 修正 pandas ValueError
# - 防洗版（今日只顯示一次）
# - 排除低成交量冷門股
# - 盤中 / 收盤後模式切換
# - 一鍵匯出 Excel
# =======================

import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import warnings
from datetime import date
import io

warnings.filterwarnings('ignore')

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（整合版）", layout="wide")
st.title("📈 股票策略篩選器（穩定整合版）")
st.markdown("---")

# -------------------------------------------------
# 股票清單（SSL 穩定版 + 快取）
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    all_tickers = []

    for mode in ['2', '4']:  # 2=上市, 4=上櫃
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        resp = requests.get(url, headers=headers, verify=False, timeout=10)
        df = pd.read_html(resp.text)[0].iloc[1:]

        for item in df[0]:
            code = str(item).split()[0]
            if code.isdigit() and len(code) == 4:
                all_tickers.append(f"{code}.TW")

    return sorted(set(all_tickers))

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    return yf.download(ticker, period="3mo", interval="1d", progress=False)

@st.cache_data(ttl=120)
def download_5m(ticker):
    return yf.download(ticker, period="5d", interval="5m", progress=False)

# -------------------------------------------------
# 今日防洗版記錄
# -------------------------------------------------
today_key = f"seen_{date.today()}"
if today_key not in st.session_state:
    st.session_state[today_key] = set()

# -------------------------------------------------
# 策略
# -------------------------------------------------
def strategy_consolidation(ticker):
    df = download_daily(ticker)
    if len(df) < 21:
        return None

    # 排除低成交量股票（10 日平均 < 500 張）
    if df['Volume'].tail(10).mean() < 500_000:
        return None

    close = float(df['Close'].iloc[-1])
    prev_vol = float(df['Volume'].iloc[-2])
    vol = float(df['Volume'].iloc[-1])
    high20 = float(df['High'].iloc[:-1].tail(20).max())

    if close > high20 and vol > prev_vol * 2:
        if ticker in st.session_state[today_key]:
            return None
        st.session_state[today_key].add(ticker)
        return {
            "股票": ticker,
            "現價": round(close, 2),
            "突破價": round(high20, 2),
            "量增倍數": round(vol / prev_vol, 1)
        }


def strategy_5m_breakout(ticker):
    df = download_5m(ticker)
    if len(df) < 21:
        return None

    close = df['Close']
    ma20 = ta.trend.sma_indicator(close, 20)

    if close.iloc[-1] > ma20.iloc[-1] and close.iloc[-2] < ma20.iloc[-2]:
        if float(df['Volume'].iloc[-1]) > float(df['Volume'].iloc[-2]) * 2:
            if ticker in st.session_state[today_key]:
                return None
            st.session_state[today_key].add(ticker)
            return {
                "股票": ticker,
                "時間": df.index[-1].strftime('%H:%M'),
                "現價": round(float(close.iloc[-1]), 2)
            }


def strategy_high_level(ticker):
    df = download_daily(ticker)
    if len(df) < 20:
        return None

    if df['Volume'].tail(10).mean() < 500_000:
        return None

    df['MA5'] = ta.trend.sma_indicator(df['Close'], 5)
    rise20 = float(df['Close'].iloc[-1] / df['Close'].iloc[-20] - 1)

    if rise20 > 0.1 and float(df['Close'].iloc[-1]) > float(df['MA5'].iloc[-1]):
        if ticker in st.session_state[today_key]:
            return None
        st.session_state[today_key].add(ticker)
        return {
            "股票": ticker,
            "現價": round(float(df['Close'].iloc[-1]), 2),
            "20日漲幅": f"{round(rise20*100,1)}%"
        }

STRATEGIES = {
    "盤整突破": strategy_consolidation,
    "5分K突破": strategy_5m_breakout,
    "高檔飛舞": strategy_high_level
}

# -------------------------------------------------
# Sidebar 設定
# -------------------------------------------------
st.sidebar.header("掃描模式")
mode = st.sidebar.radio("模式", ["盤中", "收盤後"])

st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW,2317.TW")
    tickers = [x.strip() for x in raw.split(',') if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        st.session_state['all'] = get_all_tw_tickers()
    tickers = st.session_state.get('all', [])[:30]

st.sidebar.header("策略選擇")
selected = []
for k in STRATEGIES:
    if k == "5分K突破" and mode == "收盤後":
        continue
    if st.sidebar.checkbox(k, True):
        selected.append(k)

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    result = {k: [] for k in selected}
    bar = st.progress(0)

    for i, t in enumerate(tickers):
        bar.progress((i + 1) / len(tickers))
        for k in selected:
            r = STRATEGIES[k](t)
            if r:
                result[k].append(r)
        time.sleep(0.3)

    bar.empty()

    st.subheader("📊 掃描結果")
    for k in selected:
        st.markdown(f"### {k}")
        if result[k]:
            st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
        else:
            st.info("無符合條件股票")

    # 匯出 Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for k, data in result.items():
            if data:
                pd.DataFrame(data).to_excel(writer, sheet_name=k, index=False)

    st.download_button(
        "📥 下載掃描結果 Excel",
        data=output.getvalue(),
        file_name="stock_scan_result.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
