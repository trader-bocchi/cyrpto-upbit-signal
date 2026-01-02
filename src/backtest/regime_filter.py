"""시장 레짐 필터 (BTC 1D SMA200)"""
import pandas as pd
from typing import Optional, Dict
from pathlib import Path

from src.config import DERIVED_DATA_PATH, MA_SMA200
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.indicators.moving_averages import calculate_sma


def load_btc_1d_data(start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """BTC 1D 데이터 로드 및 SMA200 계산"""
    btc_market = "KRW-BTC"
    filepath = get_candle_filepath(DERIVED_DATA_PATH, btc_market, "1d", year=2025)
    df = read_csv_safe(filepath)
    
    if df.empty:
        return pd.DataFrame()
    
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    
    # 날짜 필터링
    if start_date:
        df = df[df["candle_time_kst"] >= start_date]
    if end_date:
        df = df[df["candle_time_kst"] <= end_date]
    
    if df.empty:
        return pd.DataFrame()
    
    # SMA200 계산
    df = calculate_sma(df, periods=[MA_SMA200])
    
    # 정렬
    df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return df


def get_regime_status(btc_df: pd.DataFrame, timestamp: pd.Timestamp) -> bool:
    """
    특정 시점의 레짐 상태 반환
    
    Args:
        btc_df: BTC 1D 데이터 (SMA200 포함)
        timestamp: 판정할 시점
    
    Returns:
        True: 레짐 ON (BTC close > SMA200), False: 레짐 OFF
    """
    if btc_df.empty:
        return True  # 데이터가 없으면 기본적으로 ON
    
    # 해당 시점 이전의 가장 최근 완성된 1D 캔들 찾기
    # 1D 캔들은 해당 일의 마지막 시점에 완성되므로, timestamp의 날짜 이전 또는 같은 날짜의 마지막 캔들
    timestamp_date = timestamp.date() if hasattr(timestamp, 'date') else pd.to_datetime(timestamp).date()
    
    # timestamp 이전 또는 같은 날짜의 가장 최근 캔들
    filtered = btc_df[btc_df["candle_time_kst"].dt.date <= timestamp_date]
    
    if filtered.empty:
        return True  # 데이터가 없으면 기본적으로 ON
    
    # 가장 최근 캔들
    latest = filtered.iloc[-1]
    
    close = latest["close"]
    sma200 = latest.get(f"sma_{MA_SMA200}", close)
    
    # SMA200이 NaN이면 기본적으로 ON
    if pd.isna(sma200):
        return True
    
    return close > sma200

