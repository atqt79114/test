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
st.set_page_config(page_title="台股強勢策略篩選器", layout="wide")
st.title("📈 台股強勢策略篩選器（量能與型態版）")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必嚴格執行停損，控制風險。**

---
#### 💎 全策略共同核心：股價站上所有均線 (多頭排列)
* **定義**：現價 > 5MA、10MA、20MA、60MA、120MA
* **意義**：代表股價高於過去半年所有人的平均成本，上方無套牢賣壓，是強勢股的標準特徵。

#### 🧠 策略邏輯解析：

1. **🛁 爆量回檔 (洗盤)**：
   * **邏輯**：主力拉抬後故意不做價，讓短線客下車。
   * **特徵**：昨日爆量收黑或長上影線（看似轉弱），但今日量縮且股價守住關鍵均線。

2. **📦 日線盤整突破**：
   * **邏輯**：長時間的箱體整理後，出現帶量長紅突破壓力區。
   * **特徵**：均線糾結後發散，成交量放大至 5日均量的 1.5 倍以上。

3. **🔥 週線盤整突破 (大波段)**：
   * **邏輯**：從週線格局看趨勢，過濾掉日線的雜訊。
   * **特徵**：週 K 線站穩中長期週均線，且**本週(預估)成交量 > 上週成交量 2.8 倍** (攻擊訊號)。

**💰 風險管理**：
* **停損設定**：建議守 5MA 或 10MA，或進場 K 棒低點。
* **停利設定**：初步建議賺賠比 1:1，或沿 5MA 移動停利。
* **基本過濾**：當日成交量 > 500 張。
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
def calculate_risk_reward(c_now, sl_price, date_now):
    sl_price = round(sl_price, 2)
    risk = c_now - sl_price
    if risk <= 0: risk = c_now * 0.01 
    target_price = round(c_now + (risk * 1.0), 2) # 1:1
    
    return {
        "訊號日期": date_now.strftime('%Y-%m-%d'),
        "停損價(SL)": sl_price,
        "停利價(1:1)": target_price,
        "潛在獲利": f"{round((risk * 1.0 / c_now)*100, 1)}%"
    }

# -------------------------------------------------
# 核心：回測引擎 (已移除 SMC 邏輯)
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        lookback_days = months * 22
        if len(df) < lookback_days + 130: return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        stop_loss_price = 0
        
        start_idx = len(df) - lookback_days
        if start_idx < 130: start_idx = 130
        
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; volume = df["Volume"]
        
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        vol_ma5 = volume.rolling(5).mean()

        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]; h_curr = high.iloc[i]; ma5_curr = ma5.iloc[i]

            # 持倉檢查
            if in_position:
                if h_curr >= target_price: 
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                
                # 一般策略守 5MA 或指定 SL
                sl_trigger = ma5_curr
                if c_curr < sl_trigger:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                continue

            # 基本多頭排列過濾
            if not (c_curr > ma5_curr and c_curr > ma10.iloc[i] and c_curr > ma20.iloc[i] and 
                    c_curr > ma60.iloc[i]):
                continue
            
            signal = False
            curr_sl = 0

            # === 策略邏輯 (SMC 已移除) ===
            # 基本成交量過濾
            if volume.iloc[i] > 500_000:
                
                if strategy_type == "washout":
                    c_prev = close.iloc[i-1]; o_prev = open_p.iloc[i-1]
                    v_prev = volume.iloc[i-1]; v_prev_2 = volume.iloc[i-2]
                    ma5_prev = ma5.iloc[i-1]
                    # 昨日跌或黑K + 昨日爆量 + 今日量縮 + 今日站穩均線
                    if (c_prev < o_prev) and (v_prev > v_prev_2) and (c_prev >= ma5_prev) and \
                       (volume.iloc[i] < v_prev) and (c_curr >= ma5_curr):
                        signal = True; curr_sl = ma5_curr
                
                elif strategy_type == "consolidation":
                    res = high.iloc[i-21:i].max() # 過去20日高點
                    # 突破 + 爆量 (1.5倍均量)
                    if c_curr > res and volume.iloc[i] > vol_ma5.iloc[i-1] * 1.5:
                        signal = True; curr_sl = ma5_curr

            if signal:
                in_position = True
                entry_price = c_curr
                stop_loss_price = curr_sl
                risk = entry_price - stop_loss_price
                if risk <= 0: risk = entry_price * 0.01
                target_price = entry_price + (risk * 1.0)

        if not trades: return {"回測勝率": "無訊號", "平均獲利": "0%", "總交易": 0}
        win_count = sum(1 for p in trades if p > 0)
        return {
            "回測勝率": f"{round((win_count/len(trades))*100, 1)}%",
            "平均獲利": f"{round((sum(trades)/len(trades))*100, 2)}%",
            "總交易": len(trades)
        }
    except: return None

# -------------------------------------------------
# 策略函式 (已移除 SMC)
# -------------------------------------------------

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
        
        # 洗盤邏輯：昨天黑K且量增，今天量縮但價穩
        if c_prev >= o_prev: return None 
        if v_prev <= v_prev_2: return None 
        if c_prev < ma5.iloc[-2]: return None 
        if c_now < ma5_now: return None 
        if v_curr >= v_prev: return None 
        if not (c_now > ma5_now and c_now > ma10.iloc[-1] and c_now > ma20.iloc[-1] and c_now > ma60.iloc[-1] and c_now > ma120.iloc[-1]): return None
        
        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, **(bt_res or {}), "外資詳情": get_chip_link(ticker), "狀態": "強勢洗盤 🛁"}
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
        
        # 多頭排列
        if not (c_now > ma5 and c_now > ma10 and c_now > ma20 and c_now > ma60 and c_now > ma120): return None
        
        # 均線糾結 (5, 10, 20MA 差異不大)
        ma_vals = [ma5, ma10, ma20]
        if (max(ma_vals) - min(ma_vals)) / c_now > 0.06: return None
        
        # 突破過去20天高點
        resistance = float(high.iloc[:-1].tail(20).max())
        if c_now <= resistance: return None
        
        # 爆量
        vol_ma5 = float(volume.rolling(5).mean().iloc[-2])
        if float(volume.iloc[-1]) < vol_ma5 * 1.5: return None
        if c_now < float(open_p.iloc[-1]): return None # 收紅
        
        bt_res = run_backtest(df, "consolidation", backtest_months)
        rr = calculate_risk_reward(c_now, ma5, df.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, **(bt_res or {}), "狀態": "帶量突破 📦", "外資詳情": get_chip_link(ticker)}
    except: return None

def strategy_weekly_breakout(ticker, name, df_daily, backtest_months):
    try:
        df_weekly = df_daily.resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
        if len(df_weekly) < 30: return None
        close = df_weekly['Close']; volume = df_weekly['Volume']
        ma5 = ta.trend.sma_indicator(close, 5); ma10 = ta.trend.sma_indicator(close, 10); ma20 = ta.trend.sma_indicator(close, 20)
        c_now = float(close.iloc[-1]); v_now = float(volume.iloc[-1]); v_prev = float(volume.iloc[-2])
        ma5_now = ma5.iloc[-1]; ma10_now = ma10.iloc[-1]; ma20_now = ma20.iloc[-1]
        
        # 站穩週均線
        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now): return None
        
        # 爆量 2.8 倍
        if v_now <= v_prev * 2.8: return None
        
        rr = calculate_risk_reward(c_now, ma5_now, df_weekly.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "本週量(張)": int(v_now/1000), "爆量倍數": f"{round(v_now/v_prev, 1)}倍", "外資詳情": get_chip_link(ticker), "狀態": "週線爆量 🔥"}
    except: return None

# -------------------------------------------------
# 策略集合 (已更新)
# -------------------------------------------------
STRATEGIES = {
    "🛁 爆量回檔 (洗盤)": strategy_washout_rebound,
    "📦 日線盤整突破": strategy_consolidation,
    "🔥 週線盤整突破 (爆量2.8倍)": strategy_weekly_breakout,
}

# -------------------------------------------------
# UI 介面
# -------------------------------------------------
st.sidebar.header("股票來源")
source = st.sidebar.radio("選擇", ["手動", "全市場"])

if source == "手動":
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2454.TW")
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
st.sidebar.caption("※ 回測僅適用日線策略")
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
                
                # 簡化後的欄位顯示邏輯 (移除OB相關欄位)
                base_cols = ["代號", "名稱", "現價", "停損價(SL)", "停利價(1:1)", "外資詳情"]
                
                if "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損價(SL)", "停利價(1:1)", "外資詳情"]
                elif "回測勝率" in df_res.columns:
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
