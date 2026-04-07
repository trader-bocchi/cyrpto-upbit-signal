"""지표 계산 테스트"""
import pandas as pd
import numpy as np
import pytest

from src.indicators.moving_averages import sma, calculate_sma
from src.indicators.squeeze_momentum import calculate_smi
from src.indicators.extrema import (
    find_local_minima,
    find_local_maxima,
    find_pivot_min,
    find_pivot_max,
)


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


def test_find_local_maxima():
    """로컬 맥시멈 찾기 테스트"""
    series = pd.Series([1, 3, 2, 5, 4, 2, 6, 3])
    result = find_local_maxima(series, window=1)

    # index 1 (3), index 3 (5), index 6 (6) 이 로컬 맥시멈
    assert bool(result.iloc[1]) is True
    assert bool(result.iloc[3]) is True
    assert bool(result.iloc[6]) is True
    # 비극값
    assert bool(result.iloc[0]) is False
    assert bool(result.iloc[2]) is False


def test_find_local_minima_vs_maxima():
    """로컬 미니멈과 맥시멈이 서로 배타적인지 확인"""
    series = pd.Series([5, 2, 4, 1, 3, 6, 2, 7, 1])
    minima = find_local_minima(series, window=1)
    maxima = find_local_maxima(series, window=1)

    # 같은 위치가 동시에 미니멈이자 맥시멈일 수 없음
    for i in range(len(series)):
        assert not (minima.iloc[i] and maxima.iloc[i])


def test_find_pivot_max_basic():
    """find_pivot_max 기본 동작 테스트"""
    # window=5로 작은 테스트: 맥시멈이 명확한 시리즈
    # 인덱스 5가 로컬 맥시멈 (값 10)
    values = [1, 2, 3, 2, 1, 10, 8, 6, 5, 4, 3]
    series = pd.Series(values)

    result = find_pivot_max(series, window=5, require_positive=False)

    assert "pivot_max" in result.columns
    assert "pivot_max_idx" in result.columns
    assert "is_pivot_max" in result.columns

    # window=5 이후부터 피벗이 존재해야 함
    # index 5가 맥시멈이므로, index 6 이후에서 pivot_max_idx=5 이어야 함
    assert result.iloc[6]["pivot_max_idx"] == 5
    assert result.iloc[6]["pivot_max"] == pytest.approx(10.0)


def test_find_pivot_max_require_positive():
    """require_positive=True 일 때 음수 피벗은 무시"""
    # 모든 로컬 맥시멈이 음수인 데이터 (양수 값 없음)
    values = [-5, -3, -5, -2, -5, -1, -2, -3, -4, -5, -6]
    series = pd.Series(values)

    # require_positive=True 이면 양수 피벗 없음 → 모두 -1
    result = find_pivot_max(series, window=5, require_positive=True)
    assert (result["pivot_max_idx"] == -1).all()

    # require_positive=False 이면 음수 맥시멈도 피벗으로 선택됨
    result2 = find_pivot_max(series, window=5, require_positive=False)
    pivot_found = (result2["pivot_max_idx"] >= 0).any()
    assert pivot_found


def _make_sell_signal_df(n_base: int = 110) -> pd.DataFrame:
    """
    매도 시그널 조건을 충족하는 합성 DataFrame 생성
    - SMI momentum이 양수 고점(로컬 맥시멈)을 지나 2단계 하락
    - SMA50 필터: close < SMA50 (매도 시그널 조건)
      → 처음 100개 close=60, 마지막 10개 close=40
      → SMA50(마지막) ≈ 56.0 > close(40.0) ✓
    - candle_time_kst 컬럼 포함
    """
    n = n_base  # 110

    # close: 처음 100개=60, 마지막 10개=40
    # SMA50 at index n-1: 40×60 + 10×40 / 50 = 56.0 → close(40) < sma50(56) ✓
    close_vals = [60.0] * 100 + [40.0] * (n - 100)

    # SMI momentum: 로컬 맥시멈 → 2단계 하락 패턴
    smi_vals = [0.0] * n
    pivot_pos = n - 3  # 107: 로컬 맥시멈 위치
    smi_vals[pivot_pos - 1] = 3.0   # 106: pivot 직전 (낮아야 local max 성립)
    smi_vals[pivot_pos] = 5.0       # 107: 로컬 맥시멈 (pivot)
    smi_vals[pivot_pos + 1] = 4.0   # 108: pivot+1 (감소)
    smi_vals[pivot_pos + 2] = 3.0   # 109: pivot+2 = 현재 (더 감소) → m_i2

    candle_times = pd.date_range("2025-01-01", periods=n, freq="4h")

    df = pd.DataFrame({
        "candle_time_kst": candle_times,
        "open": close_vals,
        "high": [v + 5 for v in close_vals],
        "low": [v - 5 for v in close_vals],
        "close": close_vals,
        "volume": [1000.0] * n,
        "smi_momentum": smi_vals,
    })
    return df


def test_sell_signal_detected():
    """SMI 매도 시그널이 올바르게 감지되는지 테스트"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from batch.signal_detector import check_smi_sell_signal

    df = _make_sell_signal_df(n_base=110)

    signal = check_smi_sell_signal(df, "4h")

    # 시그널이 반환되어야 함
    assert signal is not None
    assert signal["side"] == "SELL"
    # 연속 하락 확인: m_i > m_i1 > m_i2
    assert signal["smi_m_i"] > signal["smi_m_i1"] > signal["smi_m_i2"]


def test_sell_signal_not_detected_when_smi_negative():
    """SMI 마지막 값이 음수이면 매도 시그널 없음"""
    from batch.signal_detector import check_smi_sell_signal

    df = _make_sell_signal_df(n_base=110)
    # 마지막 SMI를 음수로 변경
    df.loc[df.index[-1], "smi_momentum"] = -1.0

    signal = check_smi_sell_signal(df, "4h")
    assert signal is None


def test_sell_signal_not_detected_when_not_two_step_decline():
    """2단계 하락 조건 미충족 시 매도 시그널 없음"""
    from batch.signal_detector import check_smi_sell_signal

    df = _make_sell_signal_df(n_base=110)
    # pivot+1 값을 pivot보다 크게 만들어 단조 하락 조건 깨기
    n = len(df)
    df.loc[df.index[n - 2], "smi_momentum"] = 6.0  # pivot+1 > pivot → 조건 위반

    signal = check_smi_sell_signal(df, "4h")
    assert signal is None

