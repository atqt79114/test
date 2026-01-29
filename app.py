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

2.  **🛁 爆量回檔** & **📦 盤整突破**：
    * 經典動能策略，需站上 120MA，賺賠比 1:1.5。
    * **[新增] 爆量回檔乖離率限制**：收盤價距離 5MA 不可超過 **6%**。

3.  **🔥 週線盤整突破**：
    * 週線爆量 2.8 倍以上。

4.  **🛡️ 週線回檔守 5MA (熱門股)**：
    * **流動性**：**上週成交量 > 10 萬張** (過濾出高人氣股)。
    * **趨勢**：股價 > 週線 20MA。
    * **上週**：紅K + 收在 5MA 之上。
    * **本週**：**量縮黑K** + 收在 5MA 之上。
    * **乖離率限制**：**現價與 5MA 乖離不可超過 7%** (避免追高)。
    * **停損**：週線 5MA (收破)。 **停利**：突破上週高點。

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
# 核心：回測引擎 (修復日線策略邏輯)
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        # 判斷是日線還是週線資料來決定回測長度
        is_weekly = (strategy_type == "weekly_pullback")
        lookback = months * 4 if is_weekly else months * 22
        
        if len(df) < lookback + 20: return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        stop_loss_price = 0
        
        start_idx = len(df) - lookback
        if start_idx < 25: start_idx = 25 # 確保有足夠前面資料算MA
        
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]
        
        # 預先計算需要的指標
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10) 
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60) 
        ma120 = ta.trend.sma_indicator(close, 120)
        
        bb20 = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        for i in range(start_idx, len(df) - 1):
            c_curr = close.iloc[i]; h_curr = high.iloc[i]; l_curr = low.iloc[i]
            
            # === 持倉檢查 ===
            if in_position:
                # 停利：碰到目標價
                if h_curr >= target_price: 
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                
                # 停損出場
                exit_condition = False
                if strategy_type == "weekly_pullback":
                    if c_curr < stop_loss_price: exit_condition = True
                else:
                    if c_curr < stop_loss_price: exit_condition = True 
                
                if exit_condition:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                
                # 移動停利邏輯 (部分策略)
                if strategy_type == "bollinger_mid":
                    target_price = bb20.bollinger_hband().iloc[i]
                continue

            # === 進場訊號 ===
            signal = False
            curr_sl = 0
            curr_tp = 0
            
            # [日線策略通用過濾]
            if not is_weekly and volume.iloc[i] < 500_000: continue

            # 1. 策略：中線策略 (20MA)
            if strategy_type == "bollinger_mid":
                if c_curr > ma120.iloc[i]:
                    mid = bb20.bollinger_mavg().iloc[i]
                    if abs(c_curr - mid) / mid <= 0.015 and mid > bb20.bollinger_mavg().iloc[i-1]:
                        if c_curr < open_p.iloc[i] and volume.iloc[i] < volume.iloc[i-1]:
                            signal = True
                            curr_sl = mid * 0.97
                            curr_tp = bb20.bollinger_hband().iloc[i]

            # 2. 策略：洗盤 (Washout) - [已修復邏輯]
            elif strategy_type == "washout":
                # 模擬條件：均線多頭排列 + 帶量站回 5MA
                if c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i]:
                    # 昨日在5MA下，今日站上5MA (轉強)
                    if close.iloc[i-1] < ma5.iloc[i-1] and c_curr > ma5.iloc[i]:
                         # 帶量紅K
                         if c_curr > open_p.iloc[i] and volume.iloc[i] > volume.iloc[i-1]:
                            # 回測時加上寬鬆一點的乖離率檢查 (可選)
                            if (c_curr - ma5.iloc[i]) / ma5.iloc[i] < 0.08:
                                signal = True
                                curr_sl = ma20.iloc[i] # 跌破月線停損
                                curr_tp = c_curr * 1.15 # 預期15%獲利

            # 3. 策略：盤整突破 - [已修復邏輯]
            elif strategy_type == "consolidation":
                 # 模擬條件：均線糾結後 + 爆量長紅突破
                 if c_curr > ma5.iloc[i] and c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i]:
                      # 實體紅K > 3% 且 成交量放大 1.5 倍
                      if (c_curr - open_p.iloc[i])/open_p.iloc[i] > 0.03 and volume.iloc[i] > volume.iloc[i-1]*1.5:
                          signal = True
                          curr_sl = open_p.iloc[i] # 跌破起漲點停損
                          curr_tp = c_curr * 1.2 # 預期20%波段獲利

            # 4. 策略：週線回檔守 5MA 回測
            elif strategy_type == "weekly_pullback":
                # i = 本週, i-1 = 上週
                c_prev = close.iloc[i-1]; o_prev = open_p.iloc[i-1]; v_prev = volume.iloc[i-1]
                h_prev = high.iloc[i-1]
                
                # 條件
                if v_prev < 100000 * 1000: continue
                if c_curr < ma20.iloc[i]: continue
                if not (c_prev > o_prev and c_prev > ma5.iloc[i-1]): continue
                
                if c_curr < open_p.iloc[i] and volume.iloc[i] < v_prev and c_curr > ma5.iloc[i]:
                    signal = True
                    curr_sl = ma5.iloc[i] * 0.98 
                    curr_tp = h_prev 
            # ==========================================
            # [NEW] 新增：箱體突破回測邏輯
            # ==========================================
            elif strategy_type == "box_breakout":
                # 1. 趨勢濾網：在 MA120 之上
                if c_curr < ma120.iloc[i]: continue

                # 2. 定義箱體：取過去 60 天 (不含當日 i)
                # 範圍是 i-60 到 i
                box_lookback = 60
                past_highs = high.iloc[i-box_lookback:i]
                past_lows = low.iloc[i-box_lookback:i]
                
                box_h = past_highs.max()
                box_l = past_lows.min()
                
                # 3. 箱體寬度濾網 (< 15%)
                width = (box_h - box_l) / box_l
                if width > 0.15: continue
                
                # 4. 突破訊號
                # 今日收盤 突破 箱頂
                # 且 今日量增 (比昨日大)
                if c_curr > box_h and volume.iloc[i] > volume.iloc[i-1]:
                    # 避免追高：突破幅度不超過 5%
                    if c_curr < box_h * 1.05:
                        signal = True
                        curr_sl = box_l # 停損設箱底
                        curr_tp = c_curr + (box_h - box_l) * 1.5 # 目標：一倍半箱體幅度

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

# === 修改重點：加入乖離率 < 6% 過濾 ===
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
        
        # --- [NEW] 新增乖離率過濾 ---
        # 邏輯：現價距離 5MA 不超過 6%
        bias_5 = ((c_now - ma5_now) / ma5_now) * 100
        if bias_5 > 4: return None
        # ---------------------------

        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now, df.index[-1])
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "5日乖離率": f"{round(bias_5, 2)}%",  # 顯示乖離率
            **rr, 
            **(bt_res or {}), 
            "外資詳情": get_chip_link(ticker), 
            "狀態": "強勢洗盤 🛁"
        }
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
        return {"代號": ticker, "名稱": name, "現價": round(c_now, 2), **rr, "回測勝率": "N/A", "平均獲利": "-", "總交易": "-", "本週量(張)": int(v_now/1000), "爆量倍數": f"{round(v_now/v_prev, 1)}倍", "外資詳情": get_chip_link(ticker), "狀態": "週線爆量 🔥"}
    except: return None

# === 週線回檔守5MA (含回測功能 + 乖離率過濾) ===
def strategy_weekly_pullback(ticker, name, df_daily, backtest_months):
    try:
        # 1. 轉換為週線
        df_weekly = df_daily.resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
        
        # 為了回測，我們需要多一點資料
        if len(df_weekly) < 40: return None
        
        close = df_weekly['Close']
        open_p = df_weekly['Open']
        high = df_weekly['High']
        volume = df_weekly['Volume']

        # 2. 計算指標
        ma5 = ta.trend.sma_indicator(close, 5)
        ma20 = ta.trend.sma_indicator(close, 20)

        # 3. 取得數據 (T=本週, T-1=上週)
        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1]); v_now = float(volume.iloc[-1])
        ma5_now = float(ma5.iloc[-1]); ma20_now = float(ma20.iloc[-1])

        c_prev = float(close.iloc[-2]); o_prev = float(open_p.iloc[-2])
        h_prev = float(high.iloc[-2]); v_prev = float(volume.iloc[-2])
        ma5_prev = float(ma5.iloc[-2])

        # 4. 篩選邏輯
        # 成交量過濾：上週成交量需 > 10萬張 (100,000 * 1000 股)
        if v_prev < 100000 * 1000: return None

        if c_now < ma20_now: return None

        # 上週 (T-1): 紅K + 在 5MA 之上
        if not (c_prev > o_prev): return None
        if not (c_prev > ma5_prev): return None

        # 本週 (T): 黑K + 量縮 + 守 5MA
        if not (c_now < o_now): return None
        if not (v_now < v_prev): return None
        if not (c_now > ma5_now): return None

        # --- [NEW] 新增乖離率過濾 ---
        # 邏輯：雖然股價守在 5MA 之上，但不能離太遠 (避免買在乖離過大處)
        bias_5t = ((c_now - ma5_now) / ma5_now) * 100
        
        # 如果乖離率超過 7%，直接剔除
        if bias_5t > 7: return None
        # ---------------------------

        # 5. 執行週線回測
        bt_res = run_backtest(df_weekly, "weekly_pullback", backtest_months)

        # 6. 計算風控
        sl_price = ma5_now
        tp_price = h_prev # 目標：過上週高
        
        rr = calculate_risk_reward(c_now, sl_price, df_weekly.index[-1], custom_target=tp_price)
        
        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "5週乖離率": f"{round(bias_5t, 2)}%", # 顯示
            **rr,
            **(bt_res or {}),
            "本週量(張)": int(v_now/1000),
            "上週量(張)": int(v_prev/1000),
            "外資詳情": get_chip_link(ticker), 
            "狀態": "週線回檔守5MA 🛡️"
        }
    except Exception: return None

# === 新增策略：箱體突破 (Box Breakout) ===
def strategy_box_breakout(ticker, name, df, backtest_months):
    """
    策略：MA120之上 + 60天箱體盤整(<15%) + 今日剛突破
    """
    try:
        # 1. 資料長度與流動性檢查
        if len(df) < 130: return None
        if df['Volume'].iloc[-1] < 500_000: return None # 成交量過濾

        # 2. 準備數據
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        c_now = float(close.iloc[-1])
        v_now = float(volume.iloc[-1])
        v_prev = float(volume.iloc[-2])
        
        # 3. 趨勢濾網：股價必須在 MA120 之上
        ma120 = ta.trend.sma_indicator(close, 120).iloc[-1]
        if c_now < ma120: return None

        # 4. 定義箱體 (重點！)
        # 使用過去 60 天，但 **排除今天** (我們要看今天是否突破了過去形成的箱子)
        box_days = 60
        df_past = df.iloc[:-1] # 排除最新一天
        
        # 取得過去 N 天的高低點
        past_highs = df_past['High'].tail(box_days)
        past_lows = df_past['Low'].tail(box_days)
        
        box_h = float(past_highs.max())
        box_l = float(past_lows.min())

        # 5. 計算箱體寬度 (Box Width)
        # 公式: (箱頂 - 箱底) / 箱底
        box_width = (box_h - box_l) / box_l
        
        # 條件：震盪幅度需小於 15% (視為盤整)
        if box_width > 0.15: return None 

        # 6. 突破訊號判定
        # A. 今天收盤價 > 昨天的箱頂
        if c_now <= box_h: return None
        
        # B. 避免追高 (突破幅度 < 5%)
        if c_now > box_h * 1.05: return None
        
        # C. 量能確認 (量增)
        if v_now <= v_prev: return None

        # 7. 執行回測
        bt_res = run_backtest(df, "box_breakout", backtest_months)

        # 8. 計算風控
        # 停損：箱底 (保守者可用箱頂下緣，但這裡設箱底比較安全)
        sl_price = box_l 
        # 停利：箱體高度的 1.5 倍
        tp_price = c_now + (box_h - box_l) * 1.5

        rr = calculate_risk_reward(c_now, sl_price, df.index[-1], custom_target=tp_price)

        return {
            "代號": ticker, 
            "名稱": name, 
            "現價": round(c_now, 2), 
            "箱頂(壓力)": round(box_h, 2),
            "箱底(支撐)": round(box_l, 2),
            "震盪幅": f"{round(box_width*100, 1)}%",
            **rr, 
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker), 
            "狀態": "🚀 箱體剛突破"
        }
    except Exception as e:
        return None

# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    # 已移除布林下軌策略
    "🌀 布林中線 (量縮黑K)": strategy_bollinger_mid,
    "🛁 爆量回檔 (洗盤)": strategy_washout_rebound,
    "📦 日線盤整突破": strategy_consolidation,
    "🔥 週線盤整突破 (爆量2.8倍)": strategy_weekly_breakout,
    "🛡️ 週線回檔守 5MA (New!)": strategy_weekly_pullback,
    "🚀 箱體剛突破 (New!)": strategy_box_breakout,  # <--- 新增這一行
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
         # ... (前面程式碼不變)
                
                # 欄位顯示名稱更新
                base_cols = ["代號", "名稱", "現價", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                # === 修正重點：確保這裡的 if/elif 全部垂直對齊 ===
                if "布林中線" in df_res.columns or "布林中線(10MA)" in df_res.columns:
                    if "布林下軌" in df_res.columns: 
                        target_cols = ["代號", "名稱", "現價", "布林下軌", "布林中線(10MA)", "停損價(SL)", "停利價(TP)", "外資詳情"]
                    else: 
                        target_cols = ["代號", "名稱", "現價", "布林中線", "布林上軌", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                elif "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "現價", "本週量(張)", "爆量倍數", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                elif "上週量(張)" in df_res.columns:
                    # 優先顯示乖離率
                    target_cols = ["代號", "名稱", "現價", "5週乖離率", "本週量(張)", "上週量(張)", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                elif "5日乖離率" in df_res.columns:
                    # === 修改重點：加入 5日乖離率 到優先顯示欄位 ===
                    target_cols = ["代號", "名稱", "現價", "5日乖離率", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                elif "箱頂(壓力)" in df_res.columns:
                    # === [NEW] 新增：箱體突破專屬欄位顯示 === (這裡原本縮排錯誤)
                    target_cols = ["代號", "名稱", "現價", "震盪幅", "箱頂(壓力)", "箱底(支撐)", "停損價(SL)", "停利價(TP)", "外資詳情"]
                
                else:
                    target_cols = base_cols
                
                # 確保欄位存在才選取
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
