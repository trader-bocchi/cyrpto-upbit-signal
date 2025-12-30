"""중복 제거 테스트"""
import pandas as pd
import pytest
from datetime import datetime

from src.storage.dedup import dedup_candles, validate_no_duplicates


def test_dedup_candles():
    """중복 제거 테스트"""
    df = pd.DataFrame({
        "market": ["KRW-BTC", "KRW-BTC", "KRW-BTC"],
        "candle_time_kst": ["2025-01-01 00:00:00", "2025-01-01 00:00:00", "2025-01-01 01:00:00"],
        "open": [100, 101, 102],
        "high": [110, 111, 112],
        "low": [90, 91, 92],
        "close": [105, 106, 107],
        "volume": [1000, 2000, 3000],
        "ingest_time_kst": ["2025-01-01 10:00:00", "2025-01-01 11:00:00", "2025-01-01 12:00:00"],
    })
    
    result = dedup_candles(df)
    
    # 중복이 제거되어야 함
    assert len(result) == 2
    assert validate_no_duplicates(result)

