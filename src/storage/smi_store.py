"""SMI 지표 저장소 모듈"""
import pandas as pd
from pathlib import Path
from typing import Optional
from datetime import datetime

from src.config import DERIVED_DATA_PATH
from src.storage.csv_store import read_csv_safe, atomic_write_csv, ensure_candle_dtypes


def get_smi_filepath(market: str, timeframe: str, year: Optional[int] = None) -> Path:
    """
    SMI 파일 경로 생성
    
    Args:
        market: 마켓 코드
        timeframe: 시간프레임 (4h, 1d)
        year: 연도 (None이면 2025)
    
    Returns:
        경로 (예: data/derived/indicators/smi_4h/market=KRW-BTC/year=2025/2025.csv)
    """
    if year is None:
        year = 2025
    
    base_path = DERIVED_DATA_PATH / "indicators" / f"smi_{timeframe}"
    market_dir = base_path / f"market={market}" / f"year={year}"
    market_dir.mkdir(parents=True, exist_ok=True)
    
    return market_dir / f"{year}.csv"


def save_smi(df: pd.DataFrame, market: str, timeframe: str, year: Optional[int] = None):
    """
    SMI 지표 저장
    
    Args:
        df: SMI 지표가 포함된 DataFrame (market, candle_time_kst, smi_momentum, squeeze_on 컬럼 필요)
        market: 마켓 코드
        timeframe: 시간프레임
        year: 연도
    """
    if df.empty:
        return
    
    # 필수 컬럼 확인
    required_cols = ["market", "candle_time_kst", "smi_momentum"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return
    
    # SMI 데이터만 추출
    smi_df = df[["market", "candle_time_kst", "smi_momentum"]].copy()
    if "squeeze_on" in df.columns:
        smi_df["squeeze_on"] = df["squeeze_on"]
    
    # 기존 데이터 로드
    filepath = get_smi_filepath(market, timeframe, year)
    existing_df = read_csv_safe(filepath)
    
    # 병합
    if not existing_df.empty:
        combined_df = pd.concat([existing_df, smi_df], ignore_index=True)
    else:
        combined_df = smi_df.copy()
    
    # 중복 제거 (market, candle_time_kst 기준)
    combined_df = combined_df.drop_duplicates(subset=["market", "candle_time_kst"], keep="last")
    
    # 정렬
    combined_df["candle_time_kst"] = pd.to_datetime(combined_df["candle_time_kst"])
    combined_df = combined_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # 저장
    atomic_write_csv(combined_df, filepath)


def load_smi(market: str, timeframe: str, year: Optional[int] = None) -> pd.DataFrame:
    """
    SMI 지표 로드
    
    Args:
        market: 마켓 코드
        timeframe: 시간프레임
        year: 연도
    
    Returns:
        SMI DataFrame
    """
    filepath = get_smi_filepath(market, timeframe, year)
    return read_csv_safe(filepath)


def merge_smi_with_candles(candles_df: pd.DataFrame, smi_df: pd.DataFrame) -> pd.DataFrame:
    """
    캔들 데이터와 SMI 데이터 병합
    
    Args:
        candles_df: 캔들 DataFrame
        smi_df: SMI DataFrame
    
    Returns:
        병합된 DataFrame
    """
    if candles_df.empty:
        return candles_df
    
    if smi_df.empty:
        return candles_df
    
    # candle_time_kst를 datetime으로 통일
    candles_df = candles_df.copy()
    smi_df = smi_df.copy()
    
    candles_df["candle_time_kst"] = pd.to_datetime(candles_df["candle_time_kst"])
    smi_df["candle_time_kst"] = pd.to_datetime(smi_df["candle_time_kst"])
    
    # 병합 (left join)
    result_df = candles_df.merge(
        smi_df[["candle_time_kst", "smi_momentum"] + (["squeeze_on"] if "squeeze_on" in smi_df.columns else [])],
        on="candle_time_kst",
        how="left",
    )
    
    return result_df

