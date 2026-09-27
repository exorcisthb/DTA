"""
backfill_sjc_giavang.py
-----------------------
Cào dữ liệu lịch sử giá vàng SJC từ giavang.org.
Đầu ra: sjc_history_giavang.csv với schema đầy đủ.

Schema:
    date              : Ngày giao dịch (YYYY-MM-DD)
    time_close        : Giờ chốt giá (để trống, giavang.org không cung cấp giờ)
    gold_type         : Loại vàng SJC (SJC 1L, SJC 2L, ...)
    buy_vnd_luong     : Giá mua vào theo lượng (VND)
    sell_vnd_luong    : Giá bán ra theo lượng (VND)
    buy_vnd_chi       : Giá mua vào theo chỉ (VND) = buy_vnd_luong / 10
    sell_vnd_chi      : Giá bán ra theo chỉ (VND)  = sell_vnd_luong / 10
    avg_vnd_luong     : Giá trung bình theo lượng (VND)
    avg_vnd_chi       : Giá trung bình theo chỉ (VND)
    spread_vnd        : Chênh lệch bán-mua theo lượng (VND)
    source            : giavang_org
"""

import os
import re
import time
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

CSV_FILE   = "sjc_history_giavang.csv"
START_DATE = datetime(2009, 7, 22)
END_DATE   = datetime.now()
BASE_URL   = "https://giavang.org/trong-nuoc/sjc/lich-su/{date}.html"
DELAY_SEC  = 0.5

SCHEMA_COLUMNS = [
    "date", "time_close", "gold_type",
    "buy_vnd_luong", "sell_vnd_luong",
    "buy_vnd_chi",   "sell_vnd_chi",
    "avg_vnd_luong", "avg_vnd_chi",
    "spread_vnd",    "source",
]

# Danh sách loại vàng cần cào (nhãn tìm kiếm → tên chuẩn)
GOLD_TYPES = [
    ("1L",  "SJC 1L"),
    ("2L",  "SJC 2L"),
    ("5L",  "SJC 5L"),
    ("10L", "SJC 10L"),
    ("1C",  "SJC 1chi"),
    ("2C",  "SJC 2chi"),
    ("5C",  "SJC 5chi"),
]

BOT_USER_AGENT = "Mozilla/5.0 (compatible; SJC-price-bot/1.0)"


def _to_vnd(value_str):
    """Chuyển chuỗi giá (có thể là triệu VND) sang VND nguyên."""
    try:
        val = float(re.sub(r"[^\d.]", "", str(value_str).replace(",", ".")))
    except (ValueError, TypeError):
        return None
    if val < 10_000:
        val *= 1_000_000
    return round(val)


def _build_record(date_str, gold_type, buy_vnd, sell_vnd):
    avg = (buy_vnd + sell_vnd) / 2
    return {
        "date":           date_str,
        "time_close":     "",
        "gold_type":      gold_type,
        "buy_vnd_luong":  round(buy_vnd),
        "sell_vnd_luong": round(sell_vnd),
        "buy_vnd_chi":    round(buy_vnd  / 10),
        "sell_vnd_chi":   round(sell_vnd / 10),
        "avg_vnd_luong":  round(avg),
        "avg_vnd_chi":    round(avg / 10),
        "spread_vnd":     round(sell_vnd - buy_vnd),
        "source":         "giavang_org",
    }


def fetch_for_date(date):
    date_str = date.strftime("%Y-%m-%d")
    url      = BASE_URL.format(date=date_str)
    headers  = {"User-Agent": BOT_USER_AGENT}

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    soup  = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    df = pd.read_html(StringIO(str(table)))[0]

    # Lọc khu vực Hồ Chí Minh
    col_kv = next((c for c in df.columns if "khu" in str(c).lower()), None)
    if col_kv:
        df = df[df[col_kv].astype(str).str.contains(
            r"H[oO]\.?\s*Ch[iI]\.?\s*Minh|HCM", case=False, na=False, regex=True
        )]

    # Tìm cột tên loại vàng, mua, bán
    col_loai = next((c for c in df.columns if "lo" in str(c).lower() and "v" in str(c).lower()), None)
    col_mua  = next((c for c in df.columns if "mua" in str(c).lower()), None)
    col_ban  = next((c for c in df.columns if "ban" in str(c).lower()), None)

    if col_mua is None or col_ban is None:
        return []

    records = []
    for gold_label, label_norm in GOLD_TYPES:
        sub = df.copy()
        if col_loai:
            sub = sub[sub[col_loai].astype(str).str.contains(gold_label, case=False, na=False)]
        if sub.empty:
            continue

        row      = sub.iloc[0]
        buy_vnd  = _to_vnd(row.get(col_mua, ""))
        sell_vnd = _to_vnd(row.get(col_ban,  ""))

        if buy_vnd is None or sell_vnd is None or buy_vnd <= 0 or sell_vnd <= 0:
            continue

        records.append(_build_record(date_str, label_norm, buy_vnd, sell_vnd))

    return records


def load_existing_keys():
    """Trả về set (date, gold_type) đã tồn tại trong CSV."""
    if not os.path.isfile(CSV_FILE):
        return set()
    df = pd.read_csv(CSV_FILE, dtype=str)
    return set(zip(df["date"].str[:10], df["gold_type"]))


def append_to_csv(new_records):
    if not new_records:
        return
    df_new = pd.DataFrame(new_records, columns=SCHEMA_COLUMNS)
    if os.path.isfile(CSV_FILE):
        df_new.to_csv(CSV_FILE, mode="a", header=False, index=False)
    else:
        df_new.to_csv(CSV_FILE, index=False)


if __name__ == "__main__":
    print("=" * 60)
    print("Backfill SJC — nguon: giavang.org")
    print(f"Pham vi: {START_DATE.date()} -> {END_DATE.date()}")
    print("=" * 60)

    existing_keys = load_existing_keys()
    cur_date      = START_DATE
    all_new       = []
    skip_count    = 0
    error_count   = 0

    while cur_date.date() <= END_DATE.date():
        date_str = cur_date.strftime("%Y-%m-%d")

        # Bỏ qua nếu SJC 1L của ngày đó đã có
        if (date_str, "SJC 1L") in existing_keys:
            skip_count += 1
            cur_date += timedelta(days=1)
            continue

        print(f"Crawling {date_str}...", end=" ")
        try:
            records = fetch_for_date(cur_date)
            if records:
                all_new.extend(records)
                # Flush mỗi 100 ngày để tránh mất dữ liệu
                if len(all_new) >= 100:
                    append_to_csv(all_new)
                    print(f"\n  -> Flush {len(all_new)} dong vao CSV.")
                    all_new = []
                    # Reload keys để tránh trùng lặp
                    existing_keys = load_existing_keys()
                for r in records:
                    existing_keys.add((r["date"], r["gold_type"]))
                print(f"OK ({len(records)} loai vang)")
            else:
                print("Khong co du lieu")
        except Exception as e:
            print(f"LOI: {e}")
            error_count += 1

        cur_date += timedelta(days=1)
        time.sleep(DELAY_SEC)

    # Flush phần còn lại
    if all_new:
        append_to_csv(all_new)

    # Sắp xếp lại toàn bộ file
    if os.path.isfile(CSV_FILE):
        df = pd.read_csv(CSV_FILE, dtype=str)
        df = df.sort_values(["date", "gold_type"], ignore_index=True)
        df.to_csv(CSV_FILE, index=False)
        print(f"\nHoan thanh! Tong {len(df)} dong trong {CSV_FILE}")

    print(f"Bo qua (da co): {skip_count} ngay")
    print(f"Loi: {error_count} ngay")
