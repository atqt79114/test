import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
import random

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（極速實戰版）", layout="wide")
st.title("📈 股票策略篩選器（極速實戰版）")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必嚴格執行停損，控制風險。**

---
#### 💎 全策略共同核心：股價站上所有均線
* **定義**：現價 > 5MA、10MA、20MA、60MA、120MA
* **意義**：代表股價高於過去半年所有人的平均成本，上方無套牢賣壓，是強勢股的標準特徵。

#### 🧠 四大策略邏輯解析：

1. **🚀 SMC 箱體突破 (追價策略)**
   * **邏輯**：股價長時間在箱體整理，今日出現**倍量**（成交量 > 昨日2倍）並突破箱體上緣壓力 (BSL)。
   * **意義**：主力表態攻擊，願意花大錢吃掉所有賣單，通常是波段行情的開始。

2. **🛡️ SMC 回測支撐 (低接策略)**
   * **邏輯**：強勢股回檔至箱體下緣支撐 (OB)，且均線糾結未發散。
   * **意義**：在上升趨勢中尋找「盈虧比」最好的進場點，買在支撐確認有守的位置。

3. **🛁 爆量回檔 (主力洗盤)**
   * **邏輯**：
     * **昨日**：爆量黑K（製造恐慌），但實體K棒沒有跌破 5日線（主力有護盤）。
     * **今日**：成交量明顯縮小，且股價繼續守住 5日線。
   * **意義**：這是標準的「假跌破、真洗盤」。利用恐慌甩掉沒信心的散戶，籌碼換手後量縮止穩。

4. **📦 盤整突破 (均線糾結)**
   * **邏輯**：短中長期均線糾結在一起（代表市場成本一致），今日帶量突破近期高點。
   * **意義**：均線糾結代表波動率壓縮到極致，突破往往伴隨著能量釋放，容易走出單邊噴出行情。

---
**💰 風險管理 (Risk Management)：**
* **🛑 停損**：收盤 **實體跌破 5日均線** (5MA) 即出場。
* **🎯 停利**：風險報酬比 **1 : 1.5** (賺賠比)。

**📊 篩選範圍：** 上市櫃普通股 (排除 ETF)，成交量 > 500 張。
---
""")

# -------------------------------------------------
# 輔助：產生外資連結
# -------------------------------------------------
def get_chip_link(ticker):
    # 處理代號: 2330.TW -> 2330
    code = ticker.split('.')[0]
    return f"https://tw.stock.yahoo.com/quote/{code}/institutional-trading"

# -------------------------------------------------
# 股票清單 (排除 ETF，只留 4碼個股)
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    stock_map = {} 
    
    for mode in ["2", "4"]: # 2=上市, 4=上櫃
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            
            for item in df[0]:
                data = str(item).split()
                if len(data) >= 2:
                    code = data[0]
                    name = data[1]
                    
                    # === 嚴格限制 4 碼 (排除 5碼 ETF) ===
                    if code.isdigit() and len(code) == 4:
                        suffix = ".TWO" if mode == "4" else ".TW"
                        stock_map[f"{code}{suffix}"] = name
        except Exception:
            pass
            
    return stock_map

# -------------------------------------------------
# Yahoo 資料快取
# -------------------------------------------------
@st.cache_data(ttl=300)
def download_daily(ticker):
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty: return pd.DataFrame()
        return df
    except:
        return pd.DataFrame()

# -------------------------------------------------
# 輔助：計算風控數據
# -------------------------------------------------
def calculate_risk_reward(c_now, ma5_now, date_now):
    sl_price = round(ma5_now, 2)
    risk = c_now - sl_price
    if risk <= 0: risk = 0.01 
    target_price = round(c_now + (risk * 1.5), 2)
    
    return {
        "訊號日期": date_now.strftime('%Y-%m-%d'),
        "停損(5MA)": sl_price,
        "停利(1:1.5)": target_price,
        "潛在獲利": f"{round((risk * 1.5 / c_now)*100, 1)}%"
    }

# -------------------------------------------------
# 核心：回測引擎 (修正版：觸價即停利)
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        lookback_days = months * 22
        if len(df) < lookback_days + 130: return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        
        start_idx = len(df) - lookback_days
        if start_idx < 130: start_idx = 130
        
        close = df["Close"]
        open_p = df["Open"]
        high = df["High"] # 用於判斷停利
        low = df["Low"]
        volume = df["Volume"]
        
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        
        vol_ma5 = volume.rolling(5).mean()

        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]
            h_curr = high.iloc[i]
            ma5_curr = ma5.iloc[i]

            # 1. 出場檢查
            if in_position:
                # A. 停利優先：盤中碰到目標價
                if h_curr >= target_price:
                    profit = (target_price - entry_price) / entry_price
                    trades.append(profit)
                    in_position = False
                    continue

                # B. 停損：收盤實體跌破 5MA
                if c_curr < ma5_curr:
                    profit = (c_curr - entry_price) / entry_price
                    trades.append(profit)
                    in_position = False
                
                continue

            # 2. 進場檢查 (空手時)
            if not (c_curr > ma5_curr and c_curr > ma10.iloc[i] and c_curr > ma20.iloc[i] and 
                    c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i]):
                continue
            
            if volume.iloc[i] < 500_000: continue

            signal = False

            # === 策略判斷 ===
            if strategy_type == "washout":
                c_prev = close.iloc[i-1]
                o_prev = open_p.iloc[i-1]
                v_prev = volume.iloc[i-1]
                v_prev_2 = volume.iloc[i-2]
                ma5_prev = ma5.iloc[i-1]
                cond_prev = (c_prev < o_prev) and (v_prev > v_prev_2) and (c_prev >= ma5_prev)
                cond_curr = (volume.iloc[i] < v_prev) and (c_curr >= ma5_curr)
                if cond_prev and cond_curr: signal = True
            
            elif strategy_type == "consolidation":
                res = high.iloc[i-21:i].max()
                vals = [ma5.iloc[i], ma10.iloc[i], ma20.iloc[i]]
                spread = (max(vals) - min(vals)) / c_curr
                if c_curr > res and spread < 0.06 and volume.iloc[i] > vol_ma5.iloc[i-1] * 1.5:
                    signal = True

            if signal:
                in_position = True
                entry_price = c_curr
                # 設定停利價
                risk = entry_price - ma5_curr
                if risk <= 0: risk = entry_price * 0.01
                target_price = entry_price + (risk * 1.5)

        if not trades:
            return {"回測勝率": "無訊號", "平均獲利": "0%", "總交易": 0}
        
        win_count = sum(1 for p in trades if p > 0)
        win_rate = (win_count / len(trades)) * 100
        avg_ret = (sum(trades) / len(trades)) * 100
        
        return {
            "回測勝率": f"{round(win_rate, 1)}%",
            "平均獲利": f"{round(avg_ret, 2)}%",
            "總交易": len(trades)
        }
    except:
        return None

# -------------------------------------------------
# 策略一：SMC 箱體突破
# -------------------------------------------------
def strategy_smc_breakout(ticker, name, backtest_months):
    try:
        df = download_daily(ticker)
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]

        c_now = float(close.iloc[-1])

        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120):
            return None

        lookback = 40
        resistance = high.iloc[-lookback-1:-1].max()
        support = low.iloc[-lookback-1:-1].min()

        if (resistance - support) / support > 0.30: return None
        if c_now <= resistance: return None
        if vol_today <= float(volume.iloc[-2]) * 2: return None

        rr_data = calculate_risk_reward(c_now, ma5, df.index[-1])

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            **rr_data,
            "壓力(BSL)": round(resistance, 2),
            "成交量(千)": int(vol_today / 1000),
            "外資詳情": get_chip_link(ticker),
            "狀態": "倍量突破 🚀"
        }
    except: return None

# -------------------------------------------------
# 策略二：SMC 回測支撐
# -------------------------------------------------
def strategy_smc_support(ticker, name, backtest_months):
    try:
        df = download_daily(ticker)
        if len(df) < 200: return None

        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]

        c_now = float(close.iloc[-1])

        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120):
            return None

        lookback = 40
        resistance = high.iloc[-lookback:].max()
        support = low.iloc[-lookback:].min()

        if (resistance - support) / support > 0.30: return None
        distance = (c_now - support) / support
        if not (-0.02 <= distance <= 0.05): return None

        ma_values = [ma5, ma10, ma20]
        if (max(ma_values) - min(ma_values)) / min(ma_values) > 0.10: return None

        rr_data = calculate_risk_reward(c_now, ma5, df.index[-1])

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            **rr_data,
            "支撐(OB)": round(support, 2),
            "成交量(千)": int(vol_today / 1000),
            "外資詳情": get_chip_link(ticker),
            "狀態": "回測支撐 🛡️"
        }
    except: return None

# -------------------------------------------------
# 策略三：爆量回檔 (洗盤)
# -------------------------------------------------
def strategy_washout_rebound(ticker, name, backtest_months):
    try:
        df = download_daily(ticker)
        if len(df) < 125: return None

        close, open_p, volume = df["Close"], df["Open"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_prev = close.iloc[-2]
        o_prev = open_p.iloc[-2]
        v_prev = float(volume.iloc[-2])
        v_prev_2 = float(volume.iloc[-3])
        
        c_now = float(close.iloc[-1])
        ma5_now = ma5.iloc[-1]
        
        if c_prev >= o_prev: return None 
        if v_prev <= v_prev_2: return None 
        if c_prev < ma5.iloc[-2]: return None 
        if c_now < ma5_now: return None 
        if vol_today >= v_prev: return None 

        if not (c_now > ma5_now and c_now > ma10.iloc[-1] and c_now > ma20.iloc[-1] and 
                c_now > ma60.iloc[-1] and c_now > ma120.iloc[-1]):
            return None

        bt_res = run_backtest(df, "washout", backtest_months)
        rr_data = calculate_risk_reward(c_now, ma5_now, df.index[-1])

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            **rr_data,
            **bt_res,
            "成交量(千)": int(vol_today / 1000),
            "縮量比": f"{round((vol_today/v_prev)*100, 1)}%",
            "外資詳情": get_chip_link(ticker),
            "狀態": "強勢洗盤 🛁"
        }
    except: return None

# -------------------------------------------------
# 策略四：日線盤整突破
# -------------------------------------------------
def strategy_consolidation(ticker, name, backtest_months):
    try:
        df = download_daily(ticker)
        if len(df) < 130: return None

        close, open_p, high, volume = df["Close"], df["Open"], df["High"], df["Volume"]
        vol_today = float(volume.iloc[-1])
        if vol_today < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5  = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]
        
        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120):
            return None

        ma_vals = [ma5, ma10, ma20]
        if (max(ma_vals) - min(ma_vals)) / c_now > 0.06: return None

        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if vol_today < vol_ma5 * 1.5: return None
        if c_now < float(open_p.iloc[-1]): return None

        bt_res = run_backtest(df, "consolidation", backtest_months)
        rr_data = calculate_risk_reward(c_now, ma5, df.index[-1])

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            **rr_data,
            **bt_res,
            "狀態": "帶量突破 📦",
            "外資詳情": get_chip_link(ticker)
        }
    except: return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🚀 SMC 箱體突破": strategy_smc_breakout,
    "🛡️ SMC 回測支撐": strategy_smc_support,
    "🛁 爆量回檔（洗盤）": strategy_washout_rebound,
    "📦 盤整突破 (均線糾結)": strategy_consolidation,
}

# -------------------------------------------------
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW")
    tickers = [x.strip() for x in raw.split(",") if x.strip()]
    full_map = st.session_state.get("stock_map", {})
    if not full_map:
        with st.spinner("載入名稱庫..."):
            st.session_state["stock_map"] = get_all_tw_tickers()
            full_map = st.session_state["stock_map"]
    stock_map = {}
    for t in tickers:
        stock_map[t] = full_map.get(t, t)
else:
    if st.sidebar.button("重抓上市上櫃清單"):
        with st.spinner("更新清單中..."):
            st.session_state["stock_map"] = get_all_tw_tickers()
            st.rerun()
    
    stock_map = st.session_state.get("stock_map", {})
    if not stock_map:
        st.session_state["stock_map"] = get_all_tw_tickers()
        stock_map = st.session_state["stock_map"]

    st.sidebar.write(f"目前快取: {len(stock_map)} 檔")
    limit = st.sidebar.slider("掃描數量", 50, 2000, 200)
    tickers = list(stock_map.keys())[:limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

st.sidebar.markdown("---")
st.sidebar.header("📊 回測設定")
st.sidebar.caption("※ 回測僅適用於：爆量回檔 & 盤整突破")
backtest_period = st.sidebar.radio("回測區間", [3, 6, 12], format_func=lambda x: f"過去 {x} 個月")

# -------------------------------------------------
# 執行掃描
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if not tickers:
        st.error("沒有股票代碼！")
    else:
        result = {k: [] for k in selected}
        progress_bar = st.progress(0)
        status_text = st.empty()
        total = len(tickers)
        for i, t in enumerate(tickers):
            progress_bar.progress((i + 1) / total)
            name = stock_map.get(t, t)
            status_text.text(f"掃描中 ({i+1}/{total}): {t} {name}")
            for k in selected:
                r = STRATEGIES[k](t, name, backtest_period)
                if r:
                    r["策略"] = k
                    result[k].append(r)
        
        progress_bar.empty()
        status_text.empty()
        has_data = False
        for k in selected:
            if result[k]:
                has_data = True
                st.subheader(f"📊 {k}")
                df_res = pd.DataFrame(result[k])
                
                # 欄位排序
                base_cols = ["代號", "名稱", "現價", "停損(5MA)", "停利(1:1.5)", "外資詳情"]
                
                if "回測勝率" in df_res.columns:
                    target_cols = base_cols + ["回測勝率", "平均獲利", "總交易"]
                else:
                    target_cols = base_cols
                
                other_cols = [c for c in df_res.columns if c not in target_cols]
                
                st.dataframe(
                    df_res[target_cols + other_cols], 
                    use_container_width=True,
                    column_config={
                        "外資詳情": st.column_config.LinkColumn(
                            "外資詳情", display_text="查看數據"
                        )
                    }
                )
        if not has_data:
            st.info("掃描完成，但沒有符合條件的股票。")
