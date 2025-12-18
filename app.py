import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import time
import warnings
from datetime import date

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（突破+洗盤版）", layout="wide")
st.title("📈 股票策略篩選器（突破+洗盤版）")
st.markdown("""
---
**策略說明：**
1.  **盤整突破 (起漲點)**：尋找整理後「帶量突破」前20日高點的股票。
2.  **爆量回檔 (買綠/洗盤)**：尋找昨日「爆量收黑」，但今日「守住5日線」且尚未大漲的股票。
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
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            for item in df[0]:
                code = str(item).split()[0]
                if code.isdigit() and len(code) == 4:
                    tickers.append(f"{code}.TW")
        except Exception as e:
            st.error(f"抓取股票清單失敗: {e}")
    return sorted(set(tickers))

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    df = yf.download(ticker, period="3mo", interval="1d", progress=False)
    # 【關鍵修正】扁平化 MultiIndex，解決 ValueError
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# -------------------------------------------------
# 策略定義
# -------------------------------------------------

def strategy_consolidation(ticker):
    """盤整突破策略 (使用者指定邏輯)"""
    try:
        df = download_daily(ticker)
        if len(df) < 21:
            return None

        # 確保取出來的是純量 (scalar)
        vol_mean = float(df["Volume"].tail(10).mean())
        if vol_mean < 500_000:
            return None

        close = float(df["Close"].iloc[-1])
        prev_vol = float(df["Volume"].iloc[-2])
        vol = float(df["Volume"].iloc[-1])
        
        # 計算前20天(不含今天)的最高價
        # iloc[:-1] 排除今天，tail(20) 取近20天，max() 取最大值
        high20 = float(df["High"].iloc[:-1].tail(20).max())

        # 簡單過濾 divide by zero
        if prev_vol == 0:
            return None

        # 條件：收盤突破20日高點 且 量增2倍
        if close > high20 and vol > prev_vol * 2:
            return {
                "股票": ticker,
                "現價": round(close, 2),
                "突破價": round(high20, 2),
                "量增倍數": round(vol / prev_vol, 1),
            }
    except Exception:
        return None
    return None

def strategy_washout_rebound(ticker):
    """
    【爆量回檔洗盤】(買綠不買紅)
    顯示 MA5 / MA10 / MA20 價位，方便盤中低接判斷
    """
    try:
        df = download_daily(ticker)
        if len(df) < 30:
            return None

        # === 取得數據 ===
        close = df["Close"]
        open_price = df["Open"]
        volume = df["Volume"]

        # === 均線 ===
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)

        # === 價量資料 ===
        c_now = float(close.iloc[-1])
        c_prev = float(close.iloc[-2])
        o_prev = float(open_price.iloc[-2])

        v_prev = float(volume.iloc[-2])
        v_prev_2 = float(volume.iloc[-3])

        ma5_now = float(ma5.iloc[-1])
        ma10_now = float(ma10.iloc[-1])
        ma20_now = float(ma20.iloc[-1])

        # -------------------------------------------------
        # 條件 1：趨勢向上（MA5 > MA10 > MA20）
        # -------------------------------------------------
        if not (ma5_now > ma10_now > ma20_now):
            return None

        # -------------------------------------------------
        # 條件 2：昨日爆量黑 K
        # -------------------------------------------------
        is_black = c_prev < o_prev
        is_massive = v_prev > v_prev_2 * 1.5

        if not (is_black and is_massive):
            return None

        # -------------------------------------------------
        # 條件 3：今日守住 MA5
        # -------------------------------------------------
        if c_now < ma5_now:
            return None

        # -------------------------------------------------
        # 條件 4：買綠不買紅（避免追高）
        # -------------------------------------------------
        pct_change = (c_now / c_prev) - 1
        if pct_change > 0.02:
            return None

        # -------------------------------------------------
        # 回傳結果（含 MA 價位）
        # -------------------------------------------------
        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "MA5": round(ma5_now, 2),
            "MA10": round(ma10_now, 2),
            "MA20": round(ma20_now, 2),
            "狀態": "爆量回檔｜守MA5",
            "今日漲幅": f"{round(pct_change * 100, 2)}%"
        }

    except Exception:
        return None
    return None

STRATEGIES = {
    "盤整突破 (起漲)": strategy_consolidation,
    "爆量回檔 (買綠)": strategy_washout_rebound,
}

# -------------------------------------------------
# Sidebar
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW, 3231.TW, 2354.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        with st.spinner("抓取清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
    
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"清單數量: {len(all_tickers)}")
    scan_limit = st.sidebar.slider("掃描數量限制", 10, 2000, 50)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = []
for k in STRATEGIES:
    if st.sidebar.checkbox(k, value=True):
        selected.append(k)

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if not tickers:
        st.warning("請先輸入代碼或抓取清單")
    else:
        result = {k: [] for k in selected}
        my_bar = st.progress(0, text="掃描進行中...")
        
        for i, t in enumerate(tickers):
            my_bar.progress((i + 1) / len(tickers), text=f"掃描中: {t}")
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    result[k].append(r)
            time.sleep(0.05)

        my_bar.empty()
        st.subheader("📊 掃描結果")
        
        has_result = False
        all_rows = []

        for k in selected:
            if result[k]:
                has_result = True
                st.markdown(f"### {k}")
                st.dataframe(pd.DataFrame(result[k]), use_container_width=True)
                for row in result[k]:
                    row["策略"] = k
                    all_rows.append(row)

        if not has_result:
            st.info("無符合條件股票")

        if all_rows:
            st.markdown("---")
            df_export = pd.DataFrame(all_rows)
            st.download_button(
                "📥 下載 CSV",
                data=df_export.to_csv(index=False, encoding="utf-8-sig"),
                file_name="stock_scan_result.csv",
                mime="text/csv",
            )
