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
st.title("📈 台股強勢策略篩選器")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必嚴格執行停損，控制風險。**

---
#### 🧱 硬性濾網：股價必須站上 120MA (半年線)
所有日線策略皆已加入此條件，確保股票處於 **「長期多頭趨勢」**。

#### 🧠 策略邏輯解析：

1.  **📉 布林下軌 (穿越站回) [新]**：
    * **定義**：價格由下向上穿越下軌線。
    * **條件**：(昨日收盤破下軌 OR 今日開盤破下軌) + **今日收盤站上下軌** + **紅K** + **量縮**。
    * **意義**：超賣後的強勢回歸訊號。
    * **停損**：**當日紅K最低點**。
    * **停利**：布林中線 (20MA)。

2.  **🌀 布林中線 (量縮黑K)**：
    * **邏輯**：回檔至中線支撐，量縮整理。
    * **停利**：布林上軌。

3.  **🛁 爆量回檔** & **📦 盤整突破** & **🔥 週線爆量**：
    * 經典動能策略。

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
# 輔助：計算風控數據 (支援自訂停利價)
# -------------------------------------------------
def calculate_risk_reward(c_now, sl_price, date_now, custom_target=None):
    sl_price = round(sl_price, 2)
    risk = c_now - sl_price
    if risk <= 0: risk = c_now * 0.01 
    
    if custom_target:
        target_price = round(custom_target, 2)
        potential_profit = (target_price - c_now) / c_now
    else:
        target_price = round(c_now + (risk * 1.5), 2) 
        potential_profit = (risk * 1.5) / c_now
    
    return {
        "訊號日期": date_now.strftime('%Y-%m-%d'),
        "停損價(SL)": sl_price,
        "停利價(TP)": target_price,
        "潛在獲利": f"{round(potential_profit*100, 1)}%"
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
        stop_loss_price = 0
        
        start_idx = len(df) - lookback_days
        if start_idx < 130: start_idx = 130
        
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]
        
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        
        vol_ma5 = volume.rolling(5).mean()
        
        indicator_bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        bb_mavg = indicator_bb.bollinger_mavg()
        bb_hband = indicator_bb.bollinger_hband()
        bb_lband = indicator_bb.bollinger_lband()

        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]; h_curr = high.iloc[i]; l_curr = low.iloc[i]
            o_curr = open_p.iloc[i]; v_curr = volume.iloc[i]
            v_prev = volume.iloc[i-1]
            
            # 持倉檢查
            if in_position:
                if h_curr >= target_price: 
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                
                sl_trigger = stop_loss_price
                if c_curr < sl_trigger:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                
                # 動態停利更新
                if strategy_type == "bollinger_mid":
                    target_price = bb_hband.iloc[i]
                elif "bollinger_lower" in strategy_type:
                    target_price = bb_mavg.iloc[i]
                
                continue

            signal = False
            curr_sl = 0
            curr_tp = 0

            # === 策略邏輯 ===
            
            # 1. 📉 布林下軌 (穿越站回) [新邏輯]
            if strategy_type == "bollinger_lower_cross":
                c_prev = close.iloc[i-1]
                lower_curr = bb_lband.iloc[i]
                lower_prev = bb_lband.iloc[i-1]
                
                # A. 趨勢與量能: >120MA, 量 > 500, 量縮
                if c_curr > ma120.iloc[i] and v_curr > 500_000 and v_curr < v_prev:
                    # B. 穿越型態: 
                    #    昨日收盤破下軌 OR 今日開盤破下軌 (代表起點在下方)
                    #    AND 今日收盤站上下軌 (代表終點在上方)
                    if (c_prev < lower_prev or o_curr < lower_curr) and c_curr > lower_curr:
                        # C. 紅K確認
                        if c_curr > o_curr:
                            signal = True
                            curr_sl = l_curr # 守紅K低點
                            curr_tp = bb_mavg.iloc[i]

            # 2. 🌀 布林通道中線 (量縮 + 黑K)
            elif strategy_type == "bollinger_mid":
                mid = bb_mavg.iloc[i]
                if abs(c_curr - mid) / mid <= 0.015 and c_curr > ma120.iloc[i] and v_curr > 500_000:
                    if c_curr < o_curr: # 黑K
                        if v_curr < v_prev: # 量縮
                            signal = True
                            curr_sl = mid * 0.97
                            curr_tp = bb_hband.iloc[i]

            # 3. 其他策略
            elif (c_curr > ma5.iloc[i] and c_curr > ma10.iloc[i] and c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i]):
                if v_curr > 500_000 and c_curr > ma120.iloc[i]:
                    if strategy_type == "washout":
                        c_prev = close.iloc[i-1]; o_prev = open_p.iloc[i-1]
                        v_prev_2 = volume.iloc[i-2]
                        if (c_prev < o_prev) and (v_prev > v_prev_2) and (c_prev >= ma5.iloc[i-1]) and \
                           (v_curr < v_prev) and (c_curr >= ma5.iloc[i]):
                            signal = True; curr_sl = ma5.iloc[i]; curr_tp = c_curr + (c_curr - curr_sl) * 1.5
                    
                    elif strategy_type == "consolidation":
                        res = high.iloc[i-21:i].max() 
                        if c_curr > res and v_curr > vol_ma5.iloc[i-1] * 1.5:
                            signal = True; curr_sl = ma5.iloc[i]; curr_tp = c_curr + (c_curr - curr_sl) * 1.5

            if signal:
                in_position = True
                entry_price = c_curr
                stop_loss_price = curr_sl
                target_price = curr_tp

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

# [新策略] 下軌穿越 (由下向上)
def strategy_bollinger_lower_cross(ticker, name, df, backtest_months):
    try:
        if len(df) < 125: return None
        close = df["Close"]; open_p = df["Open"]; volume = df["Volume"]; low = df["Low"]
        
        # 今日
        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1])
        l_now = float(low.iloc[-1]); v_now = float(volume.iloc[-1])
        
        # 昨日
        c_prev = float(close.iloc[-2]); v_prev = float(volume.iloc[-2])
        
        # 1. 濾網
        if v_now < 500_000: return None
        if v_now >= v_prev: return None # 量縮
        if c_now < ta.trend.sma_indicator(close, 120).iloc[-1]: return None # 120MA
        
        # 2. 布林
        indicator_bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        lower_now = float(indicator_bb.bollinger_lband().iloc[-1])
        lower_prev = float(indicator_bb.bollinger_lband().iloc[-2])
        mid_now = float(indicator_bb.bollinger_mavg().iloc[-1])
        
        # 3. 核心邏輯：穿越 (Cross Up)
        # 條件A: "起點在下" -> 昨日收盤破下軌 OR 今日開盤破下軌
        start_below = (c_prev < lower_prev) or (o_now < lower_now)
        
        # 條件B: "終點在上" -> 今日收盤 > 下軌
        end_above = c_now > lower_now
        
        if not (start_below and end_above): return None
        
        # 條件C: 必須紅K (確認買盤)
        if c_now <= o_now: return None

        bt_res = run_backtest(df, "bollinger_lower_cross", backtest_months)
        
        sl_price = l_now # 守低點
        target_price = mid_now
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1], custom_target=target_price)
        
        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2), 
            "布林下軌": round(lower_now, 2), 
            "布林中線": round(mid_now, 2),
            **rr, **(bt_res or {}), 
            "外資詳情": get_chip_link(ticker), 
            "狀態": "穿越站回 📈"
        }
    except Exception: return None

# 中線量縮 + 黑K
def strategy_bollinger_mid(ticker, name, df, backtest_months):
    try:
        if len(df) < 125: return None
        close = df["Close"]; open_p = df["Open"]; volume = df["Volume"]
        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1])
        v_now = float(volume.iloc[-1]); v_prev = float(volume.iloc[-2])
        
        if v_now < 500_000: return None
        if c_now < ta.trend.sma_indicator(close, 120).iloc[-1]: return None 
        
        indicator_bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        bb_mavg = indicator_bb.bollinger_mavg()
        bb_hband = indicator_bb.bollinger_hband()
        mid_now = float(bb_mavg.iloc[-1])
        upper_now = float(bb_hband.iloc[-1]) 
        
        if abs(c_now - mid_now) / mid_now > 0.01: return None 
        if mid_now < float(bb_mavg.iloc[-2]): return None 
        
        if c_now >= o_now: return None 
        if v_now >= v_prev: return None 

        bt_res = run_backtest(df, "bollinger_mid", backtest_months)
        
        sl_price = mid_now * 0.97
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1], custom_target=upper_now)
        
        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2), 
            "布林中線": round(mid_now, 2), 
            "布林上軌": round(upper_now, 2),
            **rr, **(bt_res or {}), 
            "外資詳情": get_chip_link(ticker), 
            "狀態": "中線黑K量縮 🌀"
        }
    except Exception: return None

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
        
        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now): return None
        if v_now <= v_prev * 2.8: return None
        
        rr = calculate_risk_reward(c_now, ma5_now, df_weekly.index[-1])
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "本週量(張)": int(v_now/1000), "爆量倍數": f"{round(v_now/v_prev, 1)}倍", "外資詳情": get_chip_link(ticker), "狀態": "週線爆量 🔥"}
    except: return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "📉 布林下軌 (穿越站回)": strategy_bollinger_lower_cross,
    "🌀 布林中線 (量縮黑K)": strategy_bollinger_mid,
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
    raw = st.sidebar.text_area("股票代碼", "2330.TW, 2317.TW, 2603.TW")
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
                
                # 欄位顯示名稱更新
                base_cols = ["代號", "名稱", "現價", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                # 針對不同策略顯示不同輔助欄位
                if "布林中線" in df_res.columns:
                     if "布林下軌" in df_res.columns: # 下軌策略
                         target_cols = ["代號", "名稱", "現價", "布林下軌", "布林中線", "停損價(SL)", "停利價(TP)", "外資詳情"]
                     else: # 中線策略
                         target_cols = ["代號", "名稱", "現價", "布林中線", "布林上軌", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損價(SL)", "停利價(TP)", "外資詳情"]
                else:
                    target_cols = base_cols
                
                if "回測勝率" in df_res.columns:
                    target_cols += ["回測勝率", "平均獲利", "總交易"]
                
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
