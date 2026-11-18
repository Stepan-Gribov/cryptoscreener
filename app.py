import os
import json
import hashlib
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

from func import CMC, KCN, Bybit, Gate


# =========================
# Page
# =========================
st.set_page_config(page_title="Crypto Screener", layout="wide")
st.title("Crypto Screener Dashboard")


# =========================
# Logs folders
# =========================
LOG_ROOT = os.path.join(os.getcwd(), "logs")
TAB_LOG_DIR = os.path.join(LOG_ROOT, "tables")
META_DIR = os.path.join(LOG_ROOT, "meta")
NEW_ROWS_DIR = os.path.join(LOG_ROOT, "new_rows")

for d in [LOG_ROOT, TAB_LOG_DIR, META_DIR, NEW_ROWS_DIR]:
    os.makedirs(d, exist_ok=True)


# =========================
# Optional auto-refresh helper
# =========================
def maybe_autorefresh(interval_seconds: int) -> None:
    """
    True auto-refresh requires streamlit-autorefresh.
    If not installed, app still works (manual refresh button + logs).
    """
    if interval_seconds <= 0:
        return
    try:
        from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
        st_autorefresh(interval=interval_seconds * 1000, key="global_autorefresh")
    except Exception:
        pass


# =========================
# Time helpers
# =========================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =========================
# Data normalization + diff
# =========================
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    df2 = df.copy().reset_index(drop=True)
    df2 = df2.reindex(sorted(df2.columns), axis=1)
    for c in df2.columns:
        if pd.api.types.is_object_dtype(df2[c]) or pd.api.types.is_string_dtype(df2[c]):
            df2[c] = df2[c].astype(str).str.strip()
    return df2


def row_hashes(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=str)
    ser = df.astype(str).agg("||".join, axis=1)
    return ser.apply(lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest())


def load_latest_snapshot(tab_key: str) -> pd.DataFrame:
    latest_parquet = os.path.join(TAB_LOG_DIR, f"{tab_key}__latest.parquet")
    latest_csv = os.path.join(TAB_LOG_DIR, f"{tab_key}__latest.csv")

    if os.path.exists(latest_parquet):
        try:
            return pd.read_parquet(latest_parquet)
        except Exception:
            pass

    if os.path.exists(latest_csv):
        try:
            return pd.read_csv(latest_csv)
        except Exception:
            return pd.DataFrame()

    return pd.DataFrame()


def read_meta(tab_key: str) -> dict:
    path = os.path.join(META_DIR, f"{tab_key}__meta.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_meta(tab_key: str, meta: dict) -> None:
    path = os.path.join(META_DIR, f"{tab_key}__meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def save_snapshot_and_diff(tab_key: str, fresh_df: pd.DataFrame) -> dict:
    prev_df = normalize_df(load_latest_snapshot(tab_key))
    fresh_df_n = normalize_df(fresh_df)

    prev_hash = set(row_hashes(prev_df).tolist()) if not prev_df.empty else set()
    fresh_hash = row_hashes(fresh_df_n)

    if fresh_df_n.empty:
        new_rows = pd.DataFrame(columns=fresh_df_n.columns)
    else:
        is_new = ~fresh_hash.isin(prev_hash)
        new_rows = fresh_df_n.loc[is_new].copy()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_base = os.path.join(TAB_LOG_DIR, f"{tab_key}__{ts}")
    latest_base = os.path.join(TAB_LOG_DIR, f"{tab_key}__latest")

    # Parquet optional (may fail if missing pyarrow); CSV always written
    try:
        fresh_df_n.to_parquet(snap_base + ".parquet", index=False)
        fresh_df_n.to_parquet(latest_base + ".parquet", index=False)
    except Exception:
        pass

    fresh_df_n.to_csv(snap_base + ".csv", index=False)
    fresh_df_n.to_csv(latest_base + ".csv", index=False)

    new_rows_path = ""
    if not new_rows.empty:
        new_rows_path = os.path.join(NEW_ROWS_DIR, f"{tab_key}__new_rows__{ts}.csv")
        new_rows.to_csv(new_rows_path, index=False)

    meta = {
        "tab_key": tab_key,
        "last_refresh_utc": utc_now_iso(),
        "row_count": int(len(fresh_df_n)),
        "new_rows_count": int(len(new_rows)),
        "latest_csv": latest_base + ".csv",
        "latest_parquet": latest_base + ".parquet",
        "last_snapshot_csv": snap_base + ".csv",
        "last_snapshot_parquet": snap_base + ".parquet",
        "last_new_rows_csv": new_rows_path,
    }
    write_meta(tab_key, meta)
    return meta


def should_refresh(tab_key: str, interval_seconds: int) -> bool:
    if interval_seconds <= 0:
        return False
    meta = read_meta(tab_key)
    last = meta.get("last_refresh_utc")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return True
    now = datetime.now(timezone.utc)
    return (now - last_dt).total_seconds() >= interval_seconds


# =========================
# Sidebar controls
# =========================
st.sidebar.header("Refresh settings")
refresh_minutes = st.sidebar.number_input(
    "Auto-refresh interval (minutes)",
    min_value=0,
    max_value=720,
    value=0,
    step=1,
    help="0 = manual refresh. Tabs 2–7 show the last saved snapshot.",
)
interval_seconds = int(refresh_minutes) * 60
maybe_autorefresh(interval_seconds)


# =========================
# Session state for tabs
# =========================
if "selected_tab" not in st.session_state:
    st.session_state.selected_tab = 0


# =========================
# Tabs
# =========================
tabs = st.tabs([
    "CMC Perpetual",
    "KuCoin Fixed Earn",
    "KuCoin Launchpool",
    "Bybit Fixed Earn",
    "Bybit Launchpool",
    "Gate Fixed Earn",
    "Gate Launchpool"
])

tab1 = tabs[st.session_state.selected_tab]
tab2 = tabs[1]
tab3 = tabs[2]
tab4 = tabs[3]
tab5 = tabs[4]
tab6 = tabs[5]
tab7 = tabs[6]


# ============================================================
# TAB 1 (UNCHANGED LOGIC) — only dataframe param updated to remove warning
# ============================================================
@st.cache_data(ttl=3600)
def load_coin_map(api_key: str) -> pd.DataFrame:
    return CMC(api_key).get_slug()


with tab1:
    api_key = os.getenv("CMC_API_KEY")
    st.title("CoinMarketCap Perpetual Market Screener")

    slug_df = pd.DataFrame(columns=["symbol", "slug"])
    if not api_key:
        st.info("Set CMC_API_KEY in .env to enable the perpetual market search.")
    else:
        try:
            slug_df = load_coin_map(api_key)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            st.warning(f"CoinMarketCap coin list could not be loaded: {type(exc).__name__}.")

    if not slug_df.empty:
        cmc = CMC(api_key)
        slug_df["display"] = slug_df["symbol"] + " - " + slug_df["slug"]
        slug_df = pd.concat([pd.DataFrame([{"symbol": "", "slug": "", "display": ""}]), slug_df], ignore_index=True)

        if st.session_state.get("force_tab1_reload"):
            st.session_state["cmc_search_query"] = st.session_state.get("focus_symbol", "")
            st.session_state.force_tab1_reload = False

        search_query = st.text_input("Search coin symbol or name:", key="cmc_search_query")

        if "show_clicked" not in st.session_state:
            st.session_state.show_clicked = False
        if "last_slug" not in st.session_state:
            st.session_state.last_slug = ""

        if search_query.strip():
            filtered_df = slug_df[
                slug_df["symbol"].str.contains(search_query, case=False, na=False, regex=False) |
                slug_df["slug"].str.contains(search_query, case=False, na=False, regex=False)
            ]
            exact_matches = filtered_df[filtered_df["symbol"].str.lower() == search_query.lower()]
            partial_matches = filtered_df[~filtered_df.index.isin(exact_matches.index)]
            combined_df = pd.concat([exact_matches, partial_matches])
            selected_df = combined_df if not combined_df.empty else slug_df
        else:
            selected_df = slug_df

        selected = st.selectbox(
            "Select a coin:",
            selected_df["display"],
            key="cmc_coin_selectbox"
        )

        if st.button("Show", key="cmc_show_button"):
            slug = selected_df.loc[selected_df["display"] == selected, "slug"].values[0]
            st.session_state.last_slug = slug
            st.session_state.show_clicked = True

        if search_query.strip() and not st.session_state.show_clicked:
            if not selected_df.empty:
                st.session_state.last_slug = selected_df.iloc[0]["slug"]
                st.session_state.show_clicked = True

        if st.session_state.show_clicked and st.session_state.last_slug:
            with st.spinner("Fetching perpetual pairs..."):
                try:
                    df = cmc.fetch_perpetual_pairs(st.session_state.last_slug)
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    st.warning(f"Perpetual pairs could not be loaded: {type(exc).__name__}.")
                else:
                    st.subheader("Perp Trading Exchanges")
                    st.dataframe(df, width="stretch", height=1000)


# =========================
# Live fetchers (used ONLY to refresh logs)
# =========================
def fetch_kucoin_fixed() -> pd.DataFrame:
    return KCN().fixed_earn()

def fetch_kucoin_launch() -> pd.DataFrame:
    return KCN().launchpool()

def fetch_bybit_fixed() -> pd.DataFrame:
    return Bybit().fixed_earn()

def fetch_bybit_launch() -> pd.DataFrame:
    return Bybit().launchpool()

def fetch_gate_fixed() -> pd.DataFrame:
    return Gate().fixed_earn()

def fetch_gate_launch() -> pd.DataFrame:
    return Gate().launchpool()


TAB_SPECS = {
    "kucoin_fixed":  {"fetch": fetch_kucoin_fixed},
    "kucoin_launch": {"fetch": fetch_kucoin_launch},
    "bybit_fixed":   {"fetch": fetch_bybit_fixed},
    "bybit_launch":  {"fetch": fetch_bybit_launch},
    "gate_fixed":    {"fetch": fetch_gate_fixed},
    "gate_launch":   {"fetch": fetch_gate_launch},
}


def refresh_if_needed(tab_key: str, force: bool = False) -> dict:
    if not force and not should_refresh(tab_key, interval_seconds):
        return read_meta(tab_key) or {}

    try:
        df_live = TAB_SPECS[tab_key]["fetch"]()
        return save_snapshot_and_diff(tab_key, df_live)
    except Exception as e:
        meta = read_meta(tab_key) or {}
        meta["last_error"] = str(e)
        write_meta(tab_key, meta)
        return meta


def load_from_logs(tab_key: str) -> pd.DataFrame:
    return load_latest_snapshot(tab_key)


def show_last_refresh(meta: dict) -> None:
    last = meta.get("last_refresh_utc", "never")
    st.caption(f"Last refresh (UTC): **{last}**")
    if meta.get("last_error"):
        st.warning(f"Last refresh error: {meta['last_error']}")


# =========================
# TAB 2 — KuCoin Fixed Earn (kept as you had earlier in your code; loads from logs)
# =========================
with tab2:
    st.title("KuCoin Fixed Earn")

    meta = refresh_if_needed("kucoin_fixed", force=False)
    if st.button("🔄 Refresh", key="refresh_fixed"):
        meta = refresh_if_needed("kucoin_fixed", force=True)

    show_last_refresh(meta)

    fixed_df = load_from_logs("kucoin_fixed")
    if fixed_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        fixed_df["status"] = fixed_df["status"].astype(str).str.strip().str.lower()
        statuses = sorted(fixed_df["status"].dropna().unique().tolist())
        default_status = ["ongoing"] if "ongoing" in statuses else statuses

        selected_status = st.multiselect(
            "Filter by status",
            options=statuses,
            default=default_status,
            key="kucoin_fixed_status_multiselect"
        )
        filtered = fixed_df[fixed_df["status"].isin(selected_status)].sort_values("APR", ascending=False)

        st.markdown("### Select asset to see futures:")
        col_header = st.columns([1, 1, 1, 1, 1])
        col_header[0].markdown("**Asset**")
        col_header[1].markdown("**APR**")
        col_header[2].markdown("**Lock-up**")
        col_header[3].markdown("**Status**")
        col_header[4].markdown("**Action**")

        for i, row in filtered.iterrows():
            cols = st.columns([1, 1, 1, 1, 1])
            cols[0].markdown(f"**{row['asset']}**")
            cols[1].markdown(f"{row['APR']*100:.2f}%")
            cols[2].markdown(f"{row['lock_up_period']} days")
            cols[3].markdown(f"_{row['status']}_")

            if cols[4].button("→ Futures", key=f"kucoin_fixed_fut_{i}"):
                st.session_state["search_query"] = row["asset"]
                st.session_state["focus_symbol"] = row["asset"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()


# ============================================================
# TABS 3–7: DISPLAY STRUCTURE IS EXACTLY YOUR ORIGINAL CODE
# Data source: logs
# ============================================================

# === KUCOIN LAUNCHPOOL ===
with tab3:
    st.title("KuCoin Launchpool")

    meta = refresh_if_needed("kucoin_launch", force=False)
    if st.button("🔄 Refresh", key="refresh_launch_kucoin"):
        meta = refresh_if_needed("kucoin_launch", force=True)

    show_last_refresh(meta)

    launch_df = load_from_logs("kucoin_launch")
    if launch_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        st.markdown("### Select asset to see futures:")

        # Отображаем первые 7 колонок + кнопку
        colnames = list(launch_df.columns)
        colnames_to_show = colnames[:7]  # показываем первые 7

        col_header = st.columns([1] * 7 + [1])
        for i, col in enumerate(colnames_to_show):
            col_header[i].markdown(f"**{col}**")
        col_header[7].markdown("**Action**")

        for i, row in launch_df.iterrows():
            cols = st.columns([1] * 7 + [1])
            for j, column in enumerate(colnames_to_show):
                value = row[column]
                cols[j].markdown(f"{value}")

            # если нет 'staking_coin', замените на нужное название
            if cols[7].button("→ Futures", key=f"kucoin_launch_fut_{i}"):
                st.session_state["search_query"] = row["staking_coin"]
                st.session_state["focus_symbol"] = row["staking_coin"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()


# === BYBIT FIXED ===
with tab4:
    st.title("Bybit Fixed Earn")

    meta = refresh_if_needed("bybit_fixed", force=False)
    if st.button("🔄 Refresh", key="refresh_bybit_fixed"):
        meta = refresh_if_needed("bybit_fixed", force=True)

    show_last_refresh(meta)

    bybit_fixed_df = load_from_logs("bybit_fixed")
    if bybit_fixed_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        # Standardize status column
        bybit_fixed_df["status"] = bybit_fixed_df["status"].astype(str).str.strip().str.lower()
        statuses = sorted(bybit_fixed_df["status"].dropna().unique().tolist())
        default_status = ["ongoing"] if "ongoing" in statuses else statuses

        # Filter by status
        selected_status = st.multiselect(
            "Filter by status",
            options=statuses,
            default=default_status,
            key="bybit_fixed_status_multiselect"
        )
        filtered = bybit_fixed_df[bybit_fixed_df["status"].isin(selected_status)].sort_values("APR", ascending=False)

        # Column setup
        st.markdown("### Select asset to see futures:")
        colnames = list(filtered.columns)
        colnames_to_show = colnames[1:5]  # show columns 2 to 5 (index 1 to 4)

        col_header = st.columns([1, 1, 1, 1, 1])
        for i, col in enumerate(colnames_to_show):
            col_header[i].markdown(f"**{col}**")
        col_header[4].markdown("**Action**")

        for i, row in filtered.iterrows():
            cols = st.columns([1, 1, 1, 1, 1])
            for j in range(4):
                cols[j].markdown(f"{row[colnames_to_show[j]]}")

            if cols[4].button("→ Futures", key=f"bybit_fixed_fut_{i}"):
                st.session_state["search_query"] = row["asset"]  # Replace "asset" if named differently
                st.session_state["focus_symbol"] = row["asset"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()


# === BYBIT LAUNCHPOOL ===
with tab5:
    st.title("Bybit Launchpool")

    meta = refresh_if_needed("bybit_launch", force=False)
    if st.button("🔄 Refresh", key="refresh_bybit_launch"):
        meta = refresh_if_needed("bybit_launch", force=True)

    show_last_refresh(meta)

    bybit_launch_df = load_from_logs("bybit_launch")
    if bybit_launch_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        # Clean status values
        bybit_launch_df["status"] = bybit_launch_df["status"].astype(str).str.strip().str.lower()
        statuses = sorted(bybit_launch_df["status"].dropna().unique().tolist())
        default_status = ["ongoing"] if "ongoing" in statuses else statuses

        selected_status = st.multiselect(
            "Filter by status",
            options=statuses,
            default=default_status,
            key="bybit_launch_status_multiselect"
        )
        filtered = bybit_launch_df[bybit_launch_df["status"].isin(selected_status)]

        # Columns to display: 2,3,4,8,9
        colnames = list(filtered.columns)
        selected_cols = [colnames[1], colnames[2], colnames[3], colnames[7], colnames[8]]

        st.markdown("### Select asset to see futures:")
        col_header = st.columns([1, 1, 1, 1, 1, 1])
        for i, col in enumerate(selected_cols):
            col_header[i].markdown(f"**{col}**")
        col_header[5].markdown("**Action**")

        for i, row in filtered.iterrows():
            cols = st.columns([1, 1, 1, 1, 1, 1])
            for j in range(5):
                cols[j].markdown(str(row[selected_cols[j]]))
            if cols[5].button("→ Futures", key=f"bybit_launch_fut_{i}"):
                st.session_state["search_query"] = row["staking_coin"]
                st.session_state["focus_symbol"] = row["staking_coin"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()


# === GATE FIXED ===
with tab6:
    st.title("Gate Fixed Earn")

    meta = refresh_if_needed("gate_fixed", force=False)
    if st.button("🔄 Refresh", key="refresh_gate_fixed"):
        meta = refresh_if_needed("gate_fixed", force=True)

    show_last_refresh(meta)

    gate_fixed_df = load_from_logs("gate_fixed")
    if gate_fixed_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        gate_fixed_df = gate_fixed_df.sort_values("APR", ascending=False)

        # Columns to show: 2,3,4,5,7
        colnames = list(gate_fixed_df.columns)
        selected_cols = [colnames[1], colnames[2], colnames[3], colnames[4], colnames[6]]

        st.markdown("### Select asset to see futures:")
        col_header = st.columns([1, 1, 1, 1, 1, 1])
        for i, col in enumerate(selected_cols):
            col_header[i].markdown(f"**{col}**")
        col_header[5].markdown("**Action**")

        for i, row in gate_fixed_df.iterrows():
            cols = st.columns([1, 1, 1, 1, 1, 1])
            for j in range(5):
                cols[j].markdown(str(row[selected_cols[j]]))
            if cols[5].button("→ Futures", key=f"gate_fixed_fut_{i}"):
                st.session_state["search_query"] = row["asset"]
                st.session_state["focus_symbol"] = row["asset"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()


# === GATE LAUNCHPOOL ===
with tab7:
    st.title("Gate Launchpool")

    meta = refresh_if_needed("gate_launch", force=False)
    if st.button("🔄 Refresh", key="refresh_gate_launch"):
        meta = refresh_if_needed("gate_launch", force=True)

    show_last_refresh(meta)

    gate_launch_df = load_from_logs("gate_launch")
    if gate_launch_df.empty:
        st.info("No data in logs yet. Click Refresh.")
    else:
        gate_launch_df["status"] = gate_launch_df["status"].astype(str).str.strip().str.lower()
        statuses = sorted(gate_launch_df["status"].dropna().unique().tolist())
        default_status = ["ongoing"] if "ongoing" in statuses else statuses

        selected_status = st.multiselect(
            "Filter by status",
            options=statuses,
            default=default_status,
            key="gate_launch_status_multiselect"
        )
        filtered = gate_launch_df[gate_launch_df["status"].isin(selected_status)]

        # Columns to show: 2,3,4,7,8 (indexes)
        colnames = list(filtered.columns)
        selected_cols = [colnames[1], colnames[2], colnames[3], colnames[6], colnames[7]]

        st.markdown("### Select asset to see futures:")
        col_header = st.columns([1, 1, 1, 1, 1, 1])
        for i, col in enumerate(selected_cols):
            col_header[i].markdown(f"**{col}**")
        col_header[5].markdown("**Action**")

        for i, row in filtered.iterrows():
            cols = st.columns([1, 1, 1, 1, 1, 1])
            for j in range(5):
                cols[j].markdown(str(row[selected_cols[j]]))
            if cols[5].button("→ Futures", key=f"gate_launch_fut_{i}"):
                st.session_state["search_query"] = row["staking_coin"]
                st.session_state["focus_symbol"] = row["staking_coin"]
                st.session_state["show_clicked"] = False
                st.session_state["force_tab1_reload"] = True
                st.session_state["selected_tab"] = 0
                st.rerun()