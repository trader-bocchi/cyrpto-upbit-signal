"""일 단위 CSV 저장 모듈 (새로운 저장 구조)"""
import json
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from src.storage.csv_store import atomic_write_csv, read_csv_safe


def get_daily_csv_path(data_root: Path, market: str, date_kst: datetime) -> Path:
    """
    일 단위 CSV 파일 경로 생성
    
    Args:
        data_root: 기본 경로 (예: data/raw/candles_1h)
        market: 마켓 코드 (예: KRW-BTC)
        date_kst: 날짜 (KST datetime)
    
    Returns:
        경로 (예: data/raw/candles_1h/upbit_KRW-BTC_20250718.csv)
    """
    date_str = date_kst.strftime("%Y%m%d")
    filename = f"upbit_{market}_{date_str}.csv"
    return data_root / filename


def get_daily_meta_path(data_root: Path, market: str, date_kst: datetime) -> Path:
    """일 단위 meta.json 경로"""
    date_str = date_kst.strftime("%Y%m%d")
    filename = f"upbit_{market}_{date_str}.meta.json"
    return data_root / filename


def load_daily_csv(data_root: Path, market: str, date_kst: datetime) -> pd.DataFrame:
    """일 단위 CSV 로드"""
    csv_path = get_daily_csv_path(data_root, market, date_kst)
    return read_csv_safe(csv_path)


def load_daily_meta(data_root: Path, market: str, date_kst: datetime) -> Dict:
    """일 단위 meta.json 로드"""
    meta_path = get_daily_meta_path(data_root, market, date_kst)
    if not meta_path.exists():
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_daily_csv(
    df: pd.DataFrame,
    data_root: Path,
    market: str,
    date_kst: datetime,
    meta_info: Optional[Dict] = None,
):
    """
    일 단위 CSV 저장 (해당 날짜의 24개 바만)
    
    Args:
        df: 저장할 DataFrame (candle_time_kst 컬럼 필요)
        data_root: 기본 경로
        market: 마켓 코드
        date_kst: 날짜 (KST datetime)
        meta_info: 메타데이터 (없으면 자동 생성)
    """
    # 날짜 필터링 (00:00 ~ 23:00)
    date_start = date_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    date_end = date_kst.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    if "candle_time_kst" in df.columns:
        df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
        mask = (df["candle_time_kst"] >= date_start) & (df["candle_time_kst"] <= date_end)
        df_filtered = df[mask].copy()
    else:
        df_filtered = df.copy()
    
    # 정렬
    if not df_filtered.empty and "candle_time_kst" in df_filtered.columns:
        df_filtered = df_filtered.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # CSV 저장
    csv_path = get_daily_csv_path(data_root, market, date_kst)
    atomic_write_csv(df_filtered, csv_path)
    
    # Meta 정보 생성/저장
    if meta_info is None:
        meta_info = {}
    
    # missing_hours 계산
    missing_hours = []
    if not df_filtered.empty and "candle_time_kst" in df_filtered.columns:
        expected_hours = pd.date_range(date_start, date_end, freq="1H")
        actual_hours = set(df_filtered["candle_time_kst"].dt.to_pydatetime())
        expected_hours_set = set(expected_hours.to_pydatetime())
        missing_datetimes = expected_hours_set - actual_hours
        missing_hours = sorted([dt.strftime("%H:%M") for dt in missing_datetimes])
    
    meta = {
        "market": market,
        "date_kst": date_kst.strftime("%Y-%m-%d"),
        "fetched_from_kst": str(df_filtered["candle_time_kst"].min()) if not df_filtered.empty else None,
        "fetched_to_kst": str(df_filtered["candle_time_kst"].max()) if not df_filtered.empty else None,
        "rows_saved": len(df_filtered),
        "missing_hours": missing_hours,
        "updated_at_kst": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta.update(meta_info)
    
    # Meta 저장
    meta_path = get_daily_meta_path(data_root, market, date_kst)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    temp_meta = meta_path.with_suffix(".tmp.json")
    try:
        with open(temp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        temp_meta.replace(meta_path)
    except Exception as e:
        if temp_meta.exists():
            temp_meta.unlink()
        raise e
    
    return meta


def dedup_daily_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """일 단위 DataFrame 중복 제거"""
    if df.empty:
        return df
    
    # ingest_time_kst가 있으면 이를 기준으로 정렬
    if "ingest_time_kst" in df.columns:
        df = df.sort_values("ingest_time_kst", ascending=True)
    
    # 중복 제거 (가장 마지막 ingest_time_kst 유지)
    df = df.drop_duplicates(subset=["market", "candle_time_kst"], keep="last")
    
    # 정렬
    if "candle_time_kst" in df.columns:
        df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return df


def merge_daily_data(
    existing_df: pd.DataFrame,
    new_df: pd.DataFrame,
    data_root: Path,
    market: str,
    date_kst: datetime,
) -> Tuple[pd.DataFrame, Dict]:
    """
    일 단위 데이터 병합 및 저장
    
    Returns:
        (병합된 DataFrame, meta 정보)
    """
    # 병합
    if not existing_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined_df = new_df.copy()
    
    # 중복 제거
    combined_df = dedup_daily_dataframe(combined_df)
    
    # 저장
    meta = save_daily_csv(combined_df, data_root, market, date_kst)
    
    return combined_df, meta

