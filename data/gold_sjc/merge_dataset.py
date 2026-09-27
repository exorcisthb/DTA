"""
merge_dataset.py
----------------
Gộp dữ liệu lịch sử từ hai nguồn (giavang.org và giavangonline.com)
thành một dataset duy nhất với schema đầy đủ.

Đầu vào :
    sjc_history_giavang.csv
    sjc_history_giavangonline.csv

Đầu ra :
    sjc_merged.csv      — Dữ liệu gộp đầy đủ (giữ cả hai nguồn để so sánh)
    sjc_final.csv       — Dataset sạch, dùng để phân tích
    sjc_differences.csv — Các ngày hai nguồn có chênh lệch đáng kể

Schema sjc_final.csv (cũng là schema sjc_merged.csv, ngoại trừ cột so sánh):
    date              : Ngày giao dịch (YYYY-MM-DD)
    time_close        : Giờ chốt giá (trống nếu không có)
    gold_type         : Loại vàng SJC
    buy_vnd_luong     : Giá mua vào theo lượng (VND)
    sell_vnd_luong    : Giá bán ra theo lượng (VND)
    buy_vnd_chi       : Giá mua vào theo chỉ (VND)
    sell_vnd_chi      : Giá bán ra theo chỉ (VND)
    avg_vnd_luong     : Giá trung bình theo lượng (VND)
    avg_vnd_chi       : Giá trung bình theo chỉ (VND)
    spread_vnd        : Chênh lệch bán-mua theo lượng (VND)
    source            : Nguồn dữ liệu cuối được chọn
"""

import os
import pandas as pd

# ─── Cấu hình ─────────────────────────────────────────────────────────────────

F_GIAVANG       = "sjc_history_giavang.csv"
F_GIAVANGONLINE = "sjc_history_giavangonline.csv"
F_MERGED        = "sjc_merged.csv"
F_FINAL         = "sjc_final.csv"
F_DIFF          = "sjc_differences.csv"

# Ngưỡng chênh lệch (VND) để đánh dấu "conflict"
TOL_VND = 100_000  # 100,000 VND = 0.1 triệu

SCHEMA_FINAL = [
    "date", "time_close", "gold_type",
    "buy_vnd_luong", "sell_vnd_luong",
    "buy_vnd_chi",   "sell_vnd_chi",
    "avg_vnd_luong", "avg_vnd_chi",
    "spread_vnd",    "source",
]

NUMERIC_COLS = [
    "buy_vnd_luong", "sell_vnd_luong",
    "buy_vnd_chi",   "sell_vnd_chi",
    "avg_vnd_luong", "avg_vnd_chi",
    "spread_vnd",
]

# ─── Tiện ích ──────────────────────────────────────────────────────────────────

def _recalc_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Tính lại tất cả cột phái sinh từ buy/sell theo lượng (VND)."""
    df["buy_vnd_chi"]    = (df["buy_vnd_luong"]  / 10).round()
    df["sell_vnd_chi"]   = (df["sell_vnd_luong"] / 10).round()
    df["avg_vnd_luong"]  = ((df["buy_vnd_luong"] + df["sell_vnd_luong"]) / 2).round()
    df["avg_vnd_chi"]    = (df["avg_vnd_luong"]  / 10).round()
    df["spread_vnd"]     = (df["sell_vnd_luong"] - df["buy_vnd_luong"]).round()
    return df


def load_source(filepath: str) -> pd.DataFrame:
    if not os.path.isfile(filepath):
        print(f"CANH BAO: Khong tim thay file {filepath}")
        return pd.DataFrame()

    df = pd.read_csv(filepath, dtype=str)

    # Chuẩn hoá tên cột date
    if "timestamp" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"timestamp": "date"})

    df["date"] = df["date"].astype(str).str[:10]

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Nếu file cũ chỉ có buy_1l / sell_1l (schema cũ) → migrate
    if "buy_1l" in df.columns and "buy_vnd_luong" not in df.columns:
        print(f"  Schema cu phat hien trong {filepath}, dang chuyen doi...")
        df["buy_vnd_luong"]  = pd.to_numeric(df["buy_1l"],  errors="coerce") * 1_000_000
        df["sell_vnd_luong"] = pd.to_numeric(df["sell_1l"], errors="coerce") * 1_000_000
        if "gold_type" not in df.columns:
            df["gold_type"] = "SJC 1L"
        if "time_close" not in df.columns:
            df["time_close"] = ""

    return df


# ─── Logic gộp ────────────────────────────────────────────────────────────────

def merge_sources(d1: pd.DataFrame, d2: pd.DataFrame) -> pd.DataFrame:
    """
    Gộp hai nguồn theo (date, gold_type).
    Ưu tiên: giavangonline > giavang.org.
    Đánh dấu conflict nếu chênh lệch > TOL_VND.
    """
    if d1.empty and d2.empty:
        return pd.DataFrame()

    # Đổi tên cột để phân biệt hai nguồn
    key_cols   = ["date", "gold_type"]
    price_cols = ["buy_vnd_luong", "sell_vnd_luong"]

    def prep(df, suffix):
        df = df.copy()
        for col in price_cols:
            if col in df.columns:
                df = df.rename(columns={col: f"{col}_{suffix}"})
        return df[key_cols + [f"{col}_{suffix}" for col in price_cols if f"{col}_{suffix}" in df.columns]]

    left  = prep(d1, "giavang")      if not d1.empty else pd.DataFrame()
    right = prep(d2, "giavangonline") if not d2.empty else pd.DataFrame()

    if left.empty:
        merged = right.copy()
    elif right.empty:
        merged = left.copy()
    else:
        merged = pd.merge(left, right, on=key_cols, how="outer", sort=True)

    # Chọn giá cuối (ưu tiên giavangonline)
    def coalesce(col_online, col_giavang):
        if col_online in merged.columns and col_giavang in merged.columns:
            return merged[col_online].combine_first(merged[col_giavang])
        elif col_online in merged.columns:
            return merged[col_online]
        elif col_giavang in merged.columns:
            return merged[col_giavang]
        return pd.Series(dtype=float)

    merged["buy_vnd_luong"]  = coalesce("buy_vnd_luong_giavangonline",  "buy_vnd_luong_giavang")
    merged["sell_vnd_luong"] = coalesce("sell_vnd_luong_giavangonline", "sell_vnd_luong_giavang")

    # Xác định nguồn được chọn
    def pick_source(row):
        has_online = pd.notna(row.get("buy_vnd_luong_giavangonline"))
        has_giavang = pd.notna(row.get("buy_vnd_luong_giavang"))
        if has_online and has_giavang:
            buy_diff = abs(row.get("buy_vnd_luong_giavangonline", 0) - row.get("buy_vnd_luong_giavang", 0))
            return "conflict" if buy_diff > TOL_VND else "giavangonline"
        elif has_online:
            return "giavangonline"
        elif has_giavang:
            return "giavang_org"
        return "unknown"

    merged["source"] = merged.apply(pick_source, axis=1)

    # Đánh dấu chênh lệch
    if "buy_vnd_luong_giavang" in merged.columns and "buy_vnd_luong_giavangonline" in merged.columns:
        buy_diff  = merged["buy_vnd_luong_giavang"].sub(merged["buy_vnd_luong_giavangonline"]).abs()
        sell_diff = merged["sell_vnd_luong_giavang"].sub(merged["sell_vnd_luong_giavangonline"]).abs()
        merged["buy_diff"]  = buy_diff  > TOL_VND
        merged["sell_diff"] = sell_diff > TOL_VND
        merged["any_diff"]  = merged["buy_diff"] | merged["sell_diff"]
    else:
        merged["buy_diff"]  = False
        merged["sell_diff"] = False
        merged["any_diff"]  = False

    if "time_close" not in merged.columns:
        merged["time_close"] = ""
    if "gold_type" not in merged.columns:
        merged["gold_type"] = "SJC 1L"

    return merged


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Merge Dataset SJC — Schema day du")
    print("=" * 60)

    print(f"\nDoc {F_GIAVANG}...")
    d1 = load_source(F_GIAVANG)
    print(f"  {len(d1)} dong")

    print(f"Doc {F_GIAVANGONLINE}...")
    d2 = load_source(F_GIAVANGONLINE)
    print(f"  {len(d2)} dong")

    if d1.empty and d2.empty:
        print("Khong co du lieu. Ket thuc.")
        return

    print("\nGop hai nguon...")
    merged = merge_sources(d1, d2)

    # Tính lại tất cả cột phái sinh
    merged = _recalc_derived(merged)

    # ── Lưu sjc_merged.csv (đầy đủ, bao gồm cả cột so sánh) ──
    merged.to_csv(F_MERGED, index=False)
    print(f"\nDa luu {F_MERGED}: {len(merged)} dong")

    # ── Lưu sjc_differences.csv ──
    diff_df = merged[merged["any_diff"] == True] if "any_diff" in merged.columns else pd.DataFrame()
    diff_df.to_csv(F_DIFF, index=False)
    print(f"Da luu {F_DIFF}: {len(diff_df)} dong co chenh lech")

    # ── Lưu sjc_final.csv (schema sạch) ──
    for col in SCHEMA_FINAL:
        if col not in merged.columns:
            merged[col] = ""

    final = merged[SCHEMA_FINAL].copy()
    final = final.dropna(subset=["buy_vnd_luong", "sell_vnd_luong"])
    final = final.sort_values(["date", "gold_type"], ignore_index=True)

    # Đổi nguồn "conflict" → "giavangonline" (vẫn dùng giá giavangonline)
    final.loc[final["source"] == "conflict", "source"] = "giavangonline"

    final.to_csv(F_FINAL, index=False)
    print(f"Da luu {F_FINAL}: {len(final)} dong")

    # ── Thống kê ──
    print("\nThong ke sjc_final.csv:")
    print(f"  Tong so dong         : {len(final):,}")
    print(f"  Pham vi ngay         : {final['date'].min()} -> {final['date'].max()}")
    print(f"  So loai vang         : {final['gold_type'].nunique()}")
    print(f"  Phan bo loai vang    :")
    for gt, cnt in final["gold_type"].value_counts().items():
        print(f"    {gt:12s}: {cnt:,} ngay")
    print(f"  Phan bo nguon        :")
    for src, cnt in final["source"].value_counts().items():
        print(f"    {src:20s}: {cnt:,} dong")

    # ── Mẫu giá ──
    sjc1l = final[final["gold_type"] == "SJC 1L"].tail(5)
    if not sjc1l.empty:
        print("\nMau du lieu gan nhat (SJC 1L):")
        print(sjc1l[[
            "date", "buy_vnd_luong", "sell_vnd_luong",
            "buy_vnd_chi", "sell_vnd_chi",
            "avg_vnd_luong", "spread_vnd", "source"
        ]].to_string(index=False))


if __name__ == "__main__":
    main()
