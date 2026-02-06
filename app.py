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
st.set_page_config(page_title="台股潛伏策略篩選器", layout="wide")
st.title("💤 台股潛伏/糾結策略篩選器 (進階版)")

# === 核心：詳細策略邏輯與免責聲明 ===
st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必搭配基本面與籌碼面判斷。**

---
#### 🧠 策略邏輯解析：

1.  **⚡ 強勢回測 5MA (底底高) [NEW]**：
    * **趨勢**：股價 > 120MA，且呈現「底底高」型態 (今日低點 > 昨日低點)。
    * **洗盤**：盤中回測跌破 5MA。
    * **訊號**：收盤強勢站回 5MA 之上，代表多頭趨勢極強，回檔即買點。

2.  **🌀 布林中線 (量縮黑K)**：
    * **條件**：回測中線 + 黑K + 量縮。

3.  **🛁 爆量回檔**：
    * 經典動能策略，需站上 120MA，乖離率限制 6%。

4.  **🕸️ 日線極度糾結 (潛伏版)**：
    * **核心**：5/10/20/60 MA 四條均線黏合 (寬度 < 4%)。

5.  **🔥 週線盤整突破**：
    * 週線爆量 2.8 倍以上。

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
    if not tickers_batch: return {}
    try:
        # auto_adjust=False 確保數據原始性
        data = yf.download(tickers_batch, period="2y", interval="1d", group_by='ticker', progress=False, threads=True, auto_adjust=False)
        result_dict = {}
        
        if len(tickers_batch) == 1:
            t = tickers_batch[0]
            if not data.empty and len(data) > 0:
                result_dict[t] = data
            return result_dict

        for t in tickers_batch:
            try:
                if t in data.columns.levels[0]:
                    df = data[t].copy()
                    if df['Close'].isnull().all(): continue
                    df = df.dropna(how='all')
                    if not df.empty: 
                        result_dict[t] = df
            except Exception: continue
            
        return result_dict
    except Exception as e:
        return {}

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
        target_price = round(c_now + (risk * 2.0), 2)
        potential_profit = (risk * 2.0) / c_now
    
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
        is_weekly = (strategy_type == "weekly_breakout") 
        lookback = months * 4 if is_weekly else months * 22
        
        if len(df) < lookback + 20: return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        stop_loss_price = 0
        
        start_idx = len(df) - lookback
        if start_idx < 125: start_idx = 125 
        
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]
        
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10) 
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60) 
        ma120 = ta.trend.sma_indicator(close, 120)
        
        bb20 = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        for i in range(start_idx, len(df) - 1):
            c_curr = float(close.iloc[i]); h_curr = float(high.iloc[i]); l_curr = float(low.iloc[i])
            
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

            # 1. 策略：中線
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

            # 3. 策略：極度糾結
            elif strategy_type == "consolidation":
                 ma_max = max(ma5.iloc[i], ma10.iloc[i], ma20.iloc[i], ma60.iloc[i])
                 ma_min = min(ma5.iloc[i], ma10.iloc[i], ma20.iloc[i], ma60.iloc[i])
                 if ma_min == 0: continue
                 bw = (ma_max - ma_min)/ma_min
                 if bw < 0.05 and c_curr > ma_max and volume.iloc[i] > volume.iloc[i-1]:
                        signal = True
                        curr_sl = ma_min * 0.96
                        curr_tp = c_curr * 1.15

            # 5. 策略：強勢回測 (底底高)
            elif strategy_type == "strong_trend_ma5":
                if c_curr < 20: continue
                if c_curr < ma120.iloc[i]: continue
                
                l_prev = float(low.iloc[i-1])
                ma5_curr = float(ma5.iloc[i])
                
                # 條件：今日低 > 昨日低 AND 今日低 < 5MA AND 收盤 > 5MA
                if l_curr > l_prev and l_curr < ma5_curr and c_curr > ma5_curr:
                    signal = True
                    curr_sl = l_curr 
                    curr_tp = c_curr * 1.1

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
        
        bias_5 = ((c_now - ma5_now) / ma5_now) * 100
        if bias_5 > 4: return None
        
        pct_change = (c_now - c_prev) / c_prev * 100
        eps = "N/A" 

        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now, df.index[-1])
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "漲幅": f"{round(pct_change, 2)}%",
            "5日乖離率": f"{round(bias_5, 2)}%", 
            "EPS": eps,
            **rr, 
            **(bt_res or {}), 
            "外資詳情": get_chip_link(ticker), 
            "狀態": "強勢洗盤 🛁"
        }
    except: return None

def strategy_consolidation_latent(ticker, name, df, backtest_months):
    try:
        if len(df) < 120: return None
        close = df["Close"]; volume = df["Volume"]
        c_now = float(close.iloc[-1]); v_now = float(volume.iloc[-1])

        if c_now < 10: return None
        if v_now < 1_000_000: return None 
        if ticker.startswith("28"): return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)

        ma5_now = ma5.iloc[-1]; ma10_now = ma10.iloc[-1]
        ma20_now = ma20.iloc[-1]; ma60_now = ma60.iloc[-1]

        all_mas = [ma5_now, ma10_now, ma20_now, ma60_now]
        ma_max = max(all_mas); ma_min = min(all_mas)
        
        if ma_min == 0: return None
        bandwidth = (ma_max - ma_min) / ma_min
        if bandwidth > 0.04: return None

        c_prev = float(close.iloc[-2])
        if c_prev == 0: return None
        
        pct_change = abs((c_now - c_prev) / c_prev)
        if pct_change > 0.03: return None

        bias_20 = abs((c_now - ma20_now) / ma20_now)
        if bias_20 > 0.02: return None

        stock_sector = "N/A"
        try:
             # stock_sector = yf.Ticker(ticker).info.get('sector', 'N/A')
             pass
        except: pass
        
        sl_price = ma_min * 0.96
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1])
        bt_res = run_backtest(df, "consolidation", backtest_months)

        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "產業族群": stock_sector, "糾結度": f"{round(bandwidth*100, 2)}%", 
            "乖離率": f"{round(bias_20*100, 2)}%",
            **rr, **(bt_res or {}),
            "狀態": "均線黏合潛伏中 🕸️", "外資詳情": get_chip_link(ticker)
        }
    except Exception: return None

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

def strategy_strong_trend_ma5(ticker, name, df, backtest_months):
    try:
        if len(df) < 130:
            return None

        close = df["Close"]
        low = df["Low"]
        volume = df["Volume"]

        c_now = float(close.iloc[-1])
        l_now = float(low.iloc[-1])
        l_prev = float(low.iloc[-2])
        v_now = float(volume.iloc[-1])

        # ========= 基本濾網 =========
        if v_now < 1_000_000:     # 至少 1000 張
            return None
        if c_now <= 20:           # 排除低價股
            return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now = float(ma5.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # 長線多頭
        if c_now <= ma120_now:
            return None

        # ========= 核心 K 棒條件 =========
        # 1️⃣ 底底高
        cond_higher_low = l_now > l_prev

        # 2️⃣ 盤中跌破 5MA（用最低點判斷）
        cond_intraday_break = l_now < ma5_now

        # 3️⃣ 收盤拉回 5MA 上方（避免剛好貼線）
        cond_reclaim_ma5 = c_now > ma5_now * 1.001

        if cond_higher_low and cond_intraday_break and cond_reclaim_ma5:

            bt_res = run_backtest(df, "strong_trend_ma5", backtest_months)

            # 停損：今日低點（強勢股不該再破）
            sl_price = l_now
            rr = calculate_risk_reward(c_now, sl_price, df.index[-1])

            return {
                "代號": ticker,
                "名稱": name,
                "現價": round(c_now, 2),
                "今日低點": round(l_now, 2),
                "昨日低點": round(l_prev, 2),
                "5MA": round(ma5_now, 2),
                **rr,
                **(bt_res or {}),
                "外資詳情": get_chip_link(ticker),
                "狀態": "強勢回測 5MA（底底高洗盤）⚡"
            }

        return None

    except Exception:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "⚡ 強勢回測 5MA (底底高)": strategy_strong_trend_ma5, # 新增
    "🌀 布林中線 (量縮黑K)": strategy_bollinger_mid,
    "🛁 爆量回檔 (洗盤)": strategy_washout_rebound,
    "🕸️ 日線極度糾結 (潛伏中)": strategy_consolidation_latent,
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
                
                if "布林中線" in df_res.columns:
                     target_cols = ["代號", "名稱", "現價", "布林中線", "布林上軌", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "糾結度" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "產業族群", "糾結度", "乖離率", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "今日低點" in df_res.columns:
                     target_cols = ["代號", "名稱", "現價", "今日低點", "5MA", "停損價(SL)", "停利價(TP)", "外資詳情"]
                elif "5日乖離率" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "漲幅", "5日乖離率", "EPS", "停損價(SL)", "停利價(TP)", "外資詳情"]
                else:
                    target_cols = base_cols
                
                final_cols = [c for c in target_cols if c in df_res.columns]
                
                if "回測勝率" in df_res.columns:
                    final_cols += ["回測勝率", "平均獲利", "總交易"]
                
                other_cols = [c for c in df_res.columns if c not in final_cols and c not in target_cols and c != "策略"]
                
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
