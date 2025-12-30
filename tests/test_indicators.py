"""지표 계산 테스트"""
import pandas as pd
import pytest

from src.indicators.moving_averages import sma, calculate_sma
from src.indicators.squeeze_momentum import calculate_smi


def test_sma():
    """이동평균 테스트"""
    series = pd.Series([100, 101, 102, 103, 104])
    result = sma(series, window=3)
    
    assert len(result) == 5
    assert result.iloc[4] == pytest.approx(103.0, abs=0.1)


def test_calculate_sma():
    """SMA 계산 테스트"""
    df = pd.DataFrame({
        "close": [100, 101, 102, 103, 104, 105] * 10,
        "high": [110] * 60,
        "low": [90] * 60,
        "open": [100] * 60,
        "volume": [1000] * 60,
    })
    
    result = calculate_sma(df, periods=[5, 10])
    
    assert "sma_5" in result.columns
    assert "sma_10" in result.columns

