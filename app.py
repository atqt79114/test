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
st.title("📈 台股強勢策略篩選器 (含週線回測)")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必嚴格執行停損，控制風險。**

---
#### 🧠 策略邏輯解析：

1.  **🌀 布林中線 (量縮黑K)**：
    * **條件**：回測中線 + 黑K + 量縮。
    * **停利**：布林上軌。

2.  **🛁 爆量回檔**：
    * 經典動能策略，需站上 120MA，賺賠比 1:1.5。
    * **乖離率限制**：收盤價距離 5MA 不可超過 **6%**。

3.  **🐂 日線盤整突破 (糾結噴出版)**：
    * **趨勢**：股價 > 60MA 且 > 120MA。
    * **糾結**：5/10/20/60 MA 均線糾結超過 **40天**。
    * **動能**：當日漲幅 > 2% 且 成交量 > 1000張。
    * **風控**：5日乖離率 < 7%。
    * **基本面**：排除 < 10元、排除虧損股 (EPS < 0)。

4.  **🔥 週線盤整突破**：
    * 週線爆量 2.8 倍以上。

5.  **🛡️ 週線回檔守 5MA (熱門股)**：
    * **流動性**：**上週成交量 > 10 萬張**。
    * **趨勢**：股價 > 週線 20MA。
    * **乖離率限制**：**現價與 5MA 乖離不可超過 7%**。

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
        is_weekly = (strategy_type == "weekly_pullback")
        lookback = months * 4 if is_weekly else months * 22
        
        if len(df) < lookback + 20: return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        stop_loss_price = 0
        
        start_idx = len(df) - lookback
        if start_idx < 65: start_idx = 65 
        
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]
        
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10) 
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60) 
        ma120 = ta.trend.sma_indicator(close, 120)
        
        bb20 = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]; h_curr = high.iloc[i]
            
            # === 持倉檢查 ===
            if in_position:
                if h_curr >= target_price: 
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                
                exit_condition = False
                if c_curr < stop_loss_price: exit_condition = True 
                
                if exit_condition:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                
                if strategy_type == "bollinger_mid":
                    target_price = bb20.bollinger_hband().iloc[i]
                continue

            # === 進場訊號 ===
            signal = False
            curr_sl = 0
            curr_tp = 0
            
            if not is_weekly and volume.iloc[i] < 500_000: continue

            # 1. 策略：中線策略
            if strategy_type == "bollinger_mid":
                if c_curr > ma120.iloc[i]:
                    mid = bb20.bollinger_mavg().iloc[i]
                    if abs(c_curr - mid) / mid <= 0.015 and mid > bb20.bollinger_mavg().iloc[i-1]:
                        if c_curr < open_p.iloc[i] and volume.iloc[i] < volume.iloc[i-1]:
                            signal = True
                            curr_sl = mid * 0.97
                            curr_tp = bb20.bollinger_hband().iloc[i]

            # 2. 策略：洗盤
            elif strategy_type == "washout":
                if c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i]:
                    if close.iloc[i-1] < ma5.iloc[i-1] and c_curr > ma5.iloc[i]:
                         if c_curr > open_p.iloc[i] and volume.iloc[i] > volume.iloc[i-1]:
                            if (c_curr - ma5.iloc[i]) / ma5.iloc[i] < 0.08:
                                signal = True
                                curr_sl = ma20.iloc[i] 
                                curr_tp = c_curr * 1.15

            # 3. 策略：盤整突破 (回測使用簡易邏輯)
            elif strategy_type == "consolidation":
                 # 這裡只做簡易回測，複雜的糾結計算在實時掃描做
                 if c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i]:
                      if (c_curr - open_p.iloc[i])/open_p.iloc[i] > 0.02 and volume.iloc[i] > volume.iloc[i-1]:
                          signal = True
                          curr_sl = ma5.iloc[i] * 0.95
                          curr_tp = c_curr * 1.2

            # 4. 策略：週線回檔
            elif strategy_type == "weekly_pullback":
                c_prev = close.iloc[i-1]; o_prev = open_p.iloc[i-1]; v_prev = volume.iloc[i-1]
                h_prev = high.iloc[i-1]
                if v_prev < 100000 * 1000: continue
                if c_curr < ma20.iloc[i]: continue
                if not (c_prev > o_prev and c_prev > ma5.iloc[i-1]): continue
                if c_curr < open_p.iloc[i] and volume.iloc[i] < v_prev and c_curr > ma5.iloc[i]:
                    signal = True
                    curr_sl = ma5.iloc[i] * 0.98 
                    curr_tp = h_prev 

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
    except Exception as e: 
        return None

# -------------------------------------------------
# 策略函式
# -------------------------------------------------

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
        
        if abs(c_now - mid_now) / mid_now > 0.015: return None 
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
        
        # 乖離率過濾
        bias_5 = ((c_now - ma5_now) / ma5_now) * 100
        if bias_5 > 4: return None

        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now, df.index[-1])
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "5日乖離率": f"{round(bias_5, 2)}%", 
            **rr, 
            **(bt_res or {}), 
            "外資詳情": get_chip_link(ticker), 
            "狀態": "強勢洗盤 🛁"
        }
    except: return None

# =================================================
#  🎯 修改後的策略：日線盤整突破 (糾結 + 噴出)
# =================================================
def strategy_consolidation(ticker, name, df, backtest_months):
    try:
        # --- 0. 資料長度檢查 (需要 120MA + 40天糾結判定) ---
        if len(df) < 165: return None
        
        close = df["Close"]
        volume = df["Volume"]
        
        c_now = float(close.iloc[-1])
        c_prev = float(close.iloc[-2])
        v_now = float(volume.iloc[-1])
        
        # --- 1. 排除低價股 (要求5) ---
        if c_now < 10: return None

        # --- 2. 當日成交量 > 1000 張 (要求2) ---
        if v_now < 1000 * 1000: return None

        # --- 3. 當日漲幅 > 2% (要求3) ---
        pct_change = (c_now - c_prev) / c_prev
        if pct_change <= 0.02: return None

        # --- 準備技術指標 ---
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        # --- 4. 股價要在 60MA 與 120MA 之上 (要求1) ---
        if not (c_now > ma60.iloc[-1] and c_now > ma120.iloc[-1]): return None

        # --- 5. 乖離率 bias 5T 不超過 7% (要求4) ---
        bias_5 = ((c_now - ma5.iloc[-1]) / ma5.iloc[-1]) * 100
        if bias_5 > 7: return None

        # --- 6. [核心] 均線糾結大於 40 天 (要求7) ---
        # 邏輯：檢查過去40天 (不含今天)，5/10/20/60 MA 的「最大差距幅度」是否都在 15% 以內
        
        # 取得過去40天的均線數據 (slice: -41 到 -1)
        lookback_slice = slice(-41, -1)
        
        # 建立一個臨時 DataFrame 方便計算
        ma_df = pd.DataFrame({
            '5': ma5.iloc[lookback_slice],
            '10': ma10.iloc[lookback_slice],
            '20': ma20.iloc[lookback_slice],
            '60': ma60.iloc[lookback_slice]
        })
        
        # 計算每天的 (最大均線 - 最小均線) / 最小均線
        ma_max = ma_df.max(axis=1)
        ma_min = ma_df.min(axis=1)
        bandwidth = (ma_max - ma_min) / ma_min
        
        # 如果過去 40 天中，有任何一天的均線發散程度超過 15% (0.15)，則不符合糾結定義
        # (通常糾結會在 5%~10%，這邊設 15% 是為了包含 60MA，避免條件太嚴苛)
        if (bandwidth > 0.15).any(): return None

        # --- 7. 排除虧損股：近四季盈餘 < 0 (要求6) ---
        # 放在最後以節省時間
        try:
            stock_info = yf.Ticker(ticker).info
            eps = stock_info.get('trailingEps')
            if eps is None or eps < 0: 
                return None
        except Exception:
            pass 

        # --- 通過所有篩選 ---
        bt_res = run_backtest(df, "consolidation", backtest_months)
        
        sl_price = ma5.iloc[-1]
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1])
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "5日乖離率": f"{round(bias_5, 2)}%",
            "漲幅": f"{round(pct_change*100, 2)}%",
            "EPS": eps if 'eps' in locals() and eps is not None else "N/A",
            **rr, 
            **(bt_res or {}), 
            "狀態": "糾結噴出 🐂", 
            "外資詳情": get_chip_link(ticker)
        }
    except Exception as e:
        return None

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
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "回測勝率": "N/A", "平均獲利": "-", "總交易": "-", "本週量(張)": int(v_now/1000), "爆量倍數": f"{round(v_now/v_prev, 1)}倍", "外資詳情": get_chip_link(ticker), "狀態": "週線爆量 🔥"}
    except: return None

def strategy_weekly_pullback(ticker, name, df_daily, backtest_months):
    try:
        df_weekly = df_daily.resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
        if len(df_weekly) < 40: return None
        close = df_weekly['Close']; open_p = df_weekly['Open']; high = df_weekly['High']; volume = df_weekly['Volume']

        ma5 = ta.trend.sma_indicator(close, 5)
        ma20 = ta.trend.sma_indicator(close, 20)

        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1]); v_now = float(volume.iloc[-1])
        ma5_now = float(ma5.iloc[-1]); ma20_now = float(ma20.iloc[-1])
        c_prev = float(close.iloc[-2]); o_prev = float(open_p.iloc[-2]); h_prev = float(high.iloc[-2]); v_prev = float(volume.iloc[-2])
        ma5_prev = float(ma5.iloc[-2])

        if v_prev < 100000 * 1000: return None
        if c_now < ma20_now: return None
        if not (c_prev > o_prev and c_prev > ma5_prev): return None
        if not (c_now < o_now): return None
        if not (v_now < v_prev): return None
        if not (c_now > ma5_now): return None

        bias_5t = ((c_now - ma5_now) / ma5_now) * 100
        if bias_5t > 7: return None

        bt_res = run_backtest(df_weekly, "weekly_pullback", backtest_months)
        sl_price = ma5_now
        tp_price = h_prev 
        rr = calculate_risk_reward(c_now, sl_price, df_weekly.index[-1], custom_target=tp_price)
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "5週乖離率": f"{round(bias_5t, 2)}%",
            **rr,
            **(bt_res or {}),
            "本週量(張)": int(v_now/1000),
            "上週量(張)": int(v_prev/1000),
            "外資詳情": get_chip_link(ticker), 
            "狀態": "週線回檔守5MA 🛡️"
        }
    except Exception: return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "🌀 布林中線 (量縮黑K)": strategy_bollinger_mid,
    "🛁 爆量回檔 (洗盤)": strategy_washout_rebound,
    "🐂 日線盤整突破 (糾結噴出)": strategy_consolidation,
    "🔥 週線盤整突破 (爆量2.8倍)": strategy_weekly_breakout,
    "🛡️ 週線回檔守 5MA (New!)": strategy_weekly_pullback, 
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
backtest_period = st.sidebar.selectbox(
    "回測區間 (月)", 
    [3, 6, 9, 12, 24], 
    format_func=lambda x: f"過去 {x} 個月"
)

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
                
                if "布林中線" in df_res.columns or "布林中線(10MA)" in df_res.columns:
                      if "布林下軌" in df_res.columns: 
                          target_cols = ["代號", "名稱", "現價", "布林下軌", "布林中線(10MA)", "停損價(SL)", "停利價(TP)", "外資詳情"]
                      else: 
                          target_cols = ["代號", "名稱", "現價", "布林中線", "布林上軌", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "上週量(張)" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "5週乖離率", "本週量(張)", "上週量(張)", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "5日乖離率" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "漲幅", "5日乖離率", "EPS", "停損價(SL)", "停利價(TP)", "外資詳情"]
                else:
                    target_cols = base_cols
                
                final_cols = [c for c in target_cols if c in df_res.columns]
                
                if "回測勝率" in df_res.columns:
                    final_cols += ["回測勝率", "平均獲利", "總交易"]
                
                other_cols = [c for c in df_res.columns if c not in final_cols and c not in target_cols]
                
                st.dataframe(
                    df_res[final_cols + other_cols], 
                    use_container_width=True,
                    column_config={
                        "外資詳情": st.column_config.LinkColumn(
                            "外資詳情", display_text="查看數據"
                        )
                    }
                )
        if not has_data:
            st.info("掃描完成，但沒有符合條件的股票。")
