import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import requests
import warnings
import time
from datetime import date, datetime
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
# Google OAuth 登入（純手工實作）
# -------------------------------------------------
_CLIENT_ID     = st.secrets["GOOGLE_CLIENT_ID"]
_CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
_REDIRECT_URI  = "https://2sv2r89tp93nexxafg9gdm.streamlit.app/"
_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL     = "https://oauth2.googleapis.com/token"
_USERINFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"
_SCOPE         = "openid email profile"


def build_auth_url() -> str:
    params = {
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    resp = httpx.post(_TOKEN_URL, data={
        "code": code,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "redirect_uri": _REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    return resp.json()


def get_user_info(access_token: str) -> dict:
    resp = httpx.get(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    return resp.json()


params = st.query_params
if "code" in params and "user_info" not in st.session_state:
    code = params["code"]
    token_data = exchange_code_for_token(code)
    if "access_token" in token_data:
        user_info = get_user_info(token_data["access_token"])
        st.session_state["user_info"] = user_info
        st.session_state["connected"] = True
        st.query_params.clear()
        st.rerun()
    else:
        st.error(f"Google 登入失敗：{token_data.get('error_description', token_data)}")
        st.stop()

if not st.session_state.get("connected"):
    st.title("💤 台股潛伏/糾結策略篩選器")
    st.markdown("### 請先登入以使用完整功能（含個人庫存）")
    auth_url = build_auth_url()
    st.markdown(
        f'<a href="{auth_url}" target="_blank">'
        f'<button style="background:#4285F4;color:white;border:none;'
        f'padding:12px 28px;border-radius:6px;font-size:16px;cursor:pointer;">'
        f'🔑 使用 Google 帳號登入（新視窗）</button></a>',
        unsafe_allow_html=True,
    )
    st.info("💡 登入完成後請回到此頁重新整理（F5）即可進入系統。")
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
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)


def get_or_create_user_sheet(gc, spreadsheet_id: str, user_email: str):
    sh = gc.open_by_key(spreadsheet_id)
    sheet_title = user_email[:50].replace("@", "_at_").replace(".", "_")
    try:
        ws = sh.worksheet(sheet_title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_title, rows=1000, cols=12)
        ws.append_row([
            "買入日期", "代號", "名稱", "族群", "策略",
            "買入價", "成本總額(元)", "張數",
            "停損價", "停利價", "備註"
        ])
    return ws


def load_portfolio(ws) -> pd.DataFrame:
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=[
            "買入日期", "代號", "名稱", "族群", "策略",
            "買入價", "成本總額(元)", "張數",
            "停損價", "停利價", "備註"
        ])
    return pd.DataFrame(records)


def append_to_portfolio(ws, row: dict):
    ws.append_row([
        row.get("買入日期", ""), row.get("代號", ""), row.get("名稱", ""),
        row.get("族群", ""), row.get("策略", ""), row.get("買入價", ""),
        row.get("成本總額(元)", ""), row.get("張數", ""),
        row.get("停損價", ""), row.get("停利價", ""), row.get("備註", ""),
    ])


def delete_portfolio_row(ws, row_index: int):
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
# Header
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
    code = ticker.split('.')[0]
    return f"https://tw.stock.yahoo.com/quote/{code}/institutional-trading"


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


@st.cache_data(ttl=86400)
def get_sector_map():
    return {}


def get_sector(ticker, sector_map):
    return sector_map.get(ticker, "—")


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
            if not data.empty:
                result_dict[t] = data
            return result_dict
        for t in tickers_batch:
            try:
                if t in data.columns.levels[0]:
                    df = data[t].copy().dropna(how='all')
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
# 策略（保留你原邏輯精簡版）
# -------------------------------------------------
def strategy_strong_trend_ma5(ticker, name, df, backtest_months):
    try:
        if len(df) < 130:
            return None
        close, low, volume = df["Close"], df["Low"], df["Volume"]
        c_now, l_now = float(close.iloc[-1]), float(low.iloc[-1])
        l_prev, v_now = float(low.iloc[-2]), float(volume.iloc[-1])
        if v_now < 1_000_000 or c_now <= 20:
            return None
        ma5 = ta.trend.sma_indicator(close, 5)
        ma10 = ta.trend.sma_indicator(close, 10)
        ma120 = ta.trend.sma_indicator(close, 120)
        ma5_now, ma10_now, ma120_now = float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma120.iloc[-1])
        if c_now <= ma120_now:
            return None
        cond_lower_low = l_now < l_prev
        cond_intraday_break = (l_now < ma5_now) or (l_now < ma10_now)
        cond_reclaim = (c_now > ma5_now * 1.001) or (c_now > ma10_now * 1.001)
        if not (cond_lower_low and cond_intraday_break and cond_reclaim):
            return None
        reclaim_label = "5MA" if c_now > ma5_now * 1.001 else "10MA"
        rr = calculate_risk_reward(c_now, l_now, df.index[-1])
        if rr is None:
            return None
        return {
            "代號": ticker, "名稱": name, "現價": round(c_now, 2),
            "今日低點": round(l_now, 2), "昨日低點": round(l_prev, 2),
            "5MA": round(ma5_now, 2), "10MA": round(ma10_now, 2), "站回均線": reclaim_label,
            **rr, "外資詳情": get_chip_link(ticker),
            "狀態": "強勢回測 5/10MA（底底低洗盤）⚡"
        }
    except Exception:
        return None


STRATEGIES = {
    "⚡ 強勢回測 5/10MA (底底低)": strategy_strong_trend_ma5,
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
    stock_map = st.session_state.get("stock_map", {})
    if not stock_map:
        st.session_state["stock_map"] = get_all_tw_tickers()
        stock_map = st.session_state["stock_map"]
    limit = st.sidebar.slider("掃描數量", 50, 2000, 300)
    tickers = list(stock_map.keys())[:limit]

st.sidebar.header("策略選擇")
selected = [k for k in STRATEGIES if st.sidebar.checkbox(k, True)]

backtest_period = st.sidebar.selectbox("回測區間 (月)", [3, 6, 9, 12], index=1)

# -------------------------------------------------
# 頁籤
# -------------------------------------------------
tab_scan, tab_portfolio = st.tabs(["🔍 策略掃描", "📦 我的庫存"])

# -------------------------------------------------
# 我的庫存
# -------------------------------------------------
with tab_portfolio:
    st.subheader(f"📦 {user_name} 的庫存清單")

    with st.expander("➕ 手動新增持股", expanded=False):
        full_map_p = st.session_state.get("stock_map", {})
        if not full_map_p:
            full_map_p = get_all_tw_tickers()

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
                cost = round(p_buy_price * p_lots * 1000, 0)
                append_to_portfolio(portfolio_ws, {
                    "買入日期": str(p_date), "代號": p_ticker, "名稱": p_name,
                    "族群": p_sector, "策略": p_strategy, "買入價": p_buy_price,
                    "成本總額(元)": cost, "張數": p_lots,
                    "停損價": p_sl, "停利價": p_tp, "備註": p_note,
                })
                st.success(f"✅ {p_ticker} 已新增到庫存！")
                st.rerun()

    st.markdown("---")
    df_port = load_portfolio(portfolio_ws)

    if df_port.empty:
        st.info("庫存是空的，可以從掃描結果一鍵加入，或手動新增。")
    else:
        st.dataframe(df_port, use_container_width=True)

# -------------------------------------------------
# 掃描結果顯示函式
# -------------------------------------------------
def render_results(strategy_name, rows):
    if not rows:
        return

    st.subheader(f"📊 {strategy_name}　（{len(rows)} 筆）")
    df_res = pd.DataFrame(rows)

    st.dataframe(
        df_res,
        use_container_width=True,
        column_config={
            "外資詳情": st.column_config.LinkColumn("外資詳情", display_text="查看數據")
        }
    )

    st.markdown("### ➕ 將篩選結果加入庫存")
    cols_add = st.columns(min(len(rows), 5))

    for idx, row in enumerate(rows):
        with cols_add[idx % 5]:
            btn_key = f"pick_{strategy_name}_{idx}_{row.get('代號', idx)}"
            if st.button(f"{row['代號']} {row['現價']}", key=btn_key):
                st.session_state["selected_stock"] = {
                    "買入日期": date.today().strftime("%Y-%m-%d"),
                    "代號": row.get("代號", ""),
                    "名稱": row.get("名稱", ""),
                    "族群": row.get("族群", ""),
                    "策略": strategy_name,
                    "買入價": float(row.get("現價", 0) or 0),
                    "張數": 1,
                    "停損價": float(row.get("停損價(SL)", 0) or 0),
                    "停利價": float(row.get("停利價(TP)", 0) or 0),
                    "備註": "",
                }

# -------------------------------------------------
# 掃描頁
# -------------------------------------------------
with tab_scan:
    st.subheader("🔍 策略掃描")

    if st.button("🚀 開始掃描", type="primary"):
        if not tickers:
            st.error("沒有股票代碼！")
        else:
            result = {k: [] for k in selected}
            progress_bar = st.progress(0)
            status_text = st.empty()
            batch_size = 30

            for i in range(0, len(tickers), batch_size):
                progress_bar.progress(min((i + batch_size) / len(tickers), 1.0))
                batch_tickers = tickers[i: i + batch_size]
                status_text.text(f"掃描中... {i+1} ~ {min(i+batch_size, len(tickers))} / {len(tickers)}")

                data_dict = download_batch_data(batch_tickers)
                if not data_dict:
                    time.sleep(0.5)
                    continue

                for t, df_data in data_dict.items():
                    name = stock_map.get(t, t)
                    for k in selected:
                        try:
                            r = STRATEGIES[k](t, name, df_data, backtest_period)
                            if r:
                                r["族群"] = "—"
                                r["策略"] = k
                                result[k].append(r)
                        except Exception:
                            continue

                time.sleep(0.2)

            progress_bar.empty()
            status_text.empty()

            st.session_state["scan_results"] = result

    # 顯示掃描結果
    if "scan_results" in st.session_state:
        scan_results = st.session_state["scan_results"]
        total_hits = sum(len(v) for v in scan_results.values())

        for k in selected:
            render_results(k, scan_results.get(k, []))

        if total_hits == 0:
            st.info("掃描完成，沒有符合條件的股票。")
        else:
            st.success(f"✅ 掃描完成，共找到 {total_hits} 筆符合訊號。")

    # 顯示加入庫存表單（固定放在掃描結果下方）
    if "selected_stock" in st.session_state:
        s = st.session_state["selected_stock"]

        st.markdown("---")
        st.markdown(f"## 📝 加入庫存：{s['代號']} {s['名稱']}")

        c1, c2, c3 = st.columns(3)

        with c1:
            buy_date = st.date_input(
                "買入日期",
                value=datetime.strptime(s["買入日期"], "%Y-%m-%d").date(),
                key=f"scan_buy_date_{s['代號']}"
            )
            buy_price = st.number_input(
                "買入價",
                min_value=0.0,
                step=0.1,
                value=float(s["買入價"]),
                key=f"scan_buy_price_{s['代號']}"
            )
            lots = st.number_input(
                "張數",
                min_value=1,
                step=1,
                value=int(s["張數"]),
                key=f"scan_lots_{s['代號']}"
            )

        with c2:
            stop_loss = st.number_input(
                "停損價",
                min_value=0.0,
                step=0.1,
                value=float(s["停損價"]),
                key=f"scan_sl_{s['代號']}"
            )
            take_profit = st.number_input(
                "停利價",
                min_value=0.0,
                step=0.1,
                value=float(s["停利價"]),
                key=f"scan_tp_{s['代號']}"
            )
            sector = st.text_input(
                "族群",
                value=s["族群"],
                key=f"scan_sector_{s['代號']}"
            )

        with c3:
            note = st.text_input(
                "備註",
                value=s["備註"],
                key=f"scan_note_{s['代號']}"
            )
            strategy_text = st.text_input(
                "策略",
                value=s["策略"],
                key=f"scan_strategy_{s['代號']}"
            )
            total_cost = round(buy_price * lots * 1000, 0)
            st.metric("成本總額(元)", f"{int(total_cost):,}")

        cbtn1, cbtn2 = st.columns(2)

        with cbtn1:
            if st.button("✅ 確認加入庫存", key=f"confirm_add_{s['代號']}", type="primary"):
                append_to_portfolio(portfolio_ws, {
                    "買入日期": str(buy_date),
                    "代號": s["代號"],
                    "名稱": s["名稱"],
                    "族群": sector,
                    "策略": strategy_text,
                    "買入價": buy_price,
                    "成本總額(元)": total_cost,
                    "張數": lots,
                    "停損價": stop_loss,
                    "停利價": take_profit,
                    "備註": note,
                })
                st.success(f"✅ {s['代號']} 已加入庫存！")
                del st.session_state["selected_stock"]
                st.rerun()

        with cbtn2:
            if st.button("❌ 取消", key=f"cancel_add_{s['代號']}"):
                del st.session_state["selected_stock"]
                st.rerun()
