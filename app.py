import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import warnings

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（突破+洗盤版）", layout="wide")
st.title("📈 股票策略篩選器（突破+洗盤版）")
st.markdown("""
---
**策略說明：**
1. **日線盤整突破**：均線糾結 → 放量突破壓力 → 收盤站穩。
2. **爆量回檔洗盤**：強多頭 → 昨日爆量洗盤 → 今日量縮守 MA5。
---
""")

# -------------------------------------------------
# 股票清單（SSL 穩定版）
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    for mode in ["2", "4"]:  # 2=上市, 4=上櫃
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        r = requests.get(url, headers=headers, verify=False, timeout=10)
        df = pd.read_html(r.text)[0].iloc[1:]
        for item in df[0]:
            code = str(item).split()[0]
            if code.isdigit() and len(code) == 4:
                tickers.append(f"{code}.TW")
    return sorted(set(tickers))

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    df = yf.download(ticker, period="1y", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# -------------------------------------------------
# 策略一：爆量回檔 / 洗盤低接（含 500 張過濾）
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 60:
            return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # === 流動性（至少 500 張）===
        if volume.iloc[-2] < 500_000:
            return None

        # === 均線 ===
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)

        # === 昨日 ===
        c_prev = close.iloc[-2]
        o_prev = open_p.iloc[-2]
        h_prev = high.iloc[-2]
        l_prev = low.iloc[-2]
        v_prev = volume.iloc[-2]

        # === 今日 ===
        c_now = close.iloc[-1]
        v_now = volume.iloc[-1]

        # 條件 1：多頭結構
        if not (ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]):
            return None

        # 條件 2：昨日爆量洗盤黑 K
        if c_prev >= o_prev:
            return None

        vol_ma5 = volume.rolling(5).mean()
        if v_prev < vol_ma5.iloc[-2] * 1.5:
            return None

        if h_prev == l_prev:
            return None

        lower_shadow = (min(o_prev, c_prev) - l_prev) / (h_prev - l_prev)
        if lower_shadow < 0.3:
            return None

        # 條件 3：今日量縮守 MA5
        if c_now < ma5.iloc[-1]:
            return None

        if v_now > v_prev * 0.8:
            return None

        # 條件 4：不追價
        if (c_now / c_prev - 1) > 0.02:
            return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "MA5": round(ma5.iloc[-1], 2),
            "MA10": round(ma10.iloc[-1], 2),
            "MA20": round(ma20.iloc[-1], 2),
            "狀態": "爆量洗盤｜量縮守MA5",
            "昨日量": int(v_prev)
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：日線盤整突破（均線糾結＋站穩）
# -------------------------------------------------
def strategy_consolidation(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 260:
            return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # === 流動性 ===
        if volume.iloc[-1] < 500_000:
            return None

        # === 均線 ===
        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma240 = ta.trend.sma_indicator(close, 240)

        c_now = close.iloc[-1]

        # 條件 1：長期方向（年線之上）
        if c_now < ma240.iloc[-1]:
            return None

        # 條件 2：均線糾結
        ma_vals = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1], ma60.iloc[-1]]
        ma_spread = (max(ma_vals) - min(ma_vals)) / c_now
        if ma_spread > 0.035:
            return None

        # 條件 3：盤整壓力突破
        resistance = high.iloc[:-1].tail(20).max()
        if c_now < resistance * 1.01:
            return None

        # 條件 4：放量突破
        vol_ma5 = volume.rolling(5).mean()
        if volume.iloc[-1] < vol_ma5.iloc[-2] * 1.5:
            return None

        # 條件 5：收盤站穩（實體夠）
        body = abs(c_now - open_p.iloc[-1])
        rng = high.iloc[-1] - low.iloc[-1]
        if rng == 0 or body / rng < 0.55:
            return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "突破壓力": round(resistance, 2),
            "MA5": round(ma5.iloc[-1], 2),
            "MA10": round(ma10.iloc[-1], 2),
            "MA20": round(ma20.iloc[-1], 2),
            "MA60": round(ma60.iloc[-1], 2),
            "MA240": round(ma240.iloc[-1], 2),
            "均線糾結度": f"{round(ma_spread * 100, 2)}%"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "盤整突破（均線糾結）": strategy_consolidation,
    "爆量回檔（洗盤低接）": strategy_washout_rebound,
}

# -------------------------------------------------
# Sidebar
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        st.session_state["all"] = get_all_tw_tickers()
    all_tickers = st.session_state.get("all", [])
    scan_limit = st.sidebar.slider("掃描數量限制", 10, 2000, 50)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    result = {k: [] for k in selected}
    bar = st.progress(0.0)

    for i, t in enumerate(tickers):
        bar.progress((i + 1) / len(tickers), text=f"掃描中：{t}")
        for k in selected:
            r = STRATEGIES[k](t)
            if r:
                r["策略"] = k
                result[k].append(r)
        time.sleep(0.05)

    bar.empty()
    st.subheader("📊 掃描結果")

    all_rows = []
    for k in result:
        if result[k]:
            st.markdown(f"### {k}")
            st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
            all_rows.extend(result[k])

    if all_rows:
        st.download_button(
            "📥 下載 CSV",
            pd.DataFrame(all_rows).to_csv(index=False, encoding="utf-8-sig"),
            "stock_scan_result.csv",
            "text/csv"
        )
    else:
        st.info("無符合條件股票")
