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
st.set_page_config(page_title="股票策略篩選器（實戰修正版）", layout="wide")
st.title("📈 股票策略篩選器（實戰修正版）")
st.markdown("""
---
**策略邏輯優化說明：**
1. **爆量回檔**：放寬均線限制（允許 MA5 短暫跌破），移除下影線強制作法，專注於「守住 MA5」。
2. **盤整突破**：均線糾結排除 MA60（季線），只看短中期（5/10/20）是否蓄勢待發。
---
""")

# -------------------------------------------------
# 股票清單
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    tickers = []
    for mode in ["2", "4"]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            for item in df[0]:
                code = str(item).split()[0]
                if code.isdigit() and len(code) == 4:
                    tickers.append(f"{code}.TW")
        except Exception:
            pass
    return sorted(set(tickers))

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    # 這裡如果不加 try-except，有些下市股票會讓程式報錯
    try:
        df = yf.download(ticker, period="1y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 策略一：爆量回檔 / 洗盤低接
# -------------------------------------------------
def strategy_washout_rebound(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 60: return None

        close = df["Close"]
        open_p = df["Open"]
        # high = df["High"] # 暫時用不到
        # low = df["Low"]   # 暫時用不到
        volume = df["Volume"]

        # === 流動性（放寬至 300 張，避免錯過中小型股）===
        if volume.iloc[-2] < 300_000: return None

        # === 均線 ===
        ma5  = ta.trend.sma_indicator(close, 5)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)

        # === 昨日數據 (T-1) ===
        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])
        v_prev = float(volume.iloc[-2])

        # === 今日數據 (T) ===
        c_now = float(close.iloc[-1])
        v_now = float(volume.iloc[-1])
        ma5_now = float(ma5.iloc[-1])
        ma20_now = float(ma20.iloc[-1])
        ma60_now = float(ma60.iloc[-1])

        # 【修正1】條件：中長期多頭即可，短期允許混亂
        # 只要 MA20 > MA60 (月線在季線之上) 且 股價 > MA20 (還在生命線上)
        if not (ma20_now > ma60_now and c_now > ma20_now):
            return None

        # 【條件2】昨日爆量洗盤黑 K
        # 2-1: 收黑 K (Close < Open)
        if c_prev >= o_prev: return None
        
        # 2-2: 爆量 (昨日量 > 5日均量 * 1.5)
        vol_ma5_prev = float(volume.rolling(5).mean().iloc[-2])
        if v_prev < vol_ma5_prev * 1.5: return None

        # 【修正2】移除「下影線 > 0.3」的限制
        # 洗盤通常是恐慌殺盤，不一定有長下影線。
        # 我們改用「實體長度」判斷，跌幅要夠才有洗盤效果 (例如跌 > 1.5%)
        prev_pct_change = (c_prev / float(close.iloc[-3]) - 1)
        if prev_pct_change > -0.015: # 如果跌幅小於 1.5%，不算洗盤
            return None

        # 【條件3】今日量縮守 MA5
        # 3-1: 站回 MA5
        if c_now < ma5_now: return None

        # 3-2: 今日量縮 (比昨日少)
        if v_now > v_prev: return None

        # 【條件4】不追價 (今日漲幅 < 3%)
        # 既然是低接，漲太多就不要了
        if (c_now / c_prev - 1) > 0.03: return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "MA5": round(ma5_now, 2),
            "昨日跌幅": f"{round(prev_pct_change*100, 2)}%",
            "狀態": "爆量洗盤後守穩"
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略二：日線盤整突破（均線糾結＋站穩）
# -------------------------------------------------
def strategy_consolidation(ticker):
    try:
        df = download_daily(ticker)
        if len(df) < 120: return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # === 流動性 ===
        if volume.iloc[-1] < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        
        # 1. 大趨勢：年線或半年線之上 (這裡用 60MA 季線當生命線)
        if c_now < ma60.iloc[-1]: return None

        # 【修正3】均線糾結計算：只看 5, 10, 20
        # 如果把 60MA 加進來，要求 3.5% 幾乎抓不到股票
        ma_vals = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]]
        ma_max = max(ma_vals)
        ma_min = min(ma_vals)
        
        # 糾結定義：(最大均線 - 最小均線) / 股價 < 5% (放寬到 5%)
        ma_spread = (ma_max - ma_min) / c_now
        if ma_spread > 0.05: return None

        # 2. 突破壓力：收盤價 > 過去 20 天最高價
        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        # 3. 放量突破：量 > 5日均量 * 1.5
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if volume.iloc[-1] < vol_ma5 * 1.5: return None

        # 4. 實體紅K確認
        if c_now < float(open_p.iloc[-1]): return None

        return {
            "股票": ticker,
            "現價": round(c_now, 2),
            "突破價": round(resistance, 2),
            "糾結度": f"{round(ma_spread * 100, 2)}%",
            "量能": "放量"
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
# UI 邏輯保持不變
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    # 預設一些熱門股，方便你測試有沒有資料
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW, 3231.TW, 2618.TW, 2609.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
else:
    if st.sidebar.button("抓取上市上櫃"):
        with st.spinner("抓取中...請稍候"):
            st.session_state["all"] = get_all_tw_tickers()
    
    all_tickers = st.session_state.get("all", [])
    st.sidebar.write(f"已載入: {len(all_tickers)} 檔")
    scan_limit = st.sidebar.slider("掃描數量限制 (測試用)", 10, 1000, 100)
    tickers = all_tickers[:scan_limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

if st.button("開始掃描", type="primary"):
    if not tickers:
        st.warning("請先輸入代碼或載入全市場")
    else:
        result = {k: [] for k in selected}
        bar = st.progress(0.0)
        status_text = st.empty()

        for i, t in enumerate(tickers):
            bar.progress((i + 1) / len(tickers))
            status_text.text(f"正在掃描：{t}")
            
            for k in selected:
                r = STRATEGIES[k](t)
                if r:
                    r["策略"] = k
                    result[k].append(r)
            # time.sleep(0.01) # 稍微加速

        bar.empty()
        status_text.empty()
        st.subheader("📊 掃描結果")

        has_data = False
        all_rows = []
        for k in result:
            if result[k]:
                has_data = True
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
        elif not has_data:
            st.info("沒有符合條件的股票，請嘗試增加掃描數量或更換手動清單。")
