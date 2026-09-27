"""
fetch_daily.py
--------------
Cào giá vàng SJC hôm nay từ webgia.com và ghi vào sjc_final.csv.

Schema sjc_final.csv:
    timestamp   : Ngày giao dịch (YYYY-MM-DD)
    buy_1l      : Giá mua vào (triệu VND/lượng)
    sell_1l     : Giá bán ra (triệu VND/lượng)

Ghi chú: webgia.com hiển thị giá theo VND/chỉ (1 lượng = 10 chỉ).
Ví dụ: 14.140.000 VND/chỉ → 141.4 triệu VND/lượng.
"""

import os
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

DIR      = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(DIR, "sjc_final.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

WEBGIA_URL = "https://webgia.com/gia-vang/sjc/"

# Regex nhận dạng dòng SJC 1L / 10L / 1KG (vàng miếng lớn)
SJC_1L_PATTERN = re.compile(r"SJC.*1L|1L.*10L|1L.*1KG|SJC.*1\s*lượng", re.IGNORECASE)


def _clean_number(text: str) -> float | None:
    """Bỏ tất cả ký tự không phải số rồi trả về float, hoặc None nếu rỗng."""
    cleaned = re.sub(r"[^\d]", "", str(text))
    return float(cleaned) if cleaned else None


def _to_trieu_luong(raw_vnd: float) -> float:
    """
    Chuyển đổi giá từ webgia.com sang triệu VND/lượng.

    webgia.com hiển thị đơn vị VND/chỉ (ví dụ 14_140_000):
      - > 1_000_000  → VND/chỉ  → chia 1_000_000 rồi × 10
      - 100–2000     → triệu/chỉ → × 10
      - > 10_000     → VND/lượng → chia 1_000_000
      - 50–300       → triệu/lượng → dùng nguyên
    """
    if raw_vnd > 1_000_000:
        chi_trieu = raw_vnd / 1_000_000   # VND → triệu/chỉ
        if 5 < chi_trieu < 50:            # giá hợp lệ / chỉ (vàng ~14 triệu/chỉ)
            return round(chi_trieu * 10, 4)
        luong_trieu = raw_vnd / 1_000_000  # thử coi là triệu/lượng
        if 50 < luong_trieu < 500:
            return round(luong_trieu, 4)
    if 5 < raw_vnd < 50:                   # đã là triệu/chỉ
        return round(raw_vnd * 10, 4)
    if 50 < raw_vnd < 500:                 # đã là triệu/lượng
        return round(raw_vnd, 4)
    return None


def fetch_from_webgia(max_retries: int = 5, delay: int = 15) -> dict | None:
    """Trả về dict {date, buy, sell} (triệu VND/lượng) hoặc None nếu thất bại."""
    for attempt in range(1, max_retries + 1):
        print(f"[webgia.com] Lần thử {attempt}/{max_retries}...")
        try:
            r = requests.get(WEBGIA_URL, headers=HEADERS, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # ── Lấy ngày cập nhật ──────────────────────────────────────────
            date_str = datetime.now().strftime("%Y-%m-%d")
            for el in soup.find_all(string=re.compile(r"\d{2}/\d{2}/\d{4}")):
                m = re.search(r"(\d{2})/(\d{2})/(\d{4})", el)
                if m:
                    date_str = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                    break

            # ── Parse bảng giá ─────────────────────────────────────────────
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                # Tìm chỉ số cột Mua/Bán từ header
                buy_col = sell_col = None
                if rows:
                    headers = [
                        th.get_text(strip=True).lower()
                        for th in rows[0].find_all(["th", "td"])
                    ]
                    for i, h in enumerate(headers):
                        if "mua" in h and buy_col is None:
                            buy_col = i
                        if "bán" in h or "ban" in h:
                            sell_col = i

                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if not cells:
                        continue
                    row_text = " ".join(c.get_text(" ", strip=True) for c in cells)

                    if not SJC_1L_PATTERN.search(row_text):
                        continue

                    # Lấy tất cả giá trị số trong row
                    nums = [
                        _clean_number(c.get_text(strip=True))
                        for c in cells
                    ]
                    nums = [n for n in nums if n and n > 0]

                    # Ưu tiên dùng cột header nếu xác định được
                    if buy_col is not None and sell_col is not None:
                        try:
                            raw_buy  = _clean_number(cells[buy_col].get_text(strip=True))
                            raw_sell = _clean_number(cells[sell_col].get_text(strip=True))
                            if raw_buy and raw_sell:
                                nums = [raw_buy, raw_sell]
                        except IndexError:
                            pass

                    if len(nums) < 2:
                        continue

                    buy  = _to_trieu_luong(nums[-2])
                    sell = _to_trieu_luong(nums[-1])

                    if buy and sell and 50 < buy < 500 and 50 < sell < 500:
                        print(
                            f"[webgia.com] OK → date={date_str} | "
                            f"mua={buy:.3f} triệu | bán={sell:.3f} triệu VND/lượng"
                        )
                        return {"date": date_str, "buy": buy, "sell": sell}

            print(f"[webgia.com] Không tìm thấy giá SJC 1L trong response.")

        except Exception as e:
            print(f"[webgia.com] Lỗi: {e}")

        if attempt < max_retries:
            print(f"[webgia.com] Chờ {delay}s trước khi thử lại...")
            time.sleep(delay)

    return None


def load_csv() -> pd.DataFrame:
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE, dtype=str)
    return pd.DataFrame(columns=["timestamp", "buy_1l", "sell_1l"])


def save_record(date_str: str, buy: float, sell: float) -> str:
    """Thêm hoặc cập nhật bản ghi. Trả về 'added'/'updated'/'no_change'."""
    df = load_csv()
    mask = df["timestamp"].astype(str) == date_str

    if mask.any():
        old_buy  = float(df.loc[mask, "buy_1l"].values[0])
        old_sell = float(df.loc[mask, "sell_1l"].values[0])
        if abs(old_buy - buy) < 0.001 and abs(old_sell - sell) < 0.001:
            print(f"[CSV] Không có thay đổi cho {date_str}.")
            return "no_change"
        df.loc[mask, "buy_1l"]  = round(buy,  3)
        df.loc[mask, "sell_1l"] = round(sell, 3)
        status = "updated"
    else:
        new_row = pd.DataFrame([{
            "timestamp": date_str,
            "buy_1l":    round(buy,  3),
            "sell_1l":   round(sell, 3),
        }])
        df = pd.concat([df, new_row], ignore_index=True)
        status = "added"

    df = df.sort_values("timestamp", ignore_index=True)
    df.to_csv(CSV_FILE, index=False)
    print(f"[CSV] {status.upper()}: {date_str} | mua={buy:.3f} | bán={sell:.3f} triệu VND/lượng")
    return status


def main() -> bool:
    print("=" * 60)
    print("SJC Daily Fetcher  —  webgia.com")
    print(f"Thời gian chạy: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ICT")
    print("=" * 60)

    data = fetch_from_webgia(max_retries=5, delay=15)
    if not data:
        print("[THẤT BẠI] Không lấy được dữ liệu từ bất kỳ nguồn nào.")
        return False

    status = save_record(data["date"], data["buy"], data["sell"])
    if status == "no_change":
        print("[KẾT THÚC] Dữ liệu không đổi, không cần commit.")
    else:
        print(f"[THÀNH CÔNG] Đã ghi bản ghi mới ({status}).")
    return True


if __name__ == "__main__":
    try:
        ok = main()
        sys.exit(0 if ok else 1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
