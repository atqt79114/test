import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
import re
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
            triggered.append({"代號": ticker, "名稱": name, "現價": price,
                               "停損價": sl, "停利價": tp, "類型": hit_type})
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
            "<html><body><h2>📊 台股庫存價格警示</h2>"
            "<p>以下持股已觸發停損或停利條件，請留意：</p>"
            "<table style='border-collapse:collapse;width:100%;font-family:sans-serif'>"
            "<tr style='background:#f0f0f0'>"
            "<th style='padding:8px;border:1px solid #ddd'>類型</th>"
            "<th style='padding:8px;border:1px solid #ddd'>股票</th>"
            "<th style='padding:8px;border:1px solid #ddd'>現價</th>"
            "<th style='padding:8px;border:1px solid #ddd'>停損價</th>"
            "<th style='padding:8px;border:1px solid #ddd'>停利價</th>"
            f"</tr>{rows_html}</table>"
            "<br><p style='color:gray;font-size:12px'>此信由台股潛伏策略篩選器自動發送，請勿回覆。</p>"
            "</body></html>"
        )
        send_alert_email(to_email, f"台股庫存警示：{len(triggered)} 筆觸發（{date.today()}）", html_body)
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
#### 🧠 停利邏輯說明
- 🌀 **布林突破**：站穩所有均線 + 量增1.5倍，停損 = 布林中軌，停利採 1:2 風報比（上限 +12%）
- 🛁 🚀 ⚡ **其他策略**：**1:2 風報比，但停利上限 +12%**
  - 例：現價 100，停損 97（風險 3元）→ 停利 = min(106, 112) = 106
  - 避免停損距離過小導致停利目標離譜地遠
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


# -------------------------------------------------
# 🎯 大機率買盤：認購權證成交金額排行 + 個股權證明細
# -------------------------------------------------
@st.cache_data(ttl=3600 * 4)
def get_warrant_call_ranking_detail(top_n=30):
    """
    回傳 (標的排行榜 DataFrame, {標的代號: 該標的權證明細 DataFrame}, 錯誤訊息)

    資料來源:
    1. TWSE OpenAPI 上市權證基本資料 (權證代號 <-> 標的股票 對照表)
       https://openapi.twse.com.tw/v1/opendata/t187ap37_L
    2. TWSE OpenAPI 上市權證每日交易資訊 (權證代號 <-> 成交張數/成交金額)
       https://openapi.twse.com.tw/v1/opendata/t187ap42_L
       (注意:STOCK_DAY_ALL 只涵蓋一般股票,不含權證,故改用此專屬端點)

    邏輯:
    - 只統計「認購」類別的權證(視為潛在買盤避險行為的間接推論指標)
    - 依「標的股票代號」把旗下所有認購權證的成交金額加總、排序
    - 同時保留每檔權證各自的成交明細,供點開查看
    """
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r1 = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap37_L",
            headers=headers, verify=False, timeout=20
        )
        warrants = r1.json()
    except Exception as e:
        return None, None, f"權證基本資料抓取失敗：{e}"

    try:
        r2 = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap42_L",
            headers=headers, verify=False, timeout=20
        )
        warrant_trading = r2.json()
    except Exception as e:
        return None, None, f"權證每日交易資訊抓取失敗：{e}"

    if not isinstance(warrant_trading, list) or not warrant_trading:
        return None, None, "t187ap42_L 回傳格式異常或為空(可能非交易日或當日尚無權證成交)"

    _wt_sample = warrant_trading[0] if warrant_trading else {}

    turnover_map = {}
    volume_map = {}  # 單位:張(1張=1000權證單位)
    for row in warrant_trading:
        try:
            code = str(row.get("權證代號", "")).strip()
            amount_raw = str(row.get("成交金額", "0")).replace(",", "").strip()
            volume_raw = str(row.get("成交張數", "0")).replace(",", "").strip()
            turnover_map[code] = int(float(amount_raw or 0))
            volume_map[code] = int(float(volume_raw or 0))  # API 實際單位為「股」,顯示前需 /1000 換算成「張」
        except Exception:
            continue

    # 「標的證券/指數」欄位只有名稱、沒有代號,反查 get_all_tw_tickers() 的代號↔名稱對照表
    name_to_code = {}
    try:
        full_ticker_map = get_all_tw_tickers()  # {"2330.TW": "台積電", ...}
        for ticker, nm in full_ticker_map.items():
            name_to_code[nm.strip()] = ticker.split(".")[0]
    except Exception:
        pass

    def parse_underlying(text):
        name = (text or "").strip()
        if not name:
            return None, None
        code = name_to_code.get(name)
        return code, name

    def days_to_expiry(expiry_str):
        """TWSE 日期欄位為民國年格式(例:1150828 = 民國115年08月28日 = 西元2026/08/28)"""
        try:
            s = str(expiry_str).strip()
            if not s or len(s) < 6:
                return None
            roc_year = int(s[:-4])
            month_day = s[-4:]
            greg_year = roc_year + 1911
            d = pd.to_datetime(f"{greg_year}{month_day}", format="%Y%m%d")
            return max((d - pd.Timestamp.now().normalize()).days, 0)
        except Exception:
            return None

    ranking = {}
    detail_by_underlying = {}
    debug = {
        "權證基本資料筆數": len(warrants),
        "全市場成交資訊筆數": len(warrant_trading),
        "t187ap42_L原始樣本(第一筆)": _wt_sample,
        "股票代號對照表筆數": len(name_to_code),
        "類別為認購的權證數": 0,
        "能解析出標的代號的數量": 0,
        "有對應到成交金額(>0)的數量": 0,
    }
    sample_categories = set()
    sample_underlying_raw = []

    for w in warrants:
        category = (w.get("權證類型") or "").strip()
        sample_categories.add(category)
        if "購" not in category:
            continue  # 只統計認購權證
        debug["類別為認購的權證數"] += 1

        warrant_code = (w.get("權證代號") or "").strip()
        warrant_name = (w.get("權證簡稱") or "").strip()
        underlying_raw = w.get("標的證券/指數", "")
        if len(sample_underlying_raw) < 5:
            sample_underlying_raw.append(underlying_raw)
        u_code, u_name = parse_underlying(underlying_raw)
        if not u_code:
            continue  # 標的是指數(如台指)則略過,只做個股排行
        debug["能解析出標的代號的數量"] += 1

        amount = turnover_map.get(warrant_code, 0)
        if amount <= 0:
            continue
        debug["有對應到成交金額(>0)的數量"] += 1

        volume = volume_map.get(warrant_code, 0)
        expiry_days = days_to_expiry(w.get("履約截止日"))

        if u_code not in ranking:
            ranking[u_code] = {"代號": u_code, "名稱": u_name, "成交金額(萬)": 0, "權證檔數": 0}
        ranking[u_code]["成交金額(萬)"] += amount
        ranking[u_code]["權證檔數"] += 1

        detail_by_underlying.setdefault(u_code, []).append({
            "權證代碼": warrant_code,
            "權證名稱": warrant_name,
            "分類": category,
            "到期天數": expiry_days,
            "成交張數": round(volume / 1000),
            "成交金額(萬)": round(amount / 10000, 1),
        })

    debug["出現過的類別值(前幾種)"] = list(sample_categories)[:10]
    debug["標的欄位原始樣本"] = sample_underlying_raw
    debug["warrant_code範例"] = [str(w.get("權證代號", "")) for w in warrants[:5]]
    debug["turnover_map的key範例"] = list(turnover_map.keys())[:5]
    st.session_state["_warrant_debug"] = debug

    if not ranking:
        return None, None, "目前無資料(可能非交易日或 TWSE 尚未更新,詳見下方除錯資訊)"

    df_ranking = pd.DataFrame(
        sorted(ranking.values(), key=lambda x: x["成交金額(萬)"], reverse=True)[:top_n]
    )
    df_ranking["成交金額(萬)"] = round(df_ranking["成交金額(萬)"] / 10000, 1)
    df_ranking.insert(0, "排名", range(1, len(df_ranking) + 1))

    detail_dfs = {
        code: pd.DataFrame(rows_).sort_values("成交金額(萬)", ascending=False)
        for code, rows_ in detail_by_underlying.items()
    }
    return df_ranking, detail_dfs, None


def get_warrant_last_prices(warrant_codes):
    """
    按需查詢權證最新成交價。
    TWSE 開放資料沒有權證收盤價欄位,改用基本市況報導網站的即時報價 API,
    一次最多帶 100 檔代號批次查詢,避免觸發 TWSE 的請求頻率限制。

    注意:這是「即時報價」系統,z(當前盤中成交價)只有在該權證當下有新成交時才會有值,
    休市日或冷門權證常常是空的。此時退而求其次改用 y(前一交易日收盤價)顯示,
    並標記來源,避免誤把參考價當成精確的當日收盤價。
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    price_map = {}
    codes = list(dict.fromkeys(warrant_codes))  # 去重,保留順序
    batch_size = 100
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        ex_ch = "|".join(f"tse_{c}.tw" for c in batch)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            data = r.json()
            for item in data.get("msgArray", []):
                code = (item.get("c") or "").strip()
                if not code:
                    continue
                z_str = (item.get("z") or "").strip()  # 當前盤中成交價
                y_str = (item.get("y") or "").strip()  # 前一交易日收盤價(備援)
                if z_str and z_str != "-":
                    try:
                        price_map[code] = (float(z_str), "即時")
                        continue
                    except ValueError:
                        pass
                if y_str and y_str != "-":
                    try:
                        price_map[code] = (float(y_str), "前一日收盤")
                    except ValueError:
                        pass
        except Exception:
            continue
    return price_map


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
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def download_batch_data(tickers_batch):
    if not tickers_batch:
        return {}
    try:
        data = yf.download(
            tickers_batch, period="2y", interval="1d",
            group_by="ticker", progress=False, threads=True, auto_adjust=False
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
                    df = clean_ohlcv_df(data[t].copy())
                    if not df.empty:
                        result_dict[t] = df
            except Exception:
                continue
        return result_dict
    except Exception:
        return {}


# -------------------------------------------------
# ★ 核心：停利計算（1:2 風報比，上限 +12%）
# -------------------------------------------------
def calculate_risk_reward(c_now, sl_price, date_now, custom_target=None, rr_cap=0.12):
    """
    custom_target : 直接指定停利
    rr_cap        : 停利上限，預設 12%
    一般策略邏輯  : 停利 = min( 現價 + 風險×2, 現價×1.12 )
    """
    sl_price = round(sl_price, 2)
    risk = c_now - sl_price
    if risk <= 0:
        return None

    today_str   = date.today().strftime('%Y-%m-%d')
    signal_date = date_now.strftime('%Y-%m-%d')

    if custom_target:
        target_price     = round(custom_target, 2)
        potential_profit = (target_price - c_now) / c_now
    else:
        tp_by_rr     = c_now + risk * 2.0          # 1:2 風報比
        tp_capped    = c_now * (1 + rr_cap)         # 上限 +12%
        target_price = round(min(tp_by_rr, tp_capped), 2)
        potential_profit = (target_price - c_now) / c_now

    return {
        "訊號日期":   f"🆕 {signal_date}" if signal_date == today_str else signal_date,
        "停損價(SL)": sl_price,
        "停利價(TP)": target_price,
        "潛在獲利":   f"{round(potential_profit * 100, 1)}%"
    }


# -------------------------------------------------
# 回測輔助：與 calculate_risk_reward 相同的 TP 邏輯
# -------------------------------------------------
def _bt_tp(c, sl, custom_tp=None, rr_cap=0.12):
    if custom_tp is not None:
        return custom_tp
    risk = c - sl
    if risk <= 0:
        return c * 1.05
    return min(c + risk * 2.0, c * (1 + rr_cap))


# -------------------------------------------------
# 回測引擎
# -------------------------------------------------
def run_backtest(df, strategy_type, months):
    try:
        lookback  = months * 22
        if len(df) < lookback + 20:
            return None

        trades, in_position = [], False
        entry_price = target_price = stop_loss_price = 0
        start_idx   = max(len(df) - lookback, 125)

        close  = df["Close"];  open_p = df["Open"]
        high   = df["High"];   low    = df["Low"];  volume = df["Volume"]

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        bb20  = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

        # ★ 回後買上漲用的滾動指標（統一常數，跟即時掃描共用同一套定義）
        PULLBACK_WINDOW = 10
        VOL_RATIO_THRESHOLD = 1.5
        high_roll_max10 = high.rolling(PULLBACK_WINDOW).max().shift(1)
        low_roll_min10  = low.rolling(PULLBACK_WINDOW).min().shift(1)
        vol_roll_avg5   = volume.rolling(5).mean().shift(1)
        touched_series  = ((low <= ma5) | (low <= ma10)).astype(int)
        touched_roll10  = touched_series.rolling(PULLBACK_WINDOW).max().shift(1)

        for i in range(start_idx, len(df) - 1):
            c_curr = float(close.iloc[i])
            h_curr = float(high.iloc[i])
            l_curr = float(low.iloc[i])
            o_curr = float(open_p.iloc[i])

            # ── 持倉管理 ──
            if in_position:
                if h_curr >= target_price:
                    trades.append((target_price - entry_price) / entry_price)
                    in_position = False; continue
                if c_curr < stop_loss_price:
                    trades.append((c_curr - entry_price) / entry_price)
                    in_position = False; continue
                continue

            if volume.iloc[i] < 500_000:
                continue

            signal = False; curr_sl = curr_tp = 0

            # 🌀 布林突破 → 站穩所有均線 + 量增1.5倍，停利 1:2 上限 12%
            elif_check = strategy_type == "bollinger_breakout" and i >= 1
            if elif_check:
                upper = bb20.bollinger_hband().iloc[i]
                mid   = bb20.bollinger_mavg().iloc[i]
                m5, m10, m20 = ma5.iloc[i], ma10.iloc[i], ma20.iloc[i]
                m60, m120    = ma60.iloc[i], ma120.iloc[i]
                v_curr = float(volume.iloc[i]); v_prev = float(volume.iloc[i - 1])
                if all(pd.notna(v) for v in [upper, mid, m5, m10, m20, m60, m120]) and v_prev > 0:
                    if (c_curr > float(upper) and c_curr > float(m5) and c_curr > float(m10)
                            and c_curr > float(m20) and c_curr > float(m60) and c_curr > float(m120)
                            and v_curr >= v_prev * 1.5):
                        signal = True; curr_sl = float(mid)
                        curr_tp = _bt_tp(c_curr, curr_sl)

            # 🛁 爆量回檔 → 1:2 上限 12%
            elif strategy_type == "washout" and i >= 1:
                cp  = float(close.iloc[i-1]); op = float(open_p.iloc[i-1])
                m5c = float(ma5.iloc[i]);     m5p= float(ma5.iloc[i-1])
                bias= (c_curr - m5c) / m5c * 100
                if (cp < op and cp > m5p and c_curr < o_curr and c_curr > m5c and
                        c_curr > ma20.iloc[i] and c_curr > ma60.iloc[i] and
                        c_curr > ma120.iloc[i] and bias <= 6):
                    signal = True; curr_sl = m5c * 0.99
                    curr_tp = _bt_tp(c_curr, curr_sl)

            # 🚀 回後買上漲 → 回檔確認 + 近期高點 + 量增1.5倍，停利1:2上限12%
            elif strategy_type == "pullback_buy_breakout" and i >= 1:
                rh = high_roll_max10.iloc[i]
                lm = low_roll_min10.iloc[i]
                va = vol_roll_avg5.iloc[i]
                tp_flag = touched_roll10.iloc[i]
                if pd.notna(rh) and pd.notna(lm) and pd.notna(va) and pd.notna(tp_flag) and va > 0 and o_curr > 0:
                    body_pct_bt = (c_curr - o_curr) / o_curr * 100
                    if (c_curr > o_curr and body_pct_bt > 2.0 and c_curr > rh and
                            c_curr > ma5.iloc[i] and c_curr > ma10.iloc[i] and c_curr > ma20.iloc[i] and
                            c_curr > ma60.iloc[i] and c_curr > ma120.iloc[i] and
                            tp_flag == 1 and volume.iloc[i] >= va * VOL_RATIO_THRESHOLD):
                        signal = True; curr_sl = lm * 0.99
                        curr_tp = _bt_tp(c_curr, curr_sl)

            # ⚡ 強勢回測 5/10MA → 1:2 上限 12%
            elif strategy_type == "strong_trend_ma5" and i >= 1:
                if c_curr >= 20 and c_curr > ma120.iloc[i]:
                    lp = float(low.iloc[i-1])
                    m5 = float(ma5.iloc[i]); m10 = float(ma10.iloc[i])
                    if (l_curr < lp and (l_curr < m5 or l_curr < m10) and
                            (c_curr > m5 * 1.001 or c_curr > m10 * 1.001)):
                        signal = True; curr_sl = l_curr
                        curr_tp = _bt_tp(c_curr, curr_sl)

            if signal:
                in_position = True; entry_price = c_curr
                stop_loss_price = curr_sl; target_price = curr_tp

        if not trades:
            return {"回測勝率": "無訊號", "平均獲利": "0%", "總交易": 0}
        wc = sum(1 for p in trades if p > 0)
        return {
            "回測勝率": f"{round(wc / len(trades) * 100, 1)}%",
            "平均獲利": f"{round(sum(trades) / len(trades) * 100, 2)}%",
            "總交易":   len(trades)
        }
    except Exception:
        return None


# -------------------------------------------------
# 策略函式
# -------------------------------------------------

def strategy_bollinger_breakout(ticker, name, df, backtest_months):
    """布林突破：收盤價突破上軌 + 站穩所有均線 + 量增1.5倍，停利 1:2（上限+12%）"""
    try:
        if len(df) < 125: return None
        close = df["Close"]; volume = df["Volume"]
        c_now = float(close.iloc[-1])
        v_now = float(volume.iloc[-1]); v_prev = float(volume.iloc[-2])
        if v_now < 500_000: return None
        if v_prev <= 0 or v_now < v_prev * 1.5: return None   # ★ 量增 1.5 倍濾網

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now, ma10_now, ma20_now = float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1])
        ma60_now, ma120_now = float(ma60.iloc[-1]), float(ma120.iloc[-1])

        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        upper_now = float(bb.bollinger_hband().iloc[-1])
        mid_now   = float(bb.bollinger_mavg().iloc[-1])
        if pd.isna(upper_now) or pd.isna(mid_now): return None

        # 突破上軌
        if c_now <= upper_now: return None
        # 站穩所有均線
        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now
                and c_now > ma60_now and c_now > ma120_now):
            return None

        vol_ratio = v_now / v_prev
        bt_res = run_backtest(df, "bollinger_breakout", backtest_months)
        rr = calculate_risk_reward(c_now, mid_now, df.index[-1])  # 停損=布林中軌，1:2上限12%
        if rr is None: return None

        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "布林上軌": round(upper_now, 2), "布林中線": round(mid_now, 2),
            "量增倍數": f"{round(vol_ratio, 2)}x",
            **rr, **(bt_res or {}),
            "外資詳情": get_chip_link(ticker), "狀態": "布林突破上軌+站穩均線+量增1.5倍 🌀"
        }
    except Exception:
        return None


def strategy_washout_rebound(ticker, name, df, backtest_months):
    """停利 = 1:2 風報比，上限 +12%"""
    try:
        if len(df) < 125: return None
        close = df["Close"]; open_p = df["Open"]; volume = df["Volume"]
        if float(volume.iloc[-1]) < 500_000: return None
        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)
        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1])
        c_prev= float(close.iloc[-2]); o_prev= float(open_p.iloc[-2])
        ma5_now = float(ma5.iloc[-1]); ma5_prev = float(ma5.iloc[-2])
        if c_prev >= o_prev or c_prev <= ma5_prev or c_now >= o_now or c_now <= ma5_now: return None
        if not (c_now > float(ma10.iloc[-1]) and c_now > float(ma20.iloc[-1]) and
                c_now > float(ma60.iloc[-1]) and c_now > float(ma120.iloc[-1])): return None
        bias_5 = (c_now - ma5_now) / ma5_now * 100
        if bias_5 > 6: return None
        pct_change = (c_now - c_prev) / c_prev * 100
        bt_res = run_backtest(df, "washout", backtest_months)
        rr = calculate_risk_reward(c_now, ma5_now * 0.99, df.index[-1])   # 1:2，上限 12%
        if rr is None: return None
        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "漲幅": f"{round(pct_change, 2)}%",
            "5日乖離率": f"{round(bias_5, 2)}%",
            **rr, **(bt_res or {}),
            "外資詳情": get_chip_link(ticker), "狀態": "強勢回檔黑K 🛁"
        }
    except Exception: return None


def strategy_pullback_buy_breakout(ticker, name, df, backtest_months,
                                    pullback_window=10, vol_ratio_threshold=1.5):
    """回後買上漲：近N日內回檔曾碰5MA/10MA + 站穩所有均線 + 過近期高點 + 量增1.5倍，停利1:2上限+12%"""
    try:
        if len(df) < 130: return None
        close = df["Close"]; open_p = df["Open"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]
        c_now = float(close.iloc[-1]); o_now = float(open_p.iloc[-1])
        v_now = float(volume.iloc[-1])
        if v_now < 1_000_000 or c_now < 10 or ticker.startswith("28"): return None
        if o_now <= 0: return None

        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma20  = ta.trend.sma_indicator(close, 20)
        ma60  = ta.trend.sma_indicator(close, 60)
        ma120 = ta.trend.sma_indicator(close, 120)

        ma5_now, ma10_now, ma20_now = float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1])
        ma60_now, ma120_now = float(ma60.iloc[-1]), float(ma120.iloc[-1])

        # 今日收紅K，實體 >2%
        if c_now <= o_now: return None
        body_pct = (c_now - o_now) / o_now * 100
        if body_pct <= 2.0: return None

        # 站穩所有均線
        if not (c_now > ma5_now and c_now > ma10_now and c_now > ma20_now
                and c_now > ma60_now and c_now > ma120_now):
            return None

        # ★ 前高改用近N日高點（不含今天）
        recent_high = float(high.iloc[-(pullback_window + 1):-1].max())
        if c_now <= recent_high: return None

        # ★ 回檔確認：近N日內（不含今天）曾經觸及或跌破 5MA 或 10MA
        recent_low_s  = low.iloc[-(pullback_window + 1):-1]
        recent_ma5_s  = ma5.iloc[-(pullback_window + 1):-1]
        recent_ma10_s = ma10.iloc[-(pullback_window + 1):-1]
        touched_pullback = ((recent_low_s <= recent_ma5_s) | (recent_low_s <= recent_ma10_s)).any()
        if not touched_pullback: return None

        # ★ 量能相對比較：今日量 ≥ 近5日均量(不含今天) × 1.5倍
        avg_vol_5 = float(volume.iloc[-6:-1].mean())
        if avg_vol_5 <= 0 or v_now < avg_vol_5 * vol_ratio_threshold: return None
        vol_ratio = v_now / avg_vol_5

        # 停損 = 回檔期間最低點 × 0.99（跌破代表假突破）
        pullback_low = float(low.iloc[-(pullback_window + 1):-1].min())

        total_pct = (c_now - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        bt_res = run_backtest(df, "pullback_buy_breakout", backtest_months)
        rr = calculate_risk_reward(c_now, pullback_low * 0.99, df.index[-1])
        if rr is None: return None

        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "今日漲幅": f"{round(total_pct, 2)}%",
            "紅K實體": f"{round(body_pct, 2)}%",
            f"近{pullback_window}日高點": round(recent_high, 2),
            "回檔低點": round(pullback_low, 2),
            "量增倍數(比5日均量)": f"{round(vol_ratio, 2)}x",
            **rr, **(bt_res or {}),
            "外資詳情": get_chip_link(ticker), "狀態": "回檔止跌回升過前高 🚀"
        }
    except Exception:
        return None


def strategy_strong_trend_ma5(ticker, name, df, backtest_months):
    """停利 = 1:2 風報比，上限 +12%"""
    try:
        if len(df) < 130: return None
        close = df["Close"]; low = df["Low"]; volume = df["Volume"]
        c_now = float(close.iloc[-1]); l_now = float(low.iloc[-1])
        l_prev= float(low.iloc[-2]);   v_now = float(volume.iloc[-1])
        if v_now < 1_000_000 or c_now <= 20: return None
        ma5   = ta.trend.sma_indicator(close, 5)
        ma10  = ta.trend.sma_indicator(close, 10)
        ma120 = ta.trend.sma_indicator(close, 120)
        ma5_now = float(ma5.iloc[-1]); ma10_now = float(ma10.iloc[-1])
        ma120_now = float(ma120.iloc[-1])
        if c_now <= ma120_now: return None
        if not (l_now < l_prev and
                (l_now < ma5_now or l_now < ma10_now) and
                (c_now > ma5_now * 1.001 or c_now > ma10_now * 1.001)): return None
        reclaim_label = "5MA" if c_now > ma5_now * 1.001 else "10MA"
        bt_res = run_backtest(df, "strong_trend_ma5", backtest_months)
        rr = calculate_risk_reward(c_now, l_now, df.index[-1])             # 1:2，上限 12%
        if rr is None: return None
        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "今日低點": round(l_now, 2), "昨日低點": round(l_prev, 2),
            "5MA": round(ma5_now, 2), "10MA": round(ma10_now, 2),
            "站回均線": reclaim_label,
            **rr, **(bt_res or {}),
            "外資詳情": get_chip_link(ticker), "狀態": "強勢回測 5/10MA（底底低洗盤）⚡"
        }
    except Exception: return None


# -------------------------------------------------
# 策略集合
# -------------------------------------------------
STRATEGIES = {
    "⚡ 強勢回測 5/10MA (底底低)": strategy_strong_trend_ma5,
    "🌀 布林突破 (站穩所有均線+量增1.5倍)": strategy_bollinger_breakout,
    "🛁 爆量回檔 (雙黑K站5MA)":    strategy_washout_rebound,
    "🚀 回後買上漲 (紅K過昨高)":   strategy_pullback_buy_breakout,
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
    "回測區間 (月)", [3, 6, 9, 12, 24],
    format_func=lambda x: f"過去 {x} 個月"
)

# -------------------------------------------------
# 頁籤
# -------------------------------------------------
tab_scan, tab_portfolio, tab_warrant = st.tabs(["🔍 策略掃描", "📦 我的庫存", "🎯 大機率買盤"])

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
                    "買入日期": str(p_date), "代號": p_ticker, "名稱": p_name,
                    "族群": p_sector, "策略": p_strategy, "買入價": p_buy_price,
                    "成本總額(元)": round(p_buy_price * p_lots * 1000, 0),
                    "張數": p_lots, "停損價": p_sl, "停利價": p_tp, "備註": p_note,
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
        live_prices  = st.session_state.get("live_prices", {})
        df_display   = df_port.copy()
        df_display["現價"] = df_display["代號"].map(lambda x: live_prices.get(x, "—"))

        def calc_pnl(row):
            try:
                if row["現價"] == "—": return "—"
                return f"{round((float(row['現價']) - float(row['買入價'])) / float(row['買入價']) * 100, 2)}%"
            except Exception: return "—"

        df_display["損益(%)"] = df_display.apply(calc_pnl, axis=1)
        st.dataframe(df_display, use_container_width=True)
        st.markdown("##### 🗑️ 刪除持股")
        del_idx = st.number_input(
            "輸入要刪除的列號（從 0 開始）",
            min_value=0, max_value=max(0, len(df_port) - 1), step=1, key="del_idx"
        )
        if st.button("確認刪除"):
            delete_portfolio_row(portfolio_ws, int(del_idx))
            st.success("已刪除！"); st.rerun()

# =================================================
# 🎯 大機率買盤
# =================================================
with tab_warrant:
    st.subheader("🎯 大機率買盤（認購權證成交金額排行）")
    st.caption(
        "統計上市認購權證依標的股票加總成交金額,金額愈高代表發行商避險買盤壓力愈大的可能性愈高。"
        "此為間接推論指標,非即時籌碼,僅供參考,不構成投資建議。點選下方標的可展開查看該標的旗下"
        "每一檔認購權證的成交明細。"
    )
    if st.button("🔄 重新整理排行榜", key="refresh_warrant"):
        get_warrant_call_ranking_detail.clear()

    df_ranking, detail_dfs, warrant_err = get_warrant_call_ranking_detail()
    if warrant_err:
        st.warning(warrant_err)
        debug_info = st.session_state.get("_warrant_debug")
        if debug_info:
            with st.expander("🔧 除錯資訊(請把這裡的內容截圖給我)", expanded=True):
                st.json(debug_info)
    else:
        st.dataframe(
            df_ranking[["排名", "代號", "名稱", "成交金額(萬)", "權證檔數"]],
            use_container_width=True, hide_index=True
        )
        st.markdown("---")
        st.markdown("**查看個股權證明細：**")
        for _, row in df_ranking.iterrows():
            code, name = row["代號"], row["名稱"]
            with st.expander(f"{code} {name}（{row['成交金額(萬)']} 萬 / {row['權證檔數']} 檔）"):
                detail_df = detail_dfs.get(code, pd.DataFrame())
                price_key = f"_warrant_price_{code}"
                if not detail_df.empty:
                    if st.button("💰 載入價格", key=f"load_price_{code}"):
                        with st.spinner("查詢價格中..."):
                            st.session_state[price_key] = get_warrant_last_prices(
                                detail_df["權證代碼"].tolist()
                            )
                    price_map = st.session_state.get(price_key)
                    if price_map:
                        detail_df = detail_df.copy()
                        detail_df.insert(2, "價格", detail_df["權證代碼"].map(
                            lambda c: price_map.get(c, (None, None))[0]
                        ))
                        detail_df.insert(3, "價格來源", detail_df["權證代碼"].map(
                            lambda c: price_map.get(c, (None, None))[1]
                        ))
                        st.caption(
                            "「即時」為當下有新成交的盤中價格;「前一日收盤」為系統查無即時成交時的備援參考價,"
                            "非精確的當日收盤價,休市日或冷門權證較常見這種情況。"
                        )
                st.dataframe(
                    detail_df,
                    use_container_width=True, hide_index=True
                )

# =================================================
# 🔍 策略掃描
# =================================================
with tab_scan:

    def render_results(strategy_name, rows):
        if not rows: return
        st.subheader(f"📊 {strategy_name}　（{len(rows)} 筆）")
        df_res = pd.DataFrame(rows)

        if "布林中線" in df_res.columns:
            target_cols = ["代號","名稱","族群","現價","布林上軌","布林中線","量增倍數",
                           "停損價(SL)","停利價(TP)","潛在獲利","外資詳情"]
        elif "紅K實體" in df_res.columns:
            target_cols = ["代號","名稱","族群","現價","今日漲幅","紅K實體",
                           "昨日最高","5MA","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"]
        elif "今日低點" in df_res.columns:
            target_cols = ["代號","名稱","族群","現價","今日低點","昨日低點",
                           "5MA","10MA","站回均線","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"]
        elif "5日乖離率" in df_res.columns:
            target_cols = ["代號","名稱","族群","現價","漲幅","5日乖離率",
                           "停損價(SL)","停利價(TP)","潛在獲利","外資詳情"]
        else:
            target_cols = ["代號","名稱","族群","現價","停損價(SL)","停利價(TP)","潛在獲利","外資詳情"]

        final_cols = [c for c in target_cols if c in df_res.columns]
        if "回測勝率" in df_res.columns:
            final_cols += ["回測勝率", "平均獲利", "總交易"]
        if "訊號日期" in df_res.columns and "訊號日期" not in final_cols:
            final_cols = ["訊號日期"] + final_cols
        other_cols = [c for c in df_res.columns
                      if c not in final_cols and c not in target_cols and c != "策略"]

        st.dataframe(
            df_res[final_cols + other_cols], use_container_width=True,
            column_config={"外資詳情": st.column_config.LinkColumn("外資詳情", display_text="查看數據")}
        )

        st.markdown("**➕ 將篩選結果加入庫存：**")
        df_port_now    = load_portfolio(portfolio_ws)
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
                            "代號": row.get("代號",""), "名稱": row.get("名稱",""),
                            "族群": row.get("族群",""), "策略": strategy_name,
                            "買入價": row.get("現價",""), "成本總額(元)": "",
                            "張數": "", "停損價": row.get("停損價(SL)",""),
                            "停利價": row.get("停利價(TP)",""), "備註": "",
                        })
                        st.success(f"✅ {row['代號']} 已加入庫存！"); st.rerun()

    if st.button("開始掃描", type="primary"):
        if not tickers:
            st.error("沒有股票代碼！")
        else:
            if "sector_map" not in st.session_state:
                with st.spinner("載入產業族群資料..."):
                    st.session_state["sector_map"] = get_sector_map()
            _sector_map = st.session_state["sector_map"]
            result      = {k: [] for k in selected}
            progress_bar = st.progress(0)
            status_text  = st.empty()

            for i in range(0, len(tickers), 50):
                progress_bar.progress(min((i + 50) / len(tickers), 1.0))
                batch_tickers = tickers[i: i + 50]
                status_text.text(f"掃描中... {i+1} ~ {min(i+50, len(tickers))} / {len(tickers)} 檔")
                data_dict = download_batch_data(batch_tickers)
                if not data_dict:
                    time.sleep(1); continue
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

            progress_bar.empty(); status_text.empty()
            st.session_state["scan_results"] = result
            st.rerun()

    if "scan_results" in st.session_state:
        result     = st.session_state["scan_results"]
        total_hits = sum(len(v) for v in result.values())
        for k in selected:
            render_results(k, result.get(k, []))
        if total_hits == 0:
            st.info("掃描完成，沒有符合條件的股票。")
        else:
            st.success(f"✅ 掃描完成，共找到 {total_hits} 筆符合訊號。")
