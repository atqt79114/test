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
st.set_page_config(page_title="股票策略篩選器（阿良出版）", layout="wide")
st.title("📈 股票策略篩選器（阿良出版）")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必嚴格執行停損，控制風險。**

---
#### 🧠 策略邏輯解析：
1. **🚀 SMC 箱體突破**：倍量突破日線箱體壓力。
2. **🛡️ SMC 回測支撐**：回踩日線箱體支撐。
3. **🛁 爆量回檔 (洗盤)**：昨日爆量黑K守5MA，今日量縮續守。
4. **📦 日線盤整突破**：日均線糾結帶量突破。
5. **🔥 週線盤整突破 (New!)**：
   * **趨勢**：週 K 線站穩 5、10、20 週均線。
   * **動能**：**本週成交量 > 上週成交量 5 倍** (超級動能)。
   
 * **籌碼**：搭配主力籌碼勝率更高。

 
**💰 風險管理**：停損守 5MA (週線策略守週 5MA)，停利賺賠比 1:1.5。
---
""")

# -------------------------------------------------
# 輔助：產生外資連結
# -------------------------------------------------
def get_chip_link(ticker):
    code = ticker.split('.')[0]
    return f"https://tw.stock.yahoo.com/quote/{code}/institutional-trading"

# -------------------------------------------------
# 股票清單
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
                    # 嚴格限制 4 碼 (排除 ETF)
                    if code.isdigit() and len(code) == 4:
                        suffix = ".TWO" if mode == "4" else ".TW"
                        stock_map[f"{code}{suffix}"] = name
        except Exception: pass
    return stock_map

# -------------------------------------------------
# 核心：批量下載函式
# -------------------------------------------------
def download_batch_data(tickers_batch):
    try:
        data = yf.download(tickers_batch, period="2y", interval="1d", group_by='ticker', progress=False, threads=True)
        result_dict = {}
        
        if len(tickers_batch) == 1:
            t = tickers_batch[0]
            if not data.empty: result_dict[t] = data
            return result_dict

        for t in tickers_batch:
            try:
                df = data[t].copy()
                if df['Close'].isnull().all(): continue
                df = df.dropna(how='all')
                if not df.empty: result_dict[t] = df
            except KeyError: continue
        return result_dict
    except Exception: return {}

# -------------------------------------------------
# 輔助：計算風控數據
# -------------------------------------------------
def calculate_risk_reward(c_now, ma5_now, date_now, timeframe="日"):
    sl_price = round(ma5_now, 2)
    risk = c_now - sl_price
    if risk <= 0: risk = c_now * 0.01 
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
        target_price = 0
        
        start_idx = len(df) - lookback_days
        if start_idx < 130: start_idx = 130
        
        close = df["Close"]; high = df["High"]; volume = df["Volume"]
        ma5 = ta.trend.sma_indicator(close, 5)
        
        # 簡單策略回測通用邏輯
        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]; h_curr = high.iloc[i]; ma5_curr = ma5.iloc[i]

            if in_position:
                if h_curr >= target_price: # 停利
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                if c_curr < ma5_curr: # 停損
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                continue

            # 簡易進場 (這裡僅做通用均線過濾，非完整策略重現，主要用於參考)
            if c_curr > ma5_curr and volume.iloc[i] > 500000:
                in_position = True
                entry_price = c_curr
                risk = entry_price - ma5_curr
                if risk <= 0: risk = entry_price * 0.01
                target_price = entry_price + (risk * 1.5)

        if not trades: return {"回測勝率": "無訊號", "平均獲利": "0%", "總交易": 0}
        win_count = sum(1 for p in trades if p > 0)
        return {
            "回測勝率": f"{round((win_count/len(trades))*100, 1)}%",
            "平均獲利": f"{round((sum(trades)/len(trades))*100, 2)}%",
            "總交易": len(trades)
        }
    except: return None

# -------------------------------------------------
# 策略函式
# -------------------------------------------------
def strategy_smc_breakout(ticker, name, df, backtest_months):
    try:
        if len(df) < 200: return None
        close = df["Close"]; high = df["High"]; volume = df["Volume"]
        if float(volume.iloc[-1]) < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]
        c_now = float(close.iloc[-1])

        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120): return None

        lookback = 40
        resistance = high.iloc[-lookback-1:-1].max()
        if c_now <= resistance: return None
        if float(volume.iloc[-1]) <= float(volume.iloc[-2]) * 2: return None

        rr = calculate_risk_reward(c_now, ma5, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "壓力(BSL)": round(resistance, 2), "成交量(千)": int(float(volume.iloc[-1])/1000), "外資詳情": get_chip_link(ticker), "狀態": "倍量突破 🚀"}
    except: return None

def strategy_smc_support(ticker, name, df, backtest_months):
    try:
        if len(df) < 200: return None
        close = df["Close"]; low = df["Low"]; volume = df["Volume"]
        if float(volume.iloc[-1]) < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]
        c_now = float(close.iloc[-1])

        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120): return None

        support = low.iloc[-40:].min()
        distance = (c_now - support) / support
        if not (-0.02 <= distance <= 0.05): return None
        
        rr = calculate_risk_reward(c_now, ma5, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "支撐(OB)": round(support, 2), "成交量(千)": int(float(volume.iloc[-1])/1000), "外資詳情": get_chip_link(ticker), "狀態": "回測支撐 🛡️"}
    except: return None

def strategy_washout_rebound(ticker, name, df, backtest_months):
    try:
        if len(df) < 125: return None
        close = df["Close"]; open_p = df["Open"]; volume = df["Volume"]
        if float(volume.iloc[-1]) < 500_000: return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_now = float(close.iloc[-1]); ma5_now = ma5.iloc[-1]
        c_prev = float(close.iloc[-2]); o_prev = float(open_p.iloc[-2])
        v_curr = float(volume.iloc[-1]); v_prev = float(volume.iloc[-2]); v_prev_2 = float(volume.iloc[-3])

        if c_prev >= o_prev: return None 
        if v_prev <= v_prev_2: return None 
        if c_prev < ma5.iloc[-2]: return None 
        if c_now < ma5_now: return None 
        if v_curr >= v_prev: return None 

        if not (c_now > ma5_now and c_now > ma10.iloc[-1] and c_now > ma20.iloc[-1] and c_now > ma60.iloc[-1] and c_now > ma120.iloc[-1]): return None

        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, **bt_res, "成交量(千)": int(v_curr/1000), "外資詳情": get_chip_link(ticker), "狀態": "強勢洗盤 🛁"}
    except: return None

def strategy_consolidation(ticker, name, df, backtest_months):
    try:
        if len(df) < 130: return None
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; volume = df["Volume"]
        if float(volume.iloc[-1]) < 500_000: return None

        c_now = float(close.iloc[-1])
        ma5 = ta.trend.sma_indicator(close, 5).iloc[-1]
        ma10 = ta.trend.sma_indicator(close, 10).iloc[-1]
        ma20 = ta.trend.sma_indicator(close, 20).iloc[-1]
        ma60 = ta.trend.sma_indicator(close, 60).iloc[-1]
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]

        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120): return None

        ma_vals = [ma5, ma10, ma20]
        if (max(ma_vals) - min(ma_vals)) / c_now > 0.06: return None

        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None

        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if float(volume.iloc[-1]) < vol_ma5 * 1.5: return None
        if c_now < float(open_p.iloc[-1]): return None

        bt_res = run_backtest(df, "consolidation", backtest_months)
        rr = calculate_risk_reward(c_now, ma5, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, **bt_res, "狀態": "帶量突破 📦", "外資詳情": get_chip_link(ticker)}
    except: return None

# -------------------------------------------------
# 新增策略五：週線盤整突破 (5倍成交量)
# -------------------------------------------------
def strategy_weekly_breakout(ticker, name, df_daily, backtest_months):
    try:
        # 1. 轉換為週線 (Resample)
        # 'W' 代表每週，預設是週日結束，這會包含本週已發生的數據
        df_weekly = df_daily.resample('W').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        })
        
        if len(df_weekly) < 30: return None

        close = df_weekly['Close']
        volume = df_weekly['Volume']

        # 2. 計算週均線
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)

        # 取得最新一週與上一週的數值
        c_now = float(close.iloc[-1])
        v_now = float(volume.iloc[-1])
        v_prev = float(volume.iloc[-2])
        
        ma5_now = ma5.iloc[-1]
        ma10_now = ma10.iloc[-1]
        ma20_now = ma20.iloc[-1]

        # 3. 條件一：站穩週線 5MA, 10MA, 20MA
        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now):
            return None

        # 4. 條件二：本週成交量 > 上週成交量 * 5 (五倍爆量)
        if v_now <= v_prev * 5:
            return None

        # 5. 計算風控 (停損守週 5MA)
        rr = calculate_risk_reward(c_now, ma5_now, df_weekly.index[-1], timeframe="週")

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            **rr,
            "本週量(張)": int(v_now/1000),
            "上週量(張)": int(v_prev/1000),
            "爆量倍數": f"{round(v_now/v_prev, 1)}倍",
            "外資詳情": get_chip_link(ticker),
            "狀態": "週線爆量 🔥"
        }
    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🚀 SMC 箱體突破": strategy_smc_breakout,
    "🛡️ SMC 回測支撐": strategy_smc_support,
    "🛁 爆量回檔（洗盤）": strategy_washout_rebound,
    "📦 盤整突破 (均線糾結)": strategy_consolidation,
    "🔥 週線盤整突破 (爆量5倍)": strategy_weekly_breakout, # 新增
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
    limit = st.sidebar.slider("掃描數量", 50, 2000, 300)
    tickers = list(stock_map.keys())[:limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

st.sidebar.markdown("---")
st.sidebar.header("📊 回測設定")
st.sidebar.caption("※ 回測僅適用於：爆量回檔 & 盤整突破")
backtest_period = st.sidebar.radio("回測區間", [3, 6, 12], format_func=lambda x: f"過去 {x} 個月")

if st.button("開始掃描", type="primary"):
    if not tickers:
        st.error("沒有股票代碼！")
    else:
        result = {k: [] for k in selected}
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        batch_size = 50 
        total_tickers = len(tickers)
        
        for i in range(0, total_tickers, batch_size):
            current_progress = min((i + batch_size) / total_tickers, 1.0)
            progress_bar.progress(current_progress)
            
            batch_tickers = tickers[i : i + batch_size]
            status_text.text(f"正在下載第 {i+1} ~ {min(i+batch_size, total_tickers)} 檔資料...")
            
            data_dict = download_batch_data(batch_tickers)
            if not data_dict:
                time.sleep(1)
                continue

            for t, df in data_dict.items():
                name = stock_map.get(t, t)
                for k in selected:
                    try:
                        r = STRATEGIES[k](t, name, df, backtest_period)
                        if r:
                            r["策略"] = k
                            result[k].append(r)
                    except Exception:
                        continue
            
            time.sleep(0.5)

        progress_bar.empty()
        status_text.empty()
        
        has_data = False
        for k in selected:
            if result[k]:
                has_data = True
                st.subheader(f"📊 {k}")
                df_res = pd.DataFrame(result[k])
                
                # 動態調整欄位，如果是週線策略，顯示爆量倍數
                base_cols = ["代號", "名稱", "現價", "停損(5MA)", "停利(1:1.5)", "外資詳情"]
                if "爆量倍數" in df_res.columns:
                    base_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損(5MA)", "停利(1:1.5)", "外資詳情"]

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
