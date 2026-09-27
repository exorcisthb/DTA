"""
backfill_sjc_giavangonline.py
-----------------------------
Cào dữ liệu lịch sử giá vàng SJC từ giavangonline.com.
Đầu ra: sjc_history_giavangonline.csv với schema đầy đủ.

Lưu ý: giavangonline.com trả giá theo CHỈ (nghìn VND/chỉ).
Script tự quy đổi → VND/lượng (×10 rồi ×1000).

Schema:
    date              : Ngày giao dịch (YYYY-MM-DD)
    time_close        : Giờ chốt giá (để trống)
    gold_type         : Loại vàng SJC (SJC 1L, SJC 2L, ...)
    buy_vnd_luong     : Giá mua vào theo lượng (VND)
    sell_vnd_luong    : Giá bán ra theo lượng (VND)
    buy_vnd_chi       : Giá mua vào theo chỉ (VND) = buy_vnd_luong / 10
    sell_vnd_chi      : Giá bán ra theo chỉ (VND)  = sell_vnd_luong / 10
    avg_vnd_luong     : Giá trung bình theo lượng (VND)
    avg_vnd_chi       : Giá trung bình theo chỉ (VND)
    spread_vnd        : Chênh lệch bán-mua theo lượng (VND)
    source            : giavangonline
"""

import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

CSV_FILE   = "sjc_history_giavangonline.csv"
START_DATE = datetime(2012, 3, 4)
END_DATE   = datetime.now()
BASE_URL   = "https://giavangonline.com/mobile/goldhistory.php?date={date}"
DELAY_SEC  = 0.5

SCHEMA_COLUMNS = [
    "date", "time_close", "gold_type",
    "buy_vnd_luong", "sell_vnd_luong",
    "buy_vnd_chi",   "sell_vnd_chi",
    "avg_vnd_luong", "avg_vnd_chi",
    "spread_vnd",    "source",
]

# Key nhận dạng loại vàng trong cell text → tên chuẩn
GOLD_TYPE_MAP = {
    "sjc 1l":  "SJC 1L",
    "sjc 2l":  "SJC 2L",
    "sjc 5l":  "SJC 5L",
    "sjc 10l": "SJC 10L",
    "sjc 1c":  "SJC 1chi",
    "sjc 2c":  "SJC 2chi",
    "sjc 5c":  "SJC 5chi",
}

BOT_USER_AGENT = "Mozilla/5.0 (compatible; SJC-price-bot/1.0)"


def _chi_to_luong_vnd(chi_val):
    """
    Quy đổi giá theo CHỈ (đơn vị của giavangonline) sang VND/lượng.
    giavangonline lưu dạng: nghìn VND/chỉ (vd: 11850 = 11,850,000 VND/chỉ... cần kiểm tra).
    Nếu giá chỉ < 10000: đơn vị là triệu VND/chỉ -> * 1_000_000
    Nếu giá chỉ < 10_000_000: đơn vị là nghìn VND/chỉ -> * 1_000
    Sau đó nhân 10 để ra VND/lượng.
    """
    if chi_val < 10_000:
        # Giá đang tính bằng triệu VND/chỉ
        vnd_per_chi = chi_val * 1_000_000
    elif chi_val < 10_000_000:
        # Giá đang tính bằng nghìn VND/chỉ
        vnd_per_chi = chi_val * 1_000
    else:
        # Giá đã là VND/chỉ
        vnd_per_chi = chi_val

    vnd_per_luong = vnd_per_chi * 10
    return round(vnd_per_luong)


def _build_record(date_str, gold_type, buy_vnd_luong, sell_vnd_luong):
    avg = (buy_vnd_luong + sell_vnd_luong) / 2
    return {
        "date":           date_str,
        "time_close":     "",
        "gold_type":      gold_type,
        "buy_vnd_luong":  round(buy_vnd_luong),
        "sell_vnd_luong": round(sell_vnd_luong),
        "buy_vnd_chi":    round(buy_vnd_luong  / 10),
        "sell_vnd_chi":   round(sell_vnd_luong / 10),
        "avg_vnd_luong":  round(avg),
        "avg_vnd_chi":    round(avg / 10),
        "spread_vnd":     round(sell_vnd_luong - buy_vnd_luong),
        "source":         "giavangonline",
    }


def fetch_for_date(date):
    date_str = date.strftime("%Y-%m-%d")
    url      = BASE_URL.format(date=date.strftime("%Y/%m/%d"))
    headers  = {"User-Agent": BOT_USER_AGENT}

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    soup  = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="home")
    if table is None:
        return []

    records = []
    for row in table.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 2:
            continue

        cell_text    = cols[0].get_text().strip().lower()
        matched_norm = None
        for key, norm in GOLD_TYPE_MAP.items():
            if key in cell_text:
                matched_norm = norm
                break
        if matched_norm is None:
            continue

        # Tìm cặp mua/bán tốt nhất trong các cell giá (có thể có nhiều cột)
        best_pair = None
        for col in cols[1:]:
            price_text = col.get_text().strip()
            if " / " not in price_text:
                continue
            parts = price_text.split(" / ", 1)
            try:
                buy_chi  = float(parts[0].replace(",", ""))
                sell_chi = float(parts[1].replace(",", ""))
                # Chọn cặp có giá bán cao nhất (tránh pick giá nhỏ)
                if best_pair is None or sell_chi > best_pair[1]:
                    best_pair = (buy_chi, sell_chi)
            except (ValueError, IndexError):
                continue

        if best_pair is None:
            continue

        buy_vnd_luong  = _chi_to_luong_vnd(best_pair[0])
        sell_vnd_luong = _chi_to_luong_vnd(best_pair[1])

        if buy_vnd_luong <= 0 or sell_vnd_luong <= 0:
            continue

        records.append(_build_record(date_str, matched_norm, buy_vnd_luong, sell_vnd_luong))

    return records


def load_existing_keys():
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
    print("Backfill SJC — nguon: giavangonline.com")
    print(f"Pham vi: {START_DATE.date()} -> {END_DATE.date()}")
    print("=" * 60)

    existing_keys = load_existing_keys()
    cur_date      = START_DATE
    all_new       = []
    skip_count    = 0
    error_count   = 0

    while cur_date.date() <= END_DATE.date():
        date_str = cur_date.strftime("%Y-%m-%d")

        if (date_str, "SJC 1L") in existing_keys:
            skip_count += 1
            cur_date += timedelta(days=1)
            continue

        print(f"Crawling {date_str}...", end=" ")
        try:
            records = fetch_for_date(cur_date)
            if records:
                all_new.extend(records)
                if len(all_new) >= 100:
                    append_to_csv(all_new)
                    print(f"\n  -> Flush {len(all_new)} dong vao CSV.")
                    all_new = []
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

    if all_new:
        append_to_csv(all_new)

    if os.path.isfile(CSV_FILE):
        df = pd.read_csv(CSV_FILE, dtype=str)
        df = df.sort_values(["date", "gold_type"], ignore_index=True)
        df.to_csv(CSV_FILE, index=False)
        print(f"\nHoan thanh! Tong {len(df)} dong trong {CSV_FILE}")

    print(f"Bo qua (da co): {skip_count} ngay")
    print(f"Loi: {error_count} ngay")
