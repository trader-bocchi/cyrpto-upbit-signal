"""월 단위 CSV 저장 모듈"""
import json
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from src.storage.csv_store import atomic_write_csv, read_csv_safe


def get_monthly_csv_path(data_root: Path, market: str, date_kst: datetime) -> Path:
    """
    월 단위 CSV 파일 경로 생성
    
    Args:
        data_root: 기본 경로 (예: data/raw/candles_1h)
        market: 마켓 코드 (예: KRW-BTC)
        date_kst: 날짜 (KST datetime, 해당 월의 아무 날짜나)
    
    Returns:
        경로 (예: data/raw/candles_1h/upbit_KRW-BTC_202507.csv)
    """
    month_str = date_kst.strftime("%Y%m")
    filename = f"upbit_{market}_{month_str}.csv"
    return data_root / filename


def get_monthly_meta_path(meta_root: Path, market: str, date_kst: datetime) -> Path:
    """
    월 단위 meta.json 경로 (CSV와 분리된 경로)
    
    Args:
        meta_root: 메타 데이터 루트 경로 (예: data/meta/candles_1h)
        market: 마켓 코드
        date_kst: 날짜 (KST datetime)
    """
    month_str = date_kst.strftime("%Y%m")
    filename = f"upbit_{market}_{month_str}.meta.json"
    return meta_root / filename


def load_monthly_csv(data_root: Path, market: str, date_kst: datetime) -> pd.DataFrame:
    """월 단위 CSV 로드"""
    csv_path = get_monthly_csv_path(data_root, market, date_kst)
    return read_csv_safe(csv_path)


def load_monthly_meta(meta_root: Path, market: str, date_kst: datetime) -> Dict:
    """월 단위 meta.json 로드"""
    meta_path = get_monthly_meta_path(meta_root, market, date_kst)
    if not meta_path.exists():
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_monthly_csv(
    df: pd.DataFrame,
    data_root: Path,
    meta_root: Path,
    market: str,
    date_kst: datetime,
    meta_info: Optional[Dict] = None,
):
    """
    월 단위 CSV 저장 (해당 월의 모든 바)
    
    Args:
        df: 저장할 DataFrame (candle_time_kst 컬럼 필요)
        data_root: CSV 저장 경로
        meta_root: Meta 저장 경로 (CSV와 분리)
        market: 마켓 코드
        date_kst: 날짜 (KST datetime, 해당 월의 아무 날짜나)
        meta_info: 메타데이터 (없으면 자동 생성)
    """
    # 월 범위 필터링
    month_start = date_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 다음 달 1일 00:00:00 (exclusive)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    
    if "candle_time_kst" in df.columns:
        df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
        mask = (df["candle_time_kst"] >= month_start) & (df["candle_time_kst"] < month_end)
        df_filtered = df[mask].copy()
    else:
        df_filtered = df.copy()
    
    # 정렬
    if not df_filtered.empty and "candle_time_kst" in df_filtered.columns:
        df_filtered = df_filtered.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # CSV 저장
    csv_path = get_monthly_csv_path(data_root, market, date_kst)
    atomic_write_csv(df_filtered, csv_path)
    
    # Meta 정보 생성/저장
    if meta_info is None:
        meta_info = {}
    
    # missing_hours 계산 (월 전체)
    missing_hours = []
    if not df_filtered.empty and "candle_time_kst" in df_filtered.columns:
        # 월의 모든 시간대 생성
        expected_hours = pd.date_range(month_start, month_end - pd.Timedelta(hours=1), freq="1H")
        actual_hours = set(df_filtered["candle_time_kst"].dt.to_pydatetime())
        expected_hours_set = set(expected_hours.to_pydatetime())
        missing_datetimes = expected_hours_set - actual_hours
        
        # 날짜별로 그룹화하여 누락 시간 기록
        missing_by_date: Dict[str, List[str]] = {}
        for dt in sorted(missing_datetimes):
            date_str = dt.strftime("%Y-%m-%d")
            hour_str = dt.strftime("%H:%M")
            if date_str not in missing_by_date:
                missing_by_date[date_str] = []
            missing_by_date[date_str].append(hour_str)
        
        # 리스트 형태로 변환
        missing_hours = [
            {"date": date, "hours": hours}
            for date, hours in sorted(missing_by_date.items())
        ]
    
    meta = {
        "market": market,
        "month_kst": date_kst.strftime("%Y-%m"),
        "fetched_from_kst": str(df_filtered["candle_time_kst"].min()) if not df_filtered.empty else None,
        "fetched_to_kst": str(df_filtered["candle_time_kst"].max()) if not df_filtered.empty else None,
        "rows_saved": len(df_filtered),
        "missing_hours": missing_hours,
        "updated_at_kst": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta.update(meta_info)
    
    # Meta 저장 (CSV와 분리된 경로)
    meta_path = get_monthly_meta_path(meta_root, market, date_kst)
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


def dedup_monthly_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """월 단위 DataFrame 중복 제거"""
    if df.empty:
        return df
    
    # candle_time_kst를 datetime으로 통일 (타입 혼재 방지)
    if "candle_time_kst" in df.columns:
        df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    
    # ingest_time_kst가 있으면 이를 기준으로 정렬
    if "ingest_time_kst" in df.columns:
        df = df.sort_values("ingest_time_kst", ascending=True)
    
    # 중복 제거 (가장 마지막 ingest_time_kst 유지)
    df = df.drop_duplicates(subset=["market", "candle_time_kst"], keep="last")
    
    # 정렬
    if "candle_time_kst" in df.columns:
        df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return df


def merge_monthly_data(
    existing_df: pd.DataFrame,
    new_df: pd.DataFrame,
    data_root: Path,
    meta_root: Path,
    market: str,
    date_kst: datetime,
) -> Tuple[pd.DataFrame, Dict]:
    """
    월 단위 데이터 병합 및 저장
    
    Returns:
        (병합된 DataFrame, meta 정보)
    """
    # 병합 전에 타입 통일 (candle_time_kst를 datetime으로)
    if not existing_df.empty and "candle_time_kst" in existing_df.columns:
        existing_df = existing_df.copy()
        existing_df["candle_time_kst"] = pd.to_datetime(existing_df["candle_time_kst"])
    
    if not new_df.empty and "candle_time_kst" in new_df.columns:
        new_df = new_df.copy()
        new_df["candle_time_kst"] = pd.to_datetime(new_df["candle_time_kst"])
    
    # 병합
    if not existing_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined_df = new_df.copy()
    
    # 중복 제거
    combined_df = dedup_monthly_dataframe(combined_df)
    
    # 저장
    meta = save_monthly_csv(combined_df, data_root, meta_root, market, date_kst)
    
    return combined_df, meta

