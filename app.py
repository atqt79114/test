import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
from datetime import date

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

1.  **⚡ 強勢回測 5MA (底底高)**：
    * **趨勢**：股價 > 120MA，且呈現「底底高」型態 (今日低點 > 昨日低點)。
    * **洗盤**：盤中回測跌破 5MA（最低點 < 5MA）。
    * **訊號**：收盤強勢站回 5MA 之上，代表多頭趨勢極強，回檔即買點。

2.  **🌀 布林中線 (量縮黑K)**：
    * **條件**：站上 120MA + 回測中線 ± 1.5% + 黑K + 量縮 + 中線向上。

3.  **🛁 爆量回檔 (雙黑K)**：
    * **條件**：前一根黑K且站 5MA、今日也是黑K且站 5MA，多頭排列（站上所有均線），乖離率 ≤ 6%。

4.  **🚀 回後買上漲**：
    * **條件一**：今日收盤站上 5MA。
    * **條件二**：紅K實體棒漲幅 > 2%（收 > 開，且漲幅超過 2%）。
    * **條件三**：收盤過昨日最高價（突破前高確認方向）。
    * **趨勢**：股價站上 5/10/20/60/120 全部均線，確保大趨勢向上。

5.  **🔥 週線盤整突破**：
    * 週線爆量 2.8 倍以上，且站上 5/10/20 週均線。

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
        except Exception:
            pass
    return stock_map


# -------------------------------------------------
# 產業族群：從證交所 & 櫃買 Open API 抓取中文分類
# -------------------------------------------------
# 證交所產業代碼 → 中文名稱對照表（內建，不依賴網路）
TWSE_INDUSTRY_CODE = {
    "01": "水泥工業",   "02": "食品工業",   "03": "塑膠工業",
    "04": "紡織纖維",   "05": "電機機械",   "06": "電器電纜",
    "07": "化學生技醫療","08": "玻璃陶瓷",   "09": "造紙工業",
    "10": "鋼鐵工業",   "11": "橡膠工業",   "12": "汽車工業",
    "13": "電子工業",   "14": "建材營造",   "15": "航運業",
    "16": "觀光餐旅",   "17": "金融保險",   "18": "貿易百貨",
    "19": "綜合",       "20": "其他",        "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業",
    "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業",
    "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業",
    "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保",   "36": "數位雲端",
    "37": "運動休閒",   "38": "居家生活",
    # 上櫃常見代碼
    "W2": "上櫃電子",   "W3": "上櫃生技",
}

@st.cache_data(ttl=86400)
def get_sector_map():
    """
    回傳 {ticker: 中文產業別}
    策略：
      1. TWSE openapi → 取 '產業別' 欄，若是數字代碼則對照 TWSE_INDUSTRY_CODE 轉中文
      2. TPEx openapi → 同上
      3. ISIN 頁面備援（第4欄）
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    sector_map = {}

    def resolve_industry(raw: str) -> str:
        """將代碼或中文名稱統一轉成中文；若查不到則原樣回傳"""
        raw = raw.strip()
        if not raw or raw in ("nan", "None", ""):
            return ""
        # 純數字代碼 → 查對照表
        if raw.isdigit():
            return TWSE_INDUSTRY_CODE.get(raw.zfill(2), f"產業{raw}")
        # 兩位字母+數字混合
        return TWSE_INDUSTRY_CODE.get(raw, raw)

    # ── 來源1：TWSE 上市 Open API ──
    try:
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        for item in r.json():
            code = str(item.get("公司代號", "")).strip()
            raw  = str(item.get("產業別", "")).strip()
            if code.isdigit() and len(code) == 4:
                industry = resolve_industry(raw)
                if industry:
                    sector_map[f"{code}.TW"] = industry
    except Exception:
        pass

    # ── 來源2：TPEx 上櫃 Open API ──
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        for item in r.json():
            code = str(item.get("SecuritiesCompanyCode", "")).strip()
            raw  = str(item.get("IndustryType", "")).strip()
            if code.isdigit() and len(code) == 4:
                industry = resolve_industry(raw)
                if industry:
                    sector_map[f"{code}.TWO"] = industry
    except Exception:
        pass

    # ── 來源3：ISIN 頁面備援（第4欄是產業別）──
    if not sector_map:
        try:
            for mode, suffix in [("2", ".TW"), ("4", ".TWO")]:
                url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
                r = requests.get(url, headers=headers, verify=False, timeout=10)
                raw_df = pd.read_html(r.text)[0]
                for row in raw_df.itertuples(index=False):
                    try:
                        cell = str(row[0]).split()
                        if len(cell) >= 2 and cell[0].isdigit() and len(cell[0]) == 4:
                            code = cell[0]
                            raw = str(row[4]).strip() if len(row) > 4 else ""
                            industry = resolve_industry(raw)
                            if industry and industry not in ("產業別",):
                                sector_map[f"{code}{suffix}"] = industry
                    except Exception:
                        continue
        except Exception:
            pass

    return sector_map


def get_sector(ticker, sector_map):
    """查詢單一股票的中文產業族群，找不到時 fallback yfinance"""
    result = sector_map.get(ticker, "")
    if result:
        return result
    # Fallback：yfinance（只在 sector_map 完全查不到時才呼叫）
    try:
        info = yf.Ticker(ticker).info
        return info.get("industry") or info.get("sector") or "—"
    except Exception:
        return "—"


# -------------------------------------------------
# 核心：批量下載函式
# -------------------------------------------------
def download_batch_data(tickers_batch):
    if not tickers_batch:
        return {}
    try:
        data = yf.download(
            tickers_batch, period="2y", interval="1d",
            group_by='ticker', progress=False, threads=True, auto_adjust=False
        )
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
                    if df['Close'].isnull().all():
                        continue
                    df = df.dropna(how='all')
                    if not df.empty:
                        result_dict[t] = df
            except Exception:
                continue

        return result_dict
    except Exception:
        return {}

# -------------------------------------------------
# 輔助：計算風控數據
# -------------------------------------------------
def calculate_risk_reward(c_now, sl_price, date_now, custom_target=None):
    sl_price = round(sl_price, 2)
    risk = c_now - sl_price
    if risk <= 0:
        return None  # 停損設定無效，拒絕此訊號

    if custom_target:
        target_price = round(custom_target, 2)
        potential_profit = (target_price - c_now) / c_now
    else:
        target_price = round(c_now + (risk * 2.0), 2)
        potential_profit = (risk * 2.0) / c_now

    today_str = date.today().strftime('%Y-%m-%d')
    signal_date = date_now.strftime('%Y-%m-%d')
    is_today = (signal_date == today_str)

    return {
        "訊號日期": f"🆕 {signal_date}" if is_today else signal_date,
        "停損價(SL)": sl_price,
        "停利價(TP)": target_price,
        "潛在獲利": f"{round(potential_profit * 100, 1)}%"
    }

# -------------------------------------------------
# 核心：回測引擎（已同步所有策略最新邏輯）
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        is_weekly = (strategy_type == "weekly_breakout")
        lookback = months * 4 if is_weekly else months * 22

        if len(df) < lookback + 20:
            return None

        trades = []
        in_position = False
        entry_price = 0
        target_price = 0
        stop_loss_price = 0

        start_idx = len(df) - lookback
        if start_idx < 125:
            start_idx = 125

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        bb20 = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        for i in range(start_idx, len(df) - 1):
            c_curr = float(close.iloc[i])
            h_curr = float(high.iloc[i])
            l_curr = float(low.iloc[i])
            o_curr = float(open_p.iloc[i])

            # === 持倉管理 ===
            if in_position:
                if h_curr >= target_price:
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False
                    continue

                if c_curr < stop_loss_price:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False
                    continue

                if strategy_type == "bollinger_mid":
                    target_price = bb20.bollinger_hband().iloc[i]
                continue

            # === 進場訊號 ===
            signal = False
            curr_sl = 0
            curr_tp = 0

            if not is_weekly and volume.iloc[i] < 500_000:
                continue

            # ── 策略1：布林中線 ──
            if strategy_type == "bollinger_mid":
                if c_curr > ma120.iloc[i]:
                    mid = bb20.bollinger_mavg().iloc[i]
                    if abs(c_curr - mid) / mid <= 0.015 and mid > bb20.bollinger_mavg().iloc[i - 1]:
                        if c_curr < o_curr and volume.iloc[i] < volume.iloc[i - 1]:
                            signal = True
                            curr_sl = mid * 0.97
                            curr_tp = bb20.bollinger_hband().iloc[i]

            # ── 策略2：爆量回檔（雙黑K，已同步新邏輯）──
            elif strategy_type == "washout":
                if i < 1:
                    continue
                c_prev_bt = float(close.iloc[i - 1])
                o_prev_bt = float(open_p.iloc[i - 1])
                ma5_curr_bt = float(ma5.iloc[i])
                ma5_prev_bt = float(ma5.iloc[i - 1])

                # 前一根黑K且站5MA
                cond_prev = (c_prev_bt < o_prev_bt) and (c_prev_bt > ma5_prev_bt)
                # 今日黑K且站5MA
                cond_curr = (c_curr < o_curr) and (c_curr > ma5_curr_bt)
                # 多頭排列
                cond_trend = (
                    c_curr > ma20.iloc[i] and
                    c_curr > ma60.iloc[i] and
                    c_curr > ma120.iloc[i]
                )
                # 乖離率
                bias = (c_curr - ma5_curr_bt) / ma5_curr_bt * 100

                if cond_prev and cond_curr and cond_trend and bias <= 6:
                    signal = True
                    curr_sl = ma5_curr_bt * 0.99   # 停損略低於5MA
                    curr_tp = c_curr * 1.12

            # ── 策略3：回後買上漲 ──
            elif strategy_type == "pullback_buy_breakout":
                if i < 1:
                    continue
                o_curr_bt = float(open_p.iloc[i])
                h_prev_bt = float(high.iloc[i - 1])

                # 紅K且實體 > 2%
                if c_curr <= o_curr_bt:
                    continue
                body_pct_bt = (c_curr - o_curr_bt) / o_curr_bt * 100
                if body_pct_bt <= 2.0:
                    continue

                # 收盤過昨高
                if c_curr <= h_prev_bt:
                    continue

                # 站上所有均線
                if not (c_curr > ma5.iloc[i] and c_curr > ma10.iloc[i] and
                        c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i] and
                        c_curr > ma120.iloc[i]):
                    continue

                signal = True
                curr_sl = h_prev_bt * 0.99
                curr_tp = c_curr * 1.15

            # ── 策略4：強勢回測5MA（底底高，已修正方向）──
            elif strategy_type == "strong_trend_ma5":
                if c_curr < 20:
                    continue
                if c_curr < ma120.iloc[i]:
                    continue
                if i < 1:
                    continue

                l_prev_bt = float(low.iloc[i - 1])
                ma5_curr_bt = float(ma5.iloc[i])

                # 底底高：今日低點 > 昨日低點（修正：原程式寫反了）
                cond_higher_low    = l_curr > l_prev_bt
                # 盤中跌破5MA
                cond_intraday_break = l_curr < ma5_curr_bt
                # 收盤站回5MA
                cond_reclaim_ma5   = c_curr > ma5_curr_bt * 1.001

                if cond_higher_low and cond_intraday_break and cond_reclaim_ma5:
                    signal = True
                    curr_sl = l_curr
                    curr_tp = c_curr * 1.1

            if signal:
                in_position = True
                entry_price = c_curr
                stop_loss_price = curr_sl
                target_price = curr_tp

        if not trades:
            return {"回測勝率": "無訊號", "平均獲利": "0%", "總交易": 0}

        win_count = sum(1 for p in trades if p > 0)
        return {
            "回測勝率": f"{round((win_count / len(trades)) * 100, 1)}%",
            "平均獲利": f"{round((sum(trades) / len(trades)) * 100, 2)}%",
            "總交易": len(trades)
        }

    except Exception:
        return None

# -------------------------------------------------
# 策略函式
# -------------------------------------------------

def strategy_bollinger_mid(ticker, name, df, backtest_months):
    try:
        if len(df) < 125:
            return None
        close  = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        c_now = float(close.iloc[-1])
        o_now = float(open_p.iloc[-1])
        v_now = float(volume.iloc[-1])
        v_prev = float(volume.iloc[-2])

        if v_now < 500_000:
            return None
        if c_now < ta.trend.sma_indicator(close, 120).iloc[-1]:
            return None

        indicator_bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        bb_mavg  = indicator_bb.bollinger_mavg()
        bb_hband = indicator_bb.bollinger_hband()
        mid_now   = float(bb_mavg.iloc[-1])
        upper_now = float(bb_hband.iloc[-1])

        if abs(c_now - mid_now) / mid_now > 0.015:
            return None
        if mid_now < float(bb_mavg.iloc[-2]):
            return None
        if c_now >= o_now:
            return None
        if v_now >= v_prev:
            return None

        bt_res  = run_backtest(df, "bollinger_mid", backtest_months)
        sl_price = mid_now * 0.97
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1], custom_target=upper_now)
        if rr is None:
            return None

        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "布林中線": round(mid_now, 2),
            "布林上軌": round(upper_now, 2),
            **rr, **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "中線黑K量縮 🌀"
        }
    except Exception:
        return None


def strategy_washout_rebound(ticker, name, df, backtest_months):
    """
    爆量回檔（雙黑K版）
    條件：
      - 前一根：黑K（收 < 開）且收盤站在 5MA 之上
      - 今日：黑K（收 < 開）且收盤站在 5MA 之上
      - 多頭排列：站上 MA10 / MA20 / MA60 / MA120
      - 5MA 乖離率 ≤ 6%
    """
    try:
        if len(df) < 125:
            return None

        close  = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        if float(volume.iloc[-1]) < 500_000:
            return None

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_now  = float(close.iloc[-1])
        o_now  = float(open_p.iloc[-1])
        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])

        ma5_now  = float(ma5.iloc[-1])
        ma5_prev = float(ma5.iloc[-2])

        # ── 前一根：黑K ──
        if c_prev >= o_prev:
            return None
        # ── 前一根：站在 5MA 之上 ──
        if c_prev <= ma5_prev:
            return None
        # ── 今日：黑K ──
        if c_now >= o_now:
            return None
        # ── 今日：收盤仍站在 5MA 之上 ──
        if c_now <= ma5_now:
            return None
        # ── 多頭排列 ──
        if not (c_now > float(ma10.iloc[-1]) and
                c_now > float(ma20.iloc[-1]) and
                c_now > float(ma60.iloc[-1]) and
                c_now > float(ma120.iloc[-1])):
            return None

        # ── 5MA 乖離率 ≤ 6% ──
        bias_5 = ((c_now - ma5_now) / ma5_now) * 100
        if bias_5 > 6:
            return None

        pct_change = (c_now - c_prev) / c_prev * 100

        bt_res = run_backtest(df, "washout", backtest_months)
        sl_price = ma5_now * 0.99   # 停損略低於5MA
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1])
        if rr is None:
            return None

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            "漲幅": f"{round(pct_change, 2)}%",
            "5日乖離率": f"{round(bias_5, 2)}%",
            **rr,
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "強勢回檔黑K 🛁"
        }
    except Exception:
        return None


def strategy_pullback_buy_breakout(ticker, name, df, backtest_months):
    """
    回後買上漲策略（圖示版）
    條件：
      1. 收盤站上 5MA
      2. 紅K實體棒 > 2%（收 > 開，漲幅 > 2%）
      3. 收盤過昨日最高價
      4. 多頭排列：站上 MA5 / MA10 / MA20 / MA60 / MA120 全部均線
    停損：昨日最高價（跌破即代表突破失敗）
    """
    try:
        if len(df) < 130:
            return None

        close  = df["Close"]
        open_p = df["Open"]
        high   = df["High"]
        volume = df["Volume"]

        c_now  = float(close.iloc[-1])
        o_now  = float(open_p.iloc[-1])
        h_prev = float(high.iloc[-2])   # 昨日最高價
        v_now  = float(volume.iloc[-1])

        # 基本量能過濾
        if v_now < 1_000_000:
            return None
        if c_now < 10:
            return None
        if ticker.startswith("28"):
            return None

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now   = float(ma5.iloc[-1])
        ma10_now  = float(ma10.iloc[-1])
        ma20_now  = float(ma20.iloc[-1])
        ma60_now  = float(ma60.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        # ── 條件1：收盤站上 5MA ──
        if c_now <= ma5_now:
            return None

        # ── 條件2：紅K，且實體漲幅 > 2% ──
        if c_now <= o_now:
            return None
        body_pct = (c_now - o_now) / o_now * 100
        if body_pct <= 2.0:
            return None

        # ── 條件3：收盤過昨日最高價 ──
        if c_now <= h_prev:
            return None

        # ── 條件4：多頭排列（站上所有均線）──
        if not (c_now > ma10_now and c_now > ma20_now and
                c_now > ma60_now and c_now > ma120_now):
            return None

        # 計算整體漲幅（相對昨收）
        c_prev = float(close.iloc[-2])
        total_pct = (c_now - c_prev) / c_prev * 100

        bt_res = run_backtest(df, "pullback_buy_breakout", backtest_months)

        # 停損：昨日最高價（突破昨高才是有效突破，若跌回即失敗）
        sl_price = h_prev * 0.99
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1])
        if rr is None:
            return None

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            "今日漲幅": f"{round(total_pct, 2)}%",
            "紅K實體": f"{round(body_pct, 2)}%",
            "昨日最高": round(h_prev, 2),
            "5MA": round(ma5_now, 2),
            **rr,
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "回後買上漲 🚀"
        }
    except Exception:
        return None


def strategy_weekly_breakout(ticker, name, df_daily, backtest_months):
    try:
        df_weekly = df_daily.resample('W').agg({
            'Open': 'first', 'High': 'max',
            'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        })
        if len(df_weekly) < 30:
            return None

        close  = df_weekly['Close']
        volume = df_weekly['Volume']
        ma5  = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)

        c_now  = float(close.iloc[-1])
        v_now  = float(volume.iloc[-1])
        v_prev = float(volume.iloc[-2])
        ma5_now  = float(ma5.iloc[-1])
        ma10_now = float(ma10.iloc[-1])
        ma20_now = float(ma20.iloc[-1])

        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now):
            return None
        if v_now <= v_prev * 2.8:
            return None

        rr = calculate_risk_reward(c_now, ma5_now, df_weekly.index[-1])
        if rr is None:
            return None

        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            **rr,
            "回測勝率": "N/A", "平均獲利": "-", "總交易": "-",
            "本週量(張)": int(v_now / 1000),
            "爆量倍數": f"{round(v_now / v_prev, 1)}倍",
            "外資詳情": get_chip_link(ticker),
            "狀態": "週線爆量 🔥"
        }
    except Exception:
        return None


def strategy_strong_trend_ma5(ticker, name, df, backtest_months):
    """
    強勢回測5MA（底底高洗盤）
    條件：
      - 股價 > 120MA（長線多頭）
      - 今日低點 > 昨日低點（底底高，上升中的回檔，修正原版寫反的邏輯）
      - 今日最低點 < 5MA（盤中跌破5MA，完成洗盤）
      - 收盤 > 5MA * 1.001（站回5MA之上，多頭確認）
      - 成交量 ≥ 1000張，股價 > 20元
    """
    try:
        if len(df) < 130:
            return None

        close  = df["Close"]
        low    = df["Low"]
        volume = df["Volume"]

        c_now  = float(close.iloc[-1])
        l_now  = float(low.iloc[-1])
        l_prev = float(low.iloc[-2])
        v_now  = float(volume.iloc[-1])

        if v_now < 1_000_000:
            return None
        if c_now <= 20:
            return None

        ma5   = ta.trend.sma_indicator(close, 5)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now   = float(ma5.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        if c_now <= ma120_now:
            return None

        # ── 核心條件 ──
        # 1. 底底高：今日低點 > 昨日低點（修正：原版 l_now < l_prev 是反的）
        cond_higher_low     = l_now > l_prev
        # 2. 盤中跌破5MA（最低點低於5MA）
        cond_intraday_break = l_now < ma5_now
        # 3. 收盤站回5MA（避免剛好貼線，留0.1%緩衝）
        cond_reclaim_ma5    = c_now > ma5_now * 1.001

        if not (cond_higher_low and cond_intraday_break and cond_reclaim_ma5):
            return None

        bt_res   = run_backtest(df, "strong_trend_ma5", backtest_months)
        sl_price = l_now  # 停損：今日最低點，強勢股不該再破
        rr = calculate_risk_reward(c_now, sl_price, df.index[-1])
        if rr is None:
            return None

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

    except Exception:
        return None


# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "⚡ 強勢回測 5MA (底底高)":        strategy_strong_trend_ma5,
    "🌀 布林中線 (量縮黑K)":           strategy_bollinger_mid,
    "🛁 爆量回檔 (雙黑K站5MA)":        strategy_washout_rebound,
    "🚀 回後買上漲 (紅K過昨高)":       strategy_pullback_buy_breakout,
    "🔥 週線盤整突破 (爆量2.8倍)":     strategy_weekly_breakout,
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
    stock_map = {t: full_map.get(t, t) for t in tickers}
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

# -------------------------------------------------
# 掃描主流程（即時顯示結果）
# -------------------------------------------------
if st.button("開始掃描", type="primary"):
    if not tickers:
        st.error("沒有股票代碼！")
    else:
        # 載入產業族群對照表（有快取，第二次掃描不重抓）
        if "sector_map" not in st.session_state:
            with st.spinner("載入產業族群資料..."):
                st.session_state["sector_map"] = get_sector_map()
        _sector_map = st.session_state["sector_map"]

        result = {k: [] for k in selected}

        # 每個策略建立一個 placeholder，即時更新
        placeholders = {k: st.empty() for k in selected}

        progress_bar = st.progress(0)
        status_text  = st.empty()

        batch_size    = 50
        total_tickers = len(tickers)

        def render_results(strategy_name, rows):
            """即時渲染單一策略結果"""
            if not rows:
                return
            with placeholders[strategy_name].container():
                st.subheader(f"📊 {strategy_name}　（{len(rows)} 筆）")
                df_res = pd.DataFrame(rows)

                if "布林中線" in df_res.columns:
                    target_cols = ["代號", "名稱", "族群", "現價", "布林中線", "布林上軌",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]
                elif "爆量倍數" in df_res.columns:
                    target_cols = ["代號", "名稱", "族群", "現價", "本週量(張)", "爆量倍數",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]
                elif "紅K實體" in df_res.columns:
                    target_cols = ["代號", "名稱", "族群", "現價", "今日漲幅", "紅K實體",
                                   "昨日最高", "5MA",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]
                elif "今日低點" in df_res.columns:
                    target_cols = ["代號", "名稱", "族群", "現價", "今日低點", "昨日低點", "5MA",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]
                elif "5日乖離率" in df_res.columns:
                    target_cols = ["代號", "名稱", "族群", "現價", "漲幅", "5日乖離率",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]
                else:
                    target_cols = ["代號", "名稱", "族群", "現價",
                                   "停損價(SL)", "停利價(TP)", "潛在獲利", "外資詳情"]

                final_cols = [c for c in target_cols if c in df_res.columns]

                if "回測勝率" in df_res.columns:
                    final_cols += ["回測勝率", "平均獲利", "總交易"]

                # 加上訊號日期
                if "訊號日期" in df_res.columns and "訊號日期" not in final_cols:
                    final_cols = ["訊號日期"] + final_cols

                other_cols = [
                    c for c in df_res.columns
                    if c not in final_cols and c not in target_cols and c != "策略"
                ]

                st.dataframe(
                    df_res[final_cols + other_cols],
                    use_container_width=True,
                    column_config={
                        "外資詳情": st.column_config.LinkColumn(
                            "外資詳情", display_text="查看數據"
                        )
                    }
                )

        # ── 主掃描迴圈 ──
        for i in range(0, total_tickers, batch_size):
            current_progress = min((i + batch_size) / total_tickers, 1.0)
            progress_bar.progress(current_progress)

            batch_tickers = tickers[i: i + batch_size]
            status_text.text(
                f"掃描中... {i + 1} ~ {min(i + batch_size, total_tickers)} / {total_tickers} 檔"
            )

            data_dict = download_batch_data(batch_tickers)
            if not data_dict:
                time.sleep(1)
                continue

            updated = set()
            for t, df in data_dict.items():
                name = stock_map.get(t, t)
                for k in selected:
                    try:
                        r = STRATEGIES[k](t, name, df, backtest_period)
                        if r:
                            # 補充產業族群
                            r["族群"] = get_sector(t, _sector_map)
                            r["策略"] = k
                            result[k].append(r)
                            updated.add(k)
                    except Exception:
                        continue

            # 即時更新有新結果的策略
            for k in updated:
                render_results(k, result[k])

            time.sleep(0.5)

        progress_bar.empty()
        status_text.empty()

        # 最終渲染（確保所有結果都顯示）
        for k in selected:
            render_results(k, result[k])

        total_hits = sum(len(v) for v in result.values())
        if total_hits == 0:
            st.info("掃描完成，沒有符合條件的股票。")
        else:
            st.success(f"✅ 掃描完成，共找到 {total_hits} 筆符合訊號。")
