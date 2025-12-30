"""중복 제거 모듈"""
import pandas as pd
from typing import Tuple, Optional
from datetime import datetime

from src.storage.csv_store import ensure_candle_dtypes


def dedup_candles(
    df: pd.DataFrame,
    unique_key: Tuple[str, str] = ("market", "candle_time_kst"),
    keep: str = "last",
) -> pd.DataFrame:
    """
    캔들 DataFrame 중복 제거
    
    Args:
        df: 입력 DataFrame
        unique_key: 유니크 키 컬럼 튜플
        keep: 중복 시 유지 방식 ('first', 'last', False)
    
    Returns:
        중복 제거된 DataFrame (candle_time_kst 오름차순 정렬)
    """
    if df.empty:
        return df
    
    df = ensure_candle_dtypes(df)
    
    # ingest_time_kst가 있으면 이를 기준으로 keep='last' 처리
    if "ingest_time_kst" in df.columns:
        # ingest_time_kst 기준으로 정렬 후 중복 제거
        df = df.sort_values("ingest_time_kst", ascending=True)
        df = df.drop_duplicates(subset=list(unique_key), keep="last")
    else:
        # ingest_time_kst가 없으면 기본 keep 방식 사용
        df = df.drop_duplicates(subset=list(unique_key), keep=keep)
    
    # candle_time_kst 기준 정렬
    df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return df


def validate_no_duplicates(
    df: pd.DataFrame,
    unique_key: Tuple[str, str] = ("market", "candle_time_kst"),
) -> bool:
    """중복이 없는지 검증"""
    if df.empty:
        return True
    
    duplicates = df.duplicated(subset=list(unique_key), keep=False)
    return not duplicates.any()

