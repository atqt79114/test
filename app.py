import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
from urllib.parse import urlencode

import gspread
from google.oauth2.service_account import Credentials
import httpx

warnings.filterwarnings("ignore")

# -------------------------------------------------
# 頁面設定
# -------------------------------------------------
st.set_page_config(page_title="台股潛伏策略篩選器", layout="wide")

# -------------------------------------------------
# Google OAuth 登入
# -------------------------------------------------
_CLIENT_ID     = st.secrets["GOOGLE_CLIENT_ID"]
_CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
_REDIRECT_URI  = "https://2sv2r89tp93nexxafg9gdm.streamlit.app/"
_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL     = "https://oauth2.googleapis.com/token"
_USERINFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"
_SCOPE         = "openid email profile"


def build_auth_url():
    params = {
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code):
    resp = httpx.post(_TOKEN_URL, data={
        "code": code,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "redirect_uri": _REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=20)
    return resp.json()


def get_user_info(access_token):
    resp = httpx.get(_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    return resp.json()


params = st.query_params
if "code" in params and "user_info" not in st.session_state:
    token_data = exchange_code_for_token(params["code"])
    if "access_token" in token_data:
        st.session_state["user_info"] = get_user_info(token_data["access_token"])
        st.session_state["connected"] = True
        st.query_params.clear()
        st.rerun()
    else:
        st.error(f"登入失敗：{token_data.get('error_description', token_data)}")
        st.stop()

if not st.session_state.get("connected"):
    st.title("台股潛伏/糾結策略篩選器")
    st.markdown("### 請先登入以使用完整功能（含個人庫存）")
    auth_url = build_auth_url()
    st.markdown(
        f'<a href="{auth_url}" target="_blank">'
        f'<button style="background:#4285F4;color:white;border:none;'
        f'padding:12px 28px;border-radius:6px;font-size:16px;cursor:pointer;">'
        f'使用 Google 帳號登入（新視窗）</button></a>',
        unsafe_allow_html=True,
    )
    st.info("登入完成後請回到此頁重新整理（F5）即可進入系統。")
    st.stop()

user_email = st.session_state["user_info"].get("email", "unknown")
user_name  = st.session_state["user_info"].get("name", "使用者")

# -------------------------------------------------
# Google Sheets
# -------------------------------------------------
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    return gspread.authorize(creds)


def get_or_create_user_sheet(gc, spreadsheet_id, user_email):
    sh = gc.open_by_key(spreadsheet_id)
    sheet_title = user_email[:50].replace("@", "_at_").replace(".", "_")
    try:
        ws = sh.worksheet(sheet_title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_title, rows=1000, cols=12)
        ws.append_row([
            "買入日期", "代號", "名稱", "族群", "策略",
            "買入價", "成本總額(元)", "張數", "停損價", "停利價", "備註"
        ])
    return ws


def load_portfolio(ws):
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=[
            "買入日期", "代號", "名稱", "族群", "策略",
            "買入價", "成本總額(元)", "張數", "停損價", "停利價", "備註"
        ])
    return pd.DataFrame(records)


def append_to_portfolio(ws, row):
    ws.append_row([
        row.get("買入日期", ""),
        row.get("代號", ""),
        row.get("名稱", ""),
        row.get("族群", ""),
        row.get("策略", ""),
        row.get("買入價", ""),
        row.get("成本總額(元)", ""),
        row.get("張數", ""),
        row.get("停損價", ""),
        row.get("停利價", ""),
        row.get("備註", "")
    ])


def delete_portfolio_row(ws, row_index):
    ws.delete_rows(row_index + 2)


if "portfolio_ws" not in st.session_state:
    try:
        gc = get_gspread_client()
        st.session_state["portfolio_ws"] = get_or_create_user_sheet(
            gc, st.secrets["SPREADSHEET_ID"], user_email
        )
    except Exception as e:
        st.error(f"Google Sheets 連線失敗：{e}")
        st.stop()

portfolio_ws = st.session_state["portfolio_ws"]

# -------------------------------------------------
# 📧 Gmail 寄信 + 停損停利檢查
# -------------------------------------------------
def send_alert_email(to_email, subject, html_body):
    try:
        sender   = st.secrets["GMAIL_SENDER"]
        password = st.secrets["GMAIL_APP_PASSWORD"]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, to_email, msg.as_string())
        return True
    except Exception as e:
        st.warning(f"寄信失敗：{e}")
        return False


def get_latest_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if not hist.empty and "Close" in hist.columns:
            return round(float(hist["Close"].dropna().iloc[-1]), 2)
    except Exception:
        pass
    return None


def check_and_alert(df_port, to_email, force=False):
    if df_port.empty:
        return []

    if force:
        st.session_state.pop("alerted_tickers", None)

    if "alerted_tickers" not in st.session_state:
        st.session_state["alerted_tickers"] = set()

    triggered = []
    for _, row in df_port.iterrows():
        ticker = str(row.get("代號", "")).strip()
        name   = str(row.get("名稱", ""))
        sl_raw = str(row.get("停損價", "")).replace(",", "").strip()
        tp_raw = str(row.get("停利價", "")).replace(",", "").strip()

        if not sl_raw and not tp_raw:
            continue
        if ticker in st.session_state["alerted_tickers"]:
            continue

        price = get_latest_price(ticker)
        if price is None:
            continue

        sl = float(sl_raw) if sl_raw else None
        tp = float(tp_raw) if tp_raw else None

        hit_type = None
        if sl and price <= sl:
            hit_type = "🔴 停損觸發"
        elif tp and price >= tp:
            hit_type = "🟢 停利觸發"

        if hit_type:
            triggered.append({
                "代號": ticker,
                "名稱": name,
                "現價": price,
                "停損價": sl,
                "停利價": tp,
                "類型": hit_type
            })
            st.session_state["alerted_tickers"].add(ticker)

    if triggered:
        rows_html = "".join([
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #ddd'>{t['類型']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{t['代號']} {t['名稱']}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'><b>{t['現價']}</b></td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{t['停損價'] or '—'}</td>"
            f"<td style='padding:8px;border:1px solid #ddd'>{t['停利價'] or '—'}</td>"
            f"</tr>"
            for t in triggered
        ])

        html_body = (
            "<html><body>"
            "<h2>📊 台股庫存價格警示</h2>"
            "<p>以下持股已觸發停損或停利條件，請留意：</p>"
            "<table style='border-collapse:collapse;width:100%;font-family:sans-serif'>"
            "<tr style='background:#f0f0f0'>"
            "<th style='padding:8px;border:1px solid #ddd'>類型</th>"
            "<th style='padding:8px;border:1px solid #ddd'>股票</th>"
            "<th style='padding:8px;border:1px solid #ddd'>現價</th>"
            "<th style='padding:8px;border:1px solid #ddd'>停損價</th>"
            "<th style='padding:8px;border:1px solid #ddd'>停利價</th>"
            "</tr>"
            f"{rows_html}"
            "</table>"
            "<br><p style='color:gray;font-size:12px'>此信由台股潛伏策略篩選器自動發送，請勿回覆。</p>"
            "</body></html>"
        )

        send_alert_email(
            to_email,
            f"台股庫存警示：{len(triggered)} 筆觸發（{date.today()}）",
            html_body
        )

    return triggered


# -------------------------------------------------
# 頁面標題
# -------------------------------------------------
col_title, col_user = st.columns([5, 1])
with col_title:
    st.title("💤 台股潛伏/糾結策略篩選器 (進階版)")
with col_user:
    st.markdown(f"**👤 {user_name}**")
    if st.button("登出"):
        st.session_state.clear()
        st.rerun()

st.markdown("""
---
### ⚠️ 免責聲明：市場沒有 100% 穩贏的策略
**所有篩選結果僅供技術分析參考，不代表買賣建議。請務必搭配基本面與籌碼面判斷。**
---
""")

# -------------------------------------------------
# 工具函式
# -------------------------------------------------
def get_chip_link(ticker):
    return f"https://tw.stock.yahoo.com/quote/{ticker.split('.')[0]}/institutional-trading"


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
                if len(data) >= 2 and data[0].isdigit() and len(data[0]) == 4:
                    suffix = ".TWO" if mode == "4" else ".TW"
                    stock_map[f"{data[0]}{suffix}"] = data[1]
        except Exception:
            pass
    return stock_map


TWSE_INDUSTRY_CODE = {
    "01":"水泥工業","02":"食品工業","03":"塑膠工業","04":"紡織纖維",
    "05":"電機機械","06":"電器電纜","07":"化學生技醫療","08":"玻璃陶瓷",
    "09":"造紙工業","10":"鋼鐵工業","11":"橡膠工業","12":"汽車工業",
    "13":"電子工業","14":"建材營造","15":"航運業","16":"觀光餐旅",
    "17":"金融保險","18":"貿易百貨","19":"綜合","20":"其他",
    "21":"化學工業","22":"生技醫療業","23":"油電燃氣業","24":"半導體業",
    "25":"電腦及週邊設備業","26":"光電業","27":"通信網路業",
    "28":"電子零組件業","29":"電子通路業","30":"資訊服務業",
    "31":"其他電子業","32":"文化創意業","33":"農業科技業",
    "34":"電子商務","35":"綠能環保","36":"數位雲端",
    "37":"運動休閒","38":"居家生活","W2":"上櫃電子","W3":"上櫃生技",
}


@st.cache_data(ttl=86400)
def get_sector_map():
    headers = {"User-Agent": "Mozilla/5.0"}
    sector_map = {}

    def resolve(raw):
        raw = str(raw).strip()
        if not raw or raw in ("nan", "None", ""):
            return ""
        if raw.isdigit():
            return TWSE_INDUSTRY_CODE.get(raw.zfill(2), f"產業{raw}")
        return TWSE_INDUSTRY_CODE.get(raw, raw)

    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            headers=headers, verify=False, timeout=15
        )
        for item in r.json():
            code = str(item.get("公司代號", "")).strip()
            if code.isdigit() and len(code) == 4:
                ind = resolve(item.get("產業別", ""))
                if ind:
                    sector_map[f"{code}.TW"] = ind
    except Exception:
        pass

    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
            headers=headers, verify=False, timeout=15
        )
        for item in r.json():
            code = str(item.get("SecuritiesCompanyCode", "")).strip()
            if code.isdigit() and len(code) == 4:
                ind = resolve(item.get("IndustryType", ""))
                if ind:
                    sector_map[f"{code}.TWO"] = ind
    except Exception:
        pass

    return sector_map


def get_sector(ticker, sector_map):
    return sector_map.get(ticker, "—")


def clean_ohlcv_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in needed if c in df.columns]].copy()

    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return df


def download_batch_data(tickers_batch):
    if not tickers_batch:
        return {}

    try:
        data = yf.download(
            tickers_batch,
            period="2y",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
            auto_adjust=False
        )

        result_dict = {}

        if len(tickers_batch) == 1:
            df = clean_ohlcv_df(data)
            if not df.empty:
                result_dict[tickers_batch[0]] = df
            return result_dict

        for t in tickers_batch:
            try:
                if t in data.columns.levels[0]:
                    df = data[t].copy()
                    df = clean_ohlcv_df(df)
                    if not df.empty:
                        result_dict[t] = df
            except Exception:
                continue

        return result_dict
    except Exception:
        return {}


def calculate_risk_reward(c_now, sl_price, date_now, custom_target=None):
    sl_price = round(sl_price, 2)
    risk = c_now - sl_price
    if risk <= 0:
        return None

    if custom_target:
        target_price = round(custom_target, 2)
        potential_profit = (target_price - c_now) / c_now
    else:
        target_price = round(c_now + risk * 2.0, 2)
        potential_profit = (risk * 2.0) / c_now

    today_str = date.today().strftime('%Y-%m-%d')
    signal_date = date_now.strftime('%Y-%m-%d')

    return {
        "訊號日期": f"🆕 {signal_date}" if signal_date == today_str else signal_date,
        "停損價(SL)": sl_price,
        "停利價(TP)": target_price,
        "潛在獲利": f"{round(potential_profit * 100, 1)}%"
    }


# -------------------------------------------------
# 回測引擎
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        lookback = months * 22
        if len(df) < lookback + 20:
            return None

        trades, in_position = [], False
        entry_price = target_price = stop_loss_price = 0

        start_idx = max(len(df) - lookback, 125)

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
        bb20  = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        for i in range(start_idx, len(df) - 1):
            c_curr = float(close.iloc[i])
            h_curr = float(high.iloc[i])
            l_curr = float(low.iloc[i])
            o_curr = float(open_p.iloc[i])

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
                    curr_upper = bb20.bollinger_hband().iloc[i]
                    if pd.notna(curr_upper):
                        target_price = float(curr_upper)
                continue

            signal = False
            curr_sl = curr_tp = 0

            if volume.iloc[i] < 500_000:
                continue

            if strategy_type == "bollinger_mid":
                mid = bb20.bollinger_mavg().iloc[i]
                upper = bb20.bollinger_hband().iloc[i]
                prev_mid = bb20.bollinger_mavg().iloc[i - 1]

                if pd.isna(mid) or pd.isna(upper) or pd.isna(prev_mid):
                    continue

                if c_curr > ma120.iloc[i]:
                    if abs(c_curr - mid) / mid <= 0.015 and mid > prev_mid:
                        if c_curr < o_curr and volume.iloc[i] < volume.iloc[i - 1]:
                            signal = True
                            curr_sl = mid * 0.97
                            curr_tp = upper

            elif strategy_type == "washout" and i >= 1:
                c_p = float(close.iloc[i - 1])
                o_p = float(open_p.iloc[i - 1])
                m5c = float(ma5.iloc[i])
                m5p = float(ma5.iloc[i - 1])
                bias = (c_curr - m5c) / m5c * 100

                if (
                    c_p < o_p and c_p > m5p and
                    c_curr < o_curr and c_curr > m5c and
                    c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i] and
                    bias <= 6
                ):
                    signal = True
                    curr_sl = m5c * 0.99
                    curr_tp = c_curr * 1.12

            elif strategy_type == "pullback_buy_breakout" and i >= 1:
                h_p = float(high.iloc[i - 1])

                if (
                    c_curr > o_curr and
                    (c_curr - o_curr) / o_curr * 100 > 2.0 and
                    c_curr > h_p and
                    c_curr > ma5.iloc[i] and c_curr > ma10.iloc[i] and
                    c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i]
                ):
                    signal = True
                    curr_sl = h_p * 0.99
                    curr_tp = c_curr * 1.15

            elif strategy_type == "strong_trend_ma5" and i >= 1:
                if c_curr >= 20 and c_curr > ma120.iloc[i]:
                    l_p = float(low.iloc[i - 1])
                    m5 = float(ma5.iloc[i])
                    m10 = float(ma10.iloc[i])

                    if l_curr < l_p and (l_curr < m5 or l_curr < m10) and (c_curr > m5 * 1.001 or c_curr > m10 * 1.001):
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

        wc = sum(1 for p in trades if p > 0)
        return {
            "回測勝率": f"{round(wc / len(trades) * 100, 1)}%",
            "平均獲利": f"{round(sum(trades) / len(trades) * 100, 2)}%",
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

        close = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        c_now = float(close.iloc[-1])
        o_now = float(open_p.iloc[-1])
        v_now = float(volume.iloc[-1])
        v_prev = float(volume.iloc[-2])

        ma120_now = ta.trend.sma_indicator(close, 120).iloc[-1]
        if pd.isna(ma120_now) or v_now < 500_000 or c_now < float(ma120_now):
            return None

        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        mid_now = bb.bollinger_mavg().iloc[-1]
        upper_now = bb.bollinger_hband().iloc[-1]
        mid_prev = bb.bollinger_mavg().iloc[-2]

        if pd.isna(mid_now) or pd.isna(upper_now) or pd.isna(mid_prev):
            return None

        mid_now = float(mid_now)
        upper_now = float(upper_now)
        mid_prev = float(mid_prev)

        if abs(c_now - mid_now) / mid_now > 0.015:
            return None
        if mid_now < mid_prev:
            return None
        if c_now >= o_now:
            return None
        if v_now >= v_prev:
            return None

        bt_res = run_backtest(df, "bollinger_mid", backtest_months)
        rr = calculate_risk_reward(c_now, mid_now * 0.97, df.index[-1], custom_target=upper_now)
        if rr is None:
            return None

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            "布林中線": round(mid_now, 2),
            "布林上軌": round(upper_now, 2),
            **rr,
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "中線黑K量縮 🌀"
        }
    except Exception:
        return None


def strategy_washout_rebound(ticker, name, df, backtest_months):
    try:
        if len(df) < 125:
            return None

        close = df["Close"]
        open_p = df["Open"]
        volume = df["Volume"]

        if float(volume.iloc[-1]) < 500_000:
            return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        c_now = float(close.iloc[-1])
        o_now = float(open_p.iloc[-1])
        c_prev = float(close.iloc[-2])
        o_prev = float(open_p.iloc[-2])
        ma5_now = float(ma5.iloc[-1])
        ma5_prev = float(ma5.iloc[-2])

        if c_prev >= o_prev or c_prev <= ma5_prev or c_now >= o_now or c_now <= ma5_now:
            return None

        if not (
            c_now > float(ma10.iloc[-1]) and
            c_now > float(ma20.iloc[-1]) and
            c_now > float(ma60.iloc[-1]) and
            c_now > float(ma120.iloc[-1])
        ):
            return None

        bias_5 = (c_now - ma5_now) / ma5_now * 100
        if bias_5 > 6:
            return None

        pct_change = (c_now - c_prev) / c_prev * 100
        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now * 0.99, df.index[-1])
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
    try:
        if len(df) < 130:
            return None

        close = df["Close"]
        open_p = df["Open"]
        high = df["High"]
        volume = df["Volume"]

        c_now = float(close.iloc[-1])
        o_now = float(open_p.iloc[-1])
        h_prev = float(high.iloc[-2])
        v_now = float(volume.iloc[-1])

        if v_now < 1_000_000 or c_now < 10 or ticker.startswith("28"):
            return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma20 = ta.trend.sma_indicator(close, 20)
        ma60 = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        if c_now <= float(ma5.iloc[-1]) or c_now <= o_now:
            return None

        body_pct = (c_now - o_now) / o_now * 100
        if body_pct <= 2.0 or c_now <= h_prev:
            return None

        if not (
            c_now > float(ma10.iloc[-1]) and
            c_now > float(ma20.iloc[-1]) and
            c_now > float(ma60.iloc[-1]) and
            c_now > float(ma120.iloc[-1])
        ):
            return None

        total_pct = (c_now - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        bt_res = run_backtest(df, "pullback_buy_breakout", backtest_months)
        rr = calculate_risk_reward(c_now, h_prev * 0.99, df.index[-1])
        if rr is None:
            return None

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            "今日漲幅": f"{round(total_pct, 2)}%",
            "紅K實體": f"{round(body_pct, 2)}%",
            "昨日最高": round(h_prev, 2),
            "5MA": round(float(ma5.iloc[-1]), 2),
            **rr,
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "回後買上漲 🚀"
        }
    except Exception:
        return None


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

        if v_now < 1_000_000 or c_now <= 20:
            return None

        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now = float(ma5.iloc[-1])
        ma10_now = float(ma10.iloc[-1])
        ma120_now = float(ma120.iloc[-1])

        if c_now <= ma120_now:
            return None

        if not (
            l_now < l_prev and
            (l_now < ma5_now or l_now < ma10_now) and
            (c_now > ma5_now * 1.001 or c_now > ma10_now * 1.001)
        ):
            return None

        reclaim_label = "5MA" if c_now > ma5_now * 1.001 else "10MA"
        bt_res = run_backtest(df, "strong_trend_ma5", backtest_months)
        rr = calculate_risk_reward(c_now, l_now, df.index[-1])
        if rr is None:
            return None

        return {
            "代號": ticker,
            "名稱": name,
            "現價": round(c_now, 2),
            "今日低點": round(l_now, 2),
            "昨日低點": round(l_prev, 2),
            "5MA": round(ma5_now, 2),
            "10MA": round(ma10_now, 2),
            "站回均線": reclaim_label,
            **rr,
            **(bt_res or {}),
            "外資詳情": get_chip_link(ticker),
            "狀態": "強勢回測 5/10MA（底底低洗盤）⚡"
        }
    except Exception:
        return None


# ✅ 已移除：週線盤整突破
STRATEGIES = {
    "⚡ 強勢回測 5/10MA (底底低)": strategy_strong_trend_ma5,
    "🌀 布林中線 (量縮黑K)": strategy_bollinger_mid,
    "🛁 爆量回檔 (雙黑K站5MA)": strategy_washout_rebound,
    "🚀 回後買上漲 (紅K過昨高)": strategy_pullback_buy_breakout,
}

# -------------------------------------------------
# Sidebar
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
# 頁籤
# -------------------------------------------------
tab_scan, tab_portfolio = st.tabs(["🔍 策略掃描", "📦 我的庫存"])

# =================================================
# 📦 我的庫存
# =================================================
with tab_portfolio:
    st.subheader(f"📦 {user_name} 的庫存清單")

    st.markdown("### 🔔 停損停利警示")
    col_a1, col_a2 = st.columns([3, 1])

    with col_a1:
        alert_email = st.text_input("通知信箱（預設為登入帳號）", value=user_email, key="alert_email")

    with col_a2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔍 立即檢查並寄信", type="primary"):
            df_check = load_portfolio(portfolio_ws)
            if df_check.empty:
                st.info("庫存是空的，無法檢查。")
            else:
                with st.spinner("正在抓取即時價格並檢查..."):
                    triggered = check_and_alert(df_check, alert_email, force=True)

                if triggered:
                    for t in triggered:
                        st.warning(
                            f"{t['類型']} **{t['代號']} {t['名稱']}** "
                            f"現價 {t['現價']}｜停損 {t['停損價'] or '—'}｜停利 {t['停利價'] or '—'}"
                        )
                    st.success(f"✅ 已寄出通知信到 {alert_email}")
                else:
                    st.success("✅ 所有持股均在停損停利範圍內，無需通知。")

    st.markdown("---")

    with st.expander("➕ 手動新增持股", expanded=False):
        full_map_p = st.session_state.get("stock_map", {}) or get_all_tw_tickers()

        col1, col2, col3 = st.columns(3)
        with col1:
            p_ticker = st.text_input("代號 (例: 2330.TW)", key="p_ticker")
            p_name   = st.text_input("名稱", value=full_map_p.get(p_ticker, ""), key="p_name")
            p_sector = st.text_input("族群", key="p_sector")

        with col2:
            p_strategy  = st.selectbox("策略", list(STRATEGIES.keys()), key="p_strategy")
            p_buy_price = st.number_input("買入價", min_value=0.0, step=0.1, key="p_buy_price")
            p_lots      = st.number_input("張數", min_value=0, step=1, key="p_lots")

        with col3:
            p_sl   = st.number_input("停損價", min_value=0.0, step=0.1, key="p_sl")
            p_tp   = st.number_input("停利價", min_value=0.0, step=0.1, key="p_tp")
            p_note = st.text_input("備註", key="p_note")
            p_date = st.date_input("買入日期", value=date.today(), key="p_date")

        if st.button("✅ 新增到庫存", type="primary"):
            if not p_ticker:
                st.error("請輸入代號！")
            elif p_buy_price <= 0:
                st.error("請輸入買入價！")
            else:
                append_to_portfolio(portfolio_ws, {
                    "買入日期": str(p_date),
                    "代號": p_ticker,
                    "名稱": p_name,
                    "族群": p_sector,
                    "策略": p_strategy,
                    "買入價": p_buy_price,
                    "成本總額(元)": round(p_buy_price * p_lots * 1000, 0),
                    "張數": p_lots,
                    "停損價": p_sl,
                    "停利價": p_tp,
                    "備註": p_note,
                })
                st.success(f"✅ {p_ticker} 已新增到庫存！")
                st.rerun()

    st.markdown("---")
    df_port = load_portfolio(portfolio_ws)

    if df_port.empty:
        st.info("庫存是空的，可以從掃描結果一鍵加入，或手動新增。")
    else:
        def get_current_price(ticker):
            try:
                hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
                if not hist.empty and "Close" in hist.columns:
                    return round(float(hist["Close"].dropna().iloc[-1]), 2)
            except Exception:
                pass
            return None

        if st.button("🔄 更新即時價格"):
            with st.spinner("抓取即時價格..."):
                st.session_state["live_prices"] = {
                    tk: get_current_price(str(tk)) for tk in df_port["代號"].unique()
                }

        live_prices = st.session_state.get("live_prices", {})
        df_display = df_port.copy()
        df_display["現價"] = df_display["代號"].map(lambda x: live_prices.get(x, "—"))

        def calc_pnl(row):
            try:
                if row["現價"] == "—":
                    return "—"
                buy = float(row["買入價"])
                now = float(row["現價"])
                return f"{round((now - buy) / buy * 100, 2)}%"
            except Exception:
                return "—"

        df_display["損益(%)"] = df_display.apply(calc_pnl, axis=1)
        st.dataframe(df_display, use_container_width=True)

        st.markdown("##### 🗑️ 刪除持股")
        del_idx = st.number_input(
            "輸入要刪除的列號（從 0 開始）",
            min_value=0,
            max_value=max(0, len(df_port)-1),
            step=1,
            key="del_idx"
        )

        if st.button("確認刪除"):
            delete_portfolio_row(portfolio_ws, int(del_idx))
            st.success("已刪除！")
            st.rerun()

# =================================================
# 🔍 策略掃描
# =================================================
with tab_scan:

    def render_results(strategy_name, rows):
        if not rows:
            return

        st.subheader(f"📊 {strategy_name}　（{len(rows)} 筆）")
        df_res = pd.DataFrame(rows)

        if "布林中線" in df_res.columns:
            target_cols = [
                "代號","名稱","族群","現價","布林中線","布林上軌",
                "停損價(SL)","停利價(TP)","潛在獲利","外資詳情"
            ]
        elif "紅K實體" in df_res.columns:
            target_cols = [
                "代號","名稱","族群","現價","今日漲幅","紅K實體",
                "昨日最高","5MA","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"
            ]
        elif "今日低點" in df_res.columns:
            target_cols = [
                "代號","名稱","族群","現價","今日低點","昨日低點","5MA","10MA",
                "站回均線","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"
            ]
        elif "5日乖離率" in df_res.columns:
            target_cols = [
                "代號","名稱","族群","現價","漲幅","5日乖離率",
                "停損價(SL)","停利價(TP)","潛在獲利","外資詳情"
            ]
        else:
            target_cols = [
                "代號","名稱","族群","現價","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"
            ]

        final_cols = [c for c in target_cols if c in df_res.columns]

        if "回測勝率" in df_res.columns:
            final_cols += ["回測勝率", "平均獲利", "總交易"]

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
                "外資詳情": st.column_config.LinkColumn("外資詳情", display_text="查看數據")
            }
        )

        st.markdown("**➕ 將篩選結果加入庫存：**")
        df_port_now = load_portfolio(portfolio_ws)
        existing_codes = set(df_port_now["代號"].astype(str).tolist()) if not df_port_now.empty else set()

        cols_add = st.columns(min(len(rows), 5))
        for idx, row in enumerate(rows):
            with cols_add[idx % 5]:
                btn_key = f"add_{strategy_name}_{row['代號']}_{idx}"
                if row["代號"] in existing_codes:
                    st.button(f"✅ {row['代號']} 已在庫存", key=f"exists_{btn_key}", disabled=True)
                else:
                    if st.button(f"{row['代號']} {row['現價']}", key=btn_key):
                        append_to_portfolio(portfolio_ws, {
                            "買入日期": date.today().strftime("%Y-%m-%d"),
                            "代號": row.get("代號", ""),
                            "名稱": row.get("名稱", ""),
                            "族群": row.get("族群", ""),
                            "策略": strategy_name,
                            "買入價": row.get("現價", ""),
                            "成本總額(元)": "",
                            "張數": "",
                            "停損價": row.get("停損價(SL)", ""),
                            "停利價": row.get("停利價(TP)", ""),
                            "備註": "",
                        })
                        st.success(f"✅ {row['代號']} 已加入庫存！")
                        st.rerun()

    if st.button("開始掃描", type="primary"):
        if not tickers:
            st.error("沒有股票代碼！")
        else:
            if "sector_map" not in st.session_state:
                with st.spinner("載入產業族群資料..."):
                    st.session_state["sector_map"] = get_sector_map()

            _sector_map = st.session_state["sector_map"]
            result = {k: [] for k in selected}

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i in range(0, len(tickers), 50):
                progress_bar.progress(min((i + 50) / len(tickers), 1.0))
                batch_tickers = tickers[i: i + 50]
                status_text.text(f"掃描中... {i+1} ~ {min(i+50, len(tickers))} / {len(tickers)} 檔")

                data_dict = download_batch_data(batch_tickers)
                if not data_dict:
                    time.sleep(1)
                    continue

                for t, df_data in data_dict.items():
                    name = stock_map.get(t, t)
                    for k in selected:
                        try:
                            r = STRATEGIES[k](t, name, df_data, backtest_period)
                            if r:
                                r["族群"] = get_sector(t, _sector_map)
                                r["策略"] = k
                                result[k].append(r)
                        except Exception:
                            continue

                time.sleep(0.2)

            progress_bar.empty()
            status_text.empty()
            st.session_state["scan_results"] = result
            st.rerun()

    if "scan_results" in st.session_state:
        result = st.session_state["scan_results"]
        total_hits = sum(len(v) for v in result.values())

        for k in selected:
            render_results(k, result.get(k, []))

        if total_hits == 0:
            st.info("掃描完成，沒有符合條件的股票。")
        else:
            st.success(f"✅ 掃描完成，共找到 {total_hits} 筆符合訊號。")
