"""
get_sjc_price.py
----------------
Cao gia vang SJC theo thoi gian thuc (chay hang ngay qua GitHub Actions).

Schema dau ra (sjc_daily.csv):
    date              : Ngay giao dich (YYYY-MM-DD)
    time_close        : Gio chot gia (HH:MM, ICT) - blank neu nguon khong cung cap
    gold_type         : Loai vang SJC (vd: SJC 1L)
    buy_vnd_luong     : Gia mua vao theo luong (VND)
    sell_vnd_luong    : Gia ban ra theo luong (VND)
    buy_vnd_chi       : Gia mua vao theo chi (VND) = buy_vnd_luong / 10
    sell_vnd_chi      : Gia ban ra theo chi (VND)  = sell_vnd_luong / 10
    avg_vnd_luong     : Gia trung binh theo luong (VND) = (buy + sell) / 2
    avg_vnd_chi       : Gia trung binh theo chi (VND)   = avg_vnd_luong / 10
    spread_vnd        : Chenh lech ban-mua theo luong (VND) = sell - buy
    source            : Nguon du lieu (vnstock | sjc_api | giavang_org | giavangonline)
"""

import os
import re
import time
import traceback
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

SJC_API = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"
CSV_FILE = "sjc_daily.csv"

NAME_KEYS = {"name", "title", "loai_vang", "loaivang", "gold_name", "ten", "tenloaivang"}
BUY_KEYS  = {"buy", "buy_price", "giamua", "gia_mua", "mua"}
SELL_KEYS = {"sell", "sell_price", "giaban", "gia_ban", "ban"}

BOT_USER_AGENT     = "Mozilla/5.0 (compatible; SJC-price-bot/1.0)"
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

GOLD_TYPE_PRIORITY = ["sjc 1l", "sjc 2l", "sjc 5l", "sjc 10l", "sjc 1c", "sjc 2c", "sjc 5c"]
GOLD_TYPE_NORMALIZE = {
    "sjc 1l":  "SJC 1L",
    "sjc 2l":  "SJC 2L",
    "sjc 5l":  "SJC 5L",
    "sjc 10l": "SJC 10L",
    "sjc 1c":  "SJC 1chi",
    "sjc 2c":  "SJC 2chi",
    "sjc 5c":  "SJC 5chi",
}

SCHEMA_COLUMNS = [
    "date", "time_close", "gold_type",
    "buy_vnd_luong", "sell_vnd_luong",
    "buy_vnd_chi",   "sell_vnd_chi",
    "avg_vnd_luong", "avg_vnd_chi",
    "spread_vnd",    "source",
]

# ─── Tien ich ────────────────────────────────────────────────────────────────

def _to_numeric_price(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d]", "", str(value))
    return float(cleaned) if cleaned else None


def _derive_columns(buy_vnd, sell_vnd):
    avg = (buy_vnd + sell_vnd) / 2
    return {
        "buy_vnd_luong":  round(buy_vnd),
        "sell_vnd_luong": round(sell_vnd),
        "buy_vnd_chi":    round(buy_vnd  / 10),
        "sell_vnd_chi":   round(sell_vnd / 10),
        "avg_vnd_luong":  round(avg),
        "avg_vnd_chi":    round(avg / 10),
        "spread_vnd":     round(sell_vnd - buy_vnd),
    }


def _build_record(date_str, time_close, gold_type, buy_vnd, sell_vnd, source):
    record = {
        "date":       date_str,
        "time_close": time_close if time_close else "",
        "gold_type":  gold_type,
        "source":     source,
    }
    record.update(_derive_columns(buy_vnd, sell_vnd))
    return record


def recent_dates(max_days_back):
    for offset in range(max_days_back):
        dt = datetime.now() - timedelta(days=offset)
        yield dt, dt.strftime("%Y-%m-%d")


# ─── Trich xuat gia tu JSON ──────────────────────────────────────────────────

def _extract_all_prices_from_json(payload):
    results = []

    def walk(node):
        if isinstance(node, dict):
            lower_keys = {str(k).lower(): k for k in node.keys()}
            name_key = next((lower_keys[k] for k in lower_keys if k in NAME_KEYS), None)
            buy_key  = next((lower_keys[k] for k in lower_keys if k in BUY_KEYS),  None)
            sell_key = next((lower_keys[k] for k in lower_keys if k in SELL_KEYS), None)
            if buy_key is not None and sell_key is not None:
                name       = str(node.get(name_key, "")) if name_key else ""
                buy_price  = _to_numeric_price(node.get(buy_key))
                sell_price = _to_numeric_price(node.get(sell_key))
                if buy_price is not None and sell_price is not None:
                    results.append((name.strip(), buy_price, sell_price))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)
    return results


def _pick_best(rows):
    for priority_key in GOLD_TYPE_PRIORITY:
        for name, buy, sell in rows:
            if priority_key in name.lower():
                norm = GOLD_TYPE_NORMALIZE.get(priority_key, name.strip())
                return norm, buy, sell
    for name, buy, sell in rows:
        if buy and sell:
            return name.strip() or "SJC 1L", buy, sell
    return None


# ─── Nguon 1: vnstock ────────────────────────────────────────────────────────

def fetch_from_vnstock(max_retries=10, delay=10):
    try:
        from vnstock.explorer.misc import sjc_gold_price
    except ImportError:
        print("[vnstock] Thu vien chua duoc cai dat.")
        return []

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[vnstock] Lan thu {attempt}/{max_retries}...")
            gold_data = sjc_gold_price()
            if gold_data is None or len(gold_data) == 0:
                print(f"[vnstock] Khong co du lieu lan thu {attempt}.")
            else:
                records = []
                now_str = datetime.now().strftime("%Y-%m-%d")
                for _, row in gold_data.iterrows():
                    gold_type_raw = str(row.get("gold_type") or row.get("name") or "SJC 1L")
                    norm_type = "SJC 1L"
                    for key, val in GOLD_TYPE_NORMALIZE.items():
                        if key in gold_type_raw.lower():
                            norm_type = val
                            break
                    buy_raw  = row.get("buy_price")  or row.get("buy")
                    sell_raw = row.get("sell_price") or row.get("sell")
                    buy_vnd  = _to_numeric_price(buy_raw)
                    sell_vnd = _to_numeric_price(sell_raw)
                    if buy_vnd is None or sell_vnd is None:
                        continue
                    if buy_vnd < 10_000:
                        buy_vnd  *= 1_000_000
                        sell_vnd *= 1_000_000
                    records.append(_build_record(now_str, "15:00", norm_type, buy_vnd, sell_vnd, "vnstock"))
                if records:
                    print(f"[vnstock] Thanh cong: {len(records)} loai vang.")
                    return records
        except Exception as e:
            print(f"[vnstock] Loi lan thu {attempt}: {e}")
        if attempt < max_retries:
            print(f"[vnstock] Cho {delay}s...")
            time.sleep(delay)
    return []


# ─── Nguon 2: SJC API ────────────────────────────────────────────────────────

def fetch_from_sjc_api(max_retries=3, delay=5):
    headers = {
        "User-Agent":       BROWSER_USER_AGENT,
        "Referer":          "https://sjc.com.vn/",
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    now_str = datetime.now().strftime("%Y-%m-%d")

    for attempt in range(1, max_retries + 1):
        print(f"[SJC API] Lan thu {attempt}/{max_retries}...")
        for method in ("get", "post"):
            try:
                fn       = requests.get if method == "get" else requests.post
                response = fn(SJC_API, headers=headers, timeout=20)
                if response.status_code >= 400:
                    continue

                try:
                    payload = response.json()
                    rows    = _extract_all_prices_from_json(payload)
                    if rows:
                        result = _pick_best(rows)
                        if result:
                            norm_type, buy_vnd, sell_vnd = result
                            if buy_vnd < 10_000:
                                buy_vnd  *= 1_000_000
                                sell_vnd *= 1_000_000
                            print(f"[SJC API] Mua={buy_vnd:,.0f} | Ban={sell_vnd:,.0f} VND")
                            return [_build_record(now_str, "15:00", norm_type, buy_vnd, sell_vnd, "sjc_api")]
                except Exception:
                    pass

                try:
                    tables = pd.read_html(StringIO(response.text))
                except Exception:
                    tables = []

                for table in tables:
                    if table.empty:
                        continue
                    row_text = table.astype(str).agg(" ".join, axis=1).str.lower()
                    matched  = table[row_text.str.contains("sjc", na=False) & row_text.str.contains("1l", na=False)]
                    if matched.empty:
                        continue
                    prices = [_to_numeric_price(v) for v in matched.iloc[0].values]
                    prices = [p for p in prices if p and p > 0]
                    if len(prices) >= 2:
                        buy_vnd, sell_vnd = prices[0], prices[1]
                        if buy_vnd < 10_000:
                            buy_vnd  *= 1_000_000
                            sell_vnd *= 1_000_000
                        print(f"[SJC API HTML] Mua={buy_vnd:,.0f} | Ban={sell_vnd:,.0f} VND")
                        return [_build_record(now_str, "15:00", "SJC 1L", buy_vnd, sell_vnd, "sjc_api")]

                pattern = re.compile(r"(?is)sjc[^\n]{0,80}?1l.*?(\d{2,3}(?:[.,]\d{3})+).*?(\d{2,3}(?:[.,]\d{3})+)")
                m = pattern.search(response.text)
                if m:
                    buy_vnd  = _to_numeric_price(m.group(1))
                    sell_vnd = _to_numeric_price(m.group(2))
                    if buy_vnd and sell_vnd:
                        if buy_vnd < 10_000:
                            buy_vnd  *= 1_000_000
                            sell_vnd *= 1_000_000
                        print(f"[SJC API regex] Mua={buy_vnd:,.0f} | Ban={sell_vnd:,.0f} VND")
                        return [_build_record(now_str, "15:00", "SJC 1L", buy_vnd, sell_vnd, "sjc_api")]

            except Exception as e:
                print(f"[SJC API {method.upper()}] Loi: {e}")

        if attempt < max_retries:
            print(f"[SJC API] Cho {delay}s...")
            time.sleep(delay)
    return []


# ─── Nguon 3: giavang.org ─────────────────────────────────────────────────────

def fetch_from_giavang_org(max_days_back=3):
    headers  = {"User-Agent": BOT_USER_AGENT}
    type_map = [
        ("1L",  "SJC 1L"),
        ("2L",  "SJC 2L"),
        ("5L",  "SJC 5L"),
        ("10L", "SJC 10L"),
        ("1C",  "SJC 1chi"),
        ("2C",  "SJC 2chi"),
        ("5C",  "SJC 5chi"),
    ]

    for _, date_str in recent_dates(max_days_back):
        url = f"https://giavang.org/trong-nuoc/sjc/lich-su/{date_str}.html"
        try:
            print(f"[giavang.org] Thu {date_str}: {url}")
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()

            soup  = BeautifulSoup(response.text, "html.parser")
            table = soup.find("table")
            if table is None:
                continue

            df = pd.read_html(StringIO(str(table)))[0]
            if "Khu vuc" in df.columns or any("khu" in str(c).lower() for c in df.columns):
                col_kv = next((c for c in df.columns if "khu" in str(c).lower()), None)
                if col_kv:
                    df = df[df[col_kv].astype(str).str.contains("H. Ch. Minh|Ho Chi Minh|HCM", case=False, na=False)]

            records = []
            for gold_label, label_norm in type_map:
                sub = df.copy()
                col_loai = next((c for c in sub.columns if "lo" in str(c).lower() and "v" in str(c).lower()), None)
                if col_loai:
                    sub = sub[sub[col_loai].astype(str).str.contains(gold_label, case=False, na=False)]
                if sub.empty:
                    continue

                row = sub.iloc[0]
                col_mua = next((c for c in sub.columns if "mua" in str(c).lower()), None)
                col_ban = next((c for c in sub.columns if "ban" in str(c).lower()), None)
                if col_mua is None or col_ban is None:
                    continue

                buy_vnd  = pd.to_numeric(str(row.get(col_mua, "")).replace(",", ""), errors="coerce")
                sell_vnd = pd.to_numeric(str(row.get(col_ban,  "")).replace(",", ""), errors="coerce")

                if pd.isna(buy_vnd) or pd.isna(sell_vnd):
                    continue

                buy_vnd  = float(buy_vnd)
                sell_vnd = float(sell_vnd)

                # giavang.org luu theo trieu VND -> chuyen sang VND
                if buy_vnd < 10_000:
                    buy_vnd  *= 1_000_000
                    sell_vnd *= 1_000_000

                print(f"[giavang.org] {label_norm}: Mua={buy_vnd:,.0f} | Ban={sell_vnd:,.0f} VND")
                records.append(_build_record(date_str, None, label_norm, buy_vnd, sell_vnd, "giavang_org"))

            if records:
                return records

        except Exception as e:
            print(f"[giavang.org] Loi {date_str}: {e}")
    return []


# ─── Nguon 4: giavangonline.com ───────────────────────────────────────────────

def fetch_from_giavangonline(max_days_back=3):
    headers = {"User-Agent": BOT_USER_AGENT}

    for target_date, date_str in recent_dates(max_days_back):
        url = f"https://giavangonline.com/mobile/goldhistory.php?date={target_date.strftime('%Y/%m/%d')}"
        try:
            print(f"[giavangonline] Thu {date_str}: {url}")
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()

            soup  = BeautifulSoup(response.text, "html.parser")
            table = soup.find("table", class_="home")
            if table is None:
                continue

            records = []
            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue

                cell_text    = cols[0].get_text().strip().lower()
                matched_norm = None
                matched_key  = None

                for key, norm in GOLD_TYPE_NORMALIZE.items():
                    if key in cell_text:
                        matched_key  = key
                        matched_norm = norm
                        break
                if matched_norm is None:
                    continue

                best_pair = None
                for col in cols[1:]:
                    price_text = col.get_text().strip()
                    if " / " not in price_text:
                        continue
                    buy_str, sell_str = price_text.split(" / ", 1)
                    buy_chi  = pd.to_numeric(buy_str.replace(",",  ""), errors="coerce")
                    sell_chi = pd.to_numeric(sell_str.replace(",", ""), errors="coerce")
                    if pd.notna(buy_chi) and pd.notna(sell_chi):
                        candidate = (float(buy_chi), float(sell_chi))
                        if best_pair is None or candidate[1] > best_pair[1]:
                            best_pair = candidate

                if best_pair is None:
                    continue

                buy_chi_val, sell_chi_val = best_pair
                # Trang tra ve gia theo CHI (nghin VND/chi) -> doi sang VND/luong
                # 1 luong = 10 chi
                buy_luong_vnd  = buy_chi_val  * 10
                sell_luong_vnd = sell_chi_val * 10

                if buy_luong_vnd < 10_000:
                    buy_luong_vnd  *= 1_000_000
                    sell_luong_vnd *= 1_000_000
                elif buy_luong_vnd < 10_000_000:
                    buy_luong_vnd  *= 1_000
                    sell_luong_vnd *= 1_000

                print(f"[giavangonline] {matched_norm}: Mua={buy_luong_vnd:,.0f} | Ban={sell_luong_vnd:,.0f} VND")
                records.append(_build_record(date_str, None, matched_norm, buy_luong_vnd, sell_luong_vnd, "giavangonline"))

            if records:
                return records

        except Exception as e:
            print(f"[giavangonline] Loi {date_str}: {e}")
    return []


# ─── Luu CSV ─────────────────────────────────────────────────────────────────

def load_csv():
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE, dtype=str)
    return pd.DataFrame(columns=SCHEMA_COLUMNS)


def save_records(records):
    if not records:
        return 0

    df_existing = load_csv()
    df_new      = pd.DataFrame(records, columns=SCHEMA_COLUMNS)

    numeric_cols = [
        "buy_vnd_luong", "sell_vnd_luong",
        "buy_vnd_chi",   "sell_vnd_chi",
        "avg_vnd_luong", "avg_vnd_chi",
        "spread_vnd",
    ]
    for col in numeric_cols:
        if col in df_existing.columns:
            df_existing[col] = pd.to_numeric(df_existing[col], errors="coerce")
        df_new[col] = pd.to_numeric(df_new[col], errors="coerce")

    upsert_count = 0
    for _, new_row in df_new.iterrows():
        mask = (
            (df_existing["date"].astype(str)      == str(new_row["date"])) &
            (df_existing["gold_type"].astype(str) == str(new_row["gold_type"]))
        )
        if df_existing.shape[0] > 0 and mask.any():
            df_existing.loc[mask, list(df_new.columns)] = new_row.values
            print(f"  Cap nhat: {new_row['date']} | {new_row['gold_type']}")
        else:
            df_existing = pd.concat([df_existing, pd.DataFrame([new_row])], ignore_index=True)
            print(f"  Them moi: {new_row['date']} | {new_row['gold_type']}")
        upsert_count += 1

    df_existing = df_existing.sort_values(["date", "gold_type"], ignore_index=True)
    for col in SCHEMA_COLUMNS:
        if col not in df_existing.columns:
            df_existing[col] = ""
    df_existing = df_existing[SCHEMA_COLUMNS]

    df_existing.to_csv(CSV_FILE, index=False)
    print(f"\nDa luu {CSV_FILE}: tong {len(df_existing)} dong.")
    return upsert_count


# ─── Entry point ─────────────────────────────────────────────────────────────

def fetch_and_save():
    print("=" * 60)
    print("SJC Gold Price Fetcher - Schema day du")
    print("=" * 60)
    print(f"Thoi gian chay: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ICT")
    print("Thu tu uu tien: vnstock -> SJC API -> giavang.org -> giavangonline.com")
    print()

    records = []

    print("─── Nguon 1: vnstock ───")
    records = fetch_from_vnstock(max_retries=10, delay=10)

    if not records:
        print("\n─── Nguon 2: SJC API ───")
        records = fetch_from_sjc_api(max_retries=3, delay=5)

    if not records:
        print("\n─── Nguon 3: giavang.org ───")
        records = fetch_from_giavang_org(max_days_back=3)

    if not records:
        print("\n─── Nguon 4: giavangonline.com ───")
        records = fetch_from_giavangonline(max_days_back=3)

    if not records:
        print("\n[THAT BAI] Khong lay duoc du lieu tu bat ky nguon nao.")
        return False

    print(f"\n[THANH CONG] Lay duoc {len(records)} dong tu nguon '{records[0]['source']}'.")
    print("\nDu lieu cao duoc:")
    for r in records:
        print(
            f"  {r['date']} | {r['gold_type']:10s} | "
            f"Mua: {int(r['buy_vnd_luong']):>13,} VND/luong | "
            f"Ban: {int(r['sell_vnd_luong']):>13,} VND/luong | "
            f"Chenh: {int(r['spread_vnd']):>11,} VND"
        )

    print()
    saved = save_records(records)
    print(f"Ghi/cap nhat {saved} dong thanh cong.")
    return True


if __name__ == "__main__":
    try:
        success = fetch_and_save()
        exit(0 if success else 1)
    except Exception:
        traceback.print_exc()
        exit(1)
