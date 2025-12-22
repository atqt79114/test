import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="股票策略篩選器（全功能終極版）", layout="wide")
st.title("📈 股票策略篩選器（全功能終極版）")

st.markdown("""
---
**💎 全策略共同核心：股價站上所有均線**
**判斷標準：現價 > 5MA、10MA、20MA、60MA、120MA**

**💰 風險管理設定：**
* **🛑 停損**：實體跌破 5MA
* **🎯 停利**：風險報酬比 **1 : 1.5**

**籌碼數據：**
* 自動抓取 **外資今日** 與 **外資近5日** 買賣超張數。

※ 全策略皆過濾：今日成交量 > 500 張
---
""")

# -------------------------------------------------
# === 外資買超抓取函式 ===
# -------------------------------------------------
@st.cache_data(ttl=3600)
def chip_today(ticker):
    try:
        symbol = ticker.replace(".TW","").replace(".TWO","")
        url = f"https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.institutionalTrading;symbol={symbol}"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
        js = r.json()
        
        if not js["data"]:
            return 0

        d = js["data"][0]
        return d["foreignInvestors"]["buy"] - d["foreignInvestors"]["sell"]

    except:
        return 0


@st.cache_data(ttl=3600)
def chip_5d(ticker):
    try:
        symbol = ticker.replace(".TW","").replace(".TWO","")
        url = f"https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.institutionalTrading;symbol={symbol}"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
        js = r.json()

        if not js["data"]:
            return 0

        data = js["data"][:5]

        tot = 0
        for d in data:
            tot += d["foreignInvestors"]["buy"] - d["foreignInvestors"]["sell"]

        return tot

    except:
        return 0

# -------------------------------------------------
# 股票清單 (回傳 字典: 代碼->名稱)
# -------------------------------------------------
@st.cache_data(ttl=86400)
def get_all_tw_tickers():
    headers = {"User-Agent": "Mozilla/5.0"}
    stock_map = {} 
    
    for mode in ["2", "4"]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            df = pd.read_html(r.text)[0].iloc[1:]
            
            for item in df[0]:
                data = str(item).split()
                if len(data) >= 2:
                    code = data[0]
                    name = data[1]
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
# 輔助：計算風控數據 (1:1.5 RR)
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
# 核心：回測引擎
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        lookback_days = months * 22
        if len(df) < lookback_days + 130: return None

        trades = []
        in_position = False
        entry_price = 0
        
        start_idx = len(df) - lookback_days
        if start_idx < 130: start_idx = 130
        
        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
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
            ma5_curr = ma5.iloc[i]

            if in_position:
                if c_curr < ma5_curr:
                    profit = (c_curr - entry_price) / entry_price
                    trades.append(profit)
                    in_position = False
                continue

            if not (c_curr > ma5_curr and c_curr > ma10.iloc[i] and c_curr > ma20.iloc[i] and 
                    c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i]):
                continue
            
            if volume.iloc[i] < 500_000: continue

            signal = False

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
        
        # 條件檢查
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
            "狀態": "帶量突破 📦"
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
    
    # 嘗試載入名稱對照表
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
                    # 只有當符合策略時，才去抓外資，節省時間
                    r["外資今日(張)"] = chip_today(t)
                    r["外資5日(張)"] = chip_5d(t)
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
                base_cols = ["代號", "名稱", "現價", "外資今日(張)", "外資5日(張)", "停損(5MA)", "停利(1:1.5)"]
                
                if "回測勝率" in df_res.columns:
                    target_cols = base_cols + ["回測勝率", "平均獲利", "總交易"]
                else:
                    target_cols = base_cols
                
                other_cols = [c for c in df_res.columns if c not in target_cols]
                st.dataframe(df_res[target_cols + other_cols], use_container_width=True)
        
        if not has_data:
            st.info("掃描完成，但沒有符合條件的股票。")
