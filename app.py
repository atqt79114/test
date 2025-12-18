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
st.set_page_config(page_title="股票策略篩選器（收盤日線版）", layout="wide")
st.title("📈 股票策略篩選器（收盤日線版）")
st.markdown("---")

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
# Yahoo 資料快取 (修正 MultiIndex 問題)
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    # 下載數據
    df = yf.download(ticker, period="3mo", interval="1d", progress=False)
    
    # 【關鍵修正】如果欄位是 MultiIndex，將其扁平化
    # yfinance 新版可能會回傳 (Price, Ticker) 的格式，這裡強制只留 Price
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    # 確保欄位名稱乾淨
    return df

# -------------------------------------------------
# 今日防洗版
# -------------------------------------------------
today_key = f"seen_{date.today()}"
if today_key not in st.session_state:
    st.session_state[today_key] = set()

# -------------------------------------------------
# 策略定義 (只保留日線策略)
# -------------------------------------------------
def strategy_consolidation(ticker):
    """盤整突破策略"""
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
        high20 = float(df["High"].iloc[:-1].tail(20).max())

        # 簡單過濾 divide by zero
        if prev_vol == 0:
            return None

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

def strategy_high_level(ticker):
    """高檔飛舞策略"""
    try:
        df = download_daily(ticker)
        if len(df) < 21:
            return None

        vol_mean = float(df["Volume"].tail(10).mean())
        if vol_mean < 500_000:
            return None

        df["MA5"] = ta.trend.sma_indicator(df["Close"], 5)
        
        close_now = float(df["Close"].iloc[-1])
        close_20_ago = float(df["Close"].iloc[-20])
        ma5_now = float(df["MA5"].iloc[-1])

        if close_20_ago == 0:
            return None

        rise20 = (close_now / close_20_ago) - 1

        if rise20 > 0.1 and close_now > ma5_now:
            return {
                "股票": ticker,
                "現價": round(close_now, 2),
                "20日漲幅": f"{round(rise20 * 100, 1)}%",
            }
    except Exception:
        return None
    return None

STRATEGIES = {
    "盤整突破": strategy_consolidation,
    "高檔飛舞": strategy_high_level,
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
        with st.spinner("抓取清單中..."):
            st.session_state["all"] = get_all_tw_tickers()
    
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"目前清單數量: {len(all_tickers)}")
    
    # 為了示範效率，這裡預設只跑前 50 檔，你可以把 [:50] 拿掉跑全部
    scan_limit = st.sidebar.slider("掃描數量限制 (測試用)", 10, 2000, 50)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = []
for k in STRATEGIES:
    if st.sidebar.checkbox(k, True):
        selected.append(k)

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if not tickers:
        st.warning("請先輸入股票代碼或抓取全市場清單")
    else:
        result = {k: [] for k in selected}
        
        # 進度條
        progress_text = "掃描進行中..."
        my_bar = st.progress(0, text=progress_text)
        
        for i, t in enumerate(tickers):
            # 更新進度條
            my_bar.progress((i + 1) / len(tickers), text=f"掃描中: {t}")
            
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    result[k].append(r)
            
            # 稍微休息避免被擋 IP
            time.sleep(0.1)

        my_bar.empty()

        st.subheader("📊 掃描結果")
        
        has_result = False
        all_rows = []

        for k in selected:
            if result[k]:
                has_result = True
                st.markdown(f"### {k}")
                df_res = pd.DataFrame(result[k])
                st.dataframe(df_res, use_container_width=True)
                
                # 收集資料做 CSV
                for row in result[k]:
                    r_copy = row.copy()
                    r_copy["策略"] = k
                    all_rows.append(r_copy)

        if not has_result:
            st.info("沒有掃描到符合條件的股票")

        # -------------------------------------------------
        # CSV 匯出
        # -------------------------------------------------
        if all_rows:
            st.markdown("---")
            df_export = pd.DataFrame(all_rows)
            st.download_button(
                "📥 下載掃描結果 CSV",
                data=df_export.to_csv(index=False, encoding="utf-8-sig"),
                file_name="stock_scan_result.csv",
                mime="text/csv",
            )
