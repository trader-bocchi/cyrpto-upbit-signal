"""이동평균 지표"""
import pandas as pd
import numpy as np


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple Moving Average"""
    return series.rolling(window=window, min_periods=1).mean()


def calculate_sma(df: pd.DataFrame, periods: List[int] = [50, 200]) -> pd.DataFrame:
    """
    이동평균 계산
    
    Args:
        df: OHLCV DataFrame (close 컬럼 필요)
        periods: 계산할 기간 리스트
    
    Returns:
        SMA 컬럼이 추가된 DataFrame
    """
    result_df = df.copy()
    
    for period in periods:
        result_df[f"sma_{period}"] = sma(result_df["close"], period)
    
    return result_df

