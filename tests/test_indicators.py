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


def test_sell_signal_not_detected_when_pivot_not_positive():
    """피벗(국소천장)이 양수가 아니면 매도 시그널 없음 (SMI_REQUIRE_POSITIVE_PIVOT).

    주의: 현재봉 SMI가 음수라도 피벗이 양수이고 2봉 연속 하락이면 시그널은 유효하다
    (commit 9d796ba에서 m_i2>=0 조건을 의도적으로 제거). 따라서 음수로 가려야 하는
    대상은 '현재봉'이 아니라 '피벗' 값이다.
    """
    from batch.signal_detector import check_smi_sell_signal

    df = _make_sell_signal_df(n_base=110)
    # pivot 봉(국소천장)을 음수로 만들면 require_positive에 의해 시그널 없음
    n = len(df)
    pivot_pos = n - 3  # 107
    df.loc[df.index[pivot_pos - 1], "smi_momentum"] = -3.0
    df.loc[df.index[pivot_pos], "smi_momentum"] = -1.0      # 국소천장이지만 음수
    df.loc[df.index[pivot_pos + 1], "smi_momentum"] = -2.0
    df.loc[df.index[pivot_pos + 2], "smi_momentum"] = -3.0

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


def _make_buy_signal_df(n_base: int = 110) -> pd.DataFrame:
    """매수 시그널 조건을 충족하는 합성 DataFrame 생성.

    SMI momentum이 음수 국소바닥(로컬 미니멈)을 지나 2단계 상승하는 패턴.
    매도 합성 데이터의 정확한 거울. close 값은 매수 판정에 영향 없음(추세 필터 없음).
    """
    n = n_base  # 110

    close_vals = [50.0] * n

    smi_vals = [0.0] * n
    pivot_pos = n - 3  # 107: 로컬 미니멈 위치
    smi_vals[pivot_pos - 1] = -3.0  # 106: pivot 직전 (높아야 local min 성립)
    smi_vals[pivot_pos] = -5.0      # 107: 로컬 미니멈 (pivot, 음수)
    smi_vals[pivot_pos + 1] = -4.0  # 108: pivot+1 (증가)
    smi_vals[pivot_pos + 2] = -3.0  # 109: pivot+2 = 현재 (더 증가) → m_i2

    candle_times = pd.date_range("2025-01-01", periods=n, freq="4h")

    return pd.DataFrame({
        "candle_time_kst": candle_times,
        "open": close_vals,
        "high": [v + 5 for v in close_vals],
        "low": [v - 5 for v in close_vals],
        "close": close_vals,
        "volume": [1000.0] * n,
        "smi_momentum": smi_vals,
    })


def test_dispatch_log(tmp_path, monkeypatch):
    """발송 감사 로그: 메시지와 근거(시그널) 데이터가 함께 기록되는지"""
    import json
    import src.storage.dispatch_log as dl

    monkeypatch.setattr(dl, "LOG_DIR", tmp_path)
    dl.log_dispatch(
        "메시지본문",
        {
            "buy_4h": [("KRW-BTC", {"close": 100.0, "smi_m_i2": -1.0, "strength": "STRONG"})],
            "sell_4h": [],
            "buy_1d": [],
            "sell_1d": [],
        },
        success=True,
    )
    files = list(tmp_path.glob("dispatch_*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert rec["success"] is True
    assert rec["message"] == "메시지본문"
    assert rec["counts"]["buy_4h"] == 1
    assert rec["signals"]["buy_4h"][0]["market"] == "KRW-BTC"
    assert rec["signals"]["buy_4h"][0]["close"] == 100.0


def test_momentum_time_stop_hit():
    """동적A 타임스탑: 최소보유 후 SMI 2봉 하락 시 발동, 상승 중엔 미발동, 상한 캡"""
    from src.signals.exit_rules import momentum_time_stop_hit

    smi_down = [0, 1, 2, 3, 6, 5, 4]  # 마지막 3봉 6>5>4 하락
    # 최소보유 미달 → 미발동
    assert momentum_time_stop_hit(smi_down, cur_pos=6, hold_bars=2, min_bars=6, max_bars=42) is False
    # 최소보유 충족 + 2봉 연속 하락 → 발동
    assert momentum_time_stop_hit(smi_down, cur_pos=6, hold_bars=6, min_bars=6, max_bars=42) is True

    smi_up = [0, 1, 2, 3, 4, 5, 6]  # 계속 상승
    # 상승 중 → 미발동
    assert momentum_time_stop_hit(smi_up, cur_pos=6, hold_bars=10, min_bars=6, max_bars=42) is False
    # 최대보유 도달 → 강제 발동
    assert momentum_time_stop_hit(smi_up, cur_pos=6, hold_bars=42, min_bars=6, max_bars=42) is True


def test_drop_incomplete_candle():
    """수집 단계에서 마지막(진행 중) 캔들이 제거되는지 — 리페인팅 방지"""
    from batch.fetch_data import drop_incomplete_candle

    df = pd.DataFrame({
        "candle_time_kst": pd.date_range("2025-01-01", periods=5, freq="4h"),
        "close": [10.0, 11.0, 12.0, 13.0, 99.0],  # 마지막 = 미완성 봉
    })
    result = drop_incomplete_candle(df)

    assert len(result) == 4
    assert 99.0 not in result["close"].values
    assert result["close"].iloc[-1] == 13.0
    # 빈 DataFrame은 그대로
    assert drop_incomplete_candle(pd.DataFrame()).empty


def test_buy_signal_detected():
    """SMI 매수 시그널(2봉 회복)이 올바르게 감지되는지 테스트"""
    from batch.signal_detector import check_smi_signal

    df = _make_buy_signal_df(n_base=110)
    signal = check_smi_signal(df, "4h")

    assert signal is not None
    assert signal["side"] == "BUY"
    # 2단계 상승: m_i < m_i1 < m_i2
    assert signal["smi_m_i"] < signal["smi_m_i1"] < signal["smi_m_i2"]
    # 피벗은 음수여야 함
    assert signal["smi_pivot_min"] < 0


def test_buy_signal_not_detected_when_pivot_positive():
    """피벗(국소바닥)이 음수가 아니면 매수 시그널 없음 (SMI_REQUIRE_NEGATIVE_PIVOT)"""
    from batch.signal_detector import check_smi_signal

    df = _make_buy_signal_df(n_base=110)
    n = len(df)
    pivot_pos = n - 3
    # 바닥 패턴을 양수 영역으로 평행이동 → require_negative에 의해 시그널 없음
    df.loc[df.index[pivot_pos - 1], "smi_momentum"] = 3.0
    df.loc[df.index[pivot_pos], "smi_momentum"] = 1.0       # 국소바닥이지만 양수
    df.loc[df.index[pivot_pos + 1], "smi_momentum"] = 2.0
    df.loc[df.index[pivot_pos + 2], "smi_momentum"] = 3.0

    signal = check_smi_signal(df, "4h")
    assert signal is None


def _make_oscillating_ohlc(n: int = 300) -> pd.DataFrame:
    """SMI가 0을 오가며 국소 극값/2봉 패턴을 만들도록 진동하는 OHLC 합성 데이터.

    실전(check_smi_signal)과 백테스트(detect_buy_signals)가 같은 OHLC로부터 동일한
    매수 시그널을 내는지 검증하는 정합성 테스트용. (결정적: 난수 없음)
    """
    idx = np.arange(n)
    base = 100.0 + 12.0 * np.sin(idx / 6.0) + 4.0 * np.sin(idx / 2.3)
    close = base
    high = base + 1.5
    low = base - 1.5
    open_ = base - 0.3
    candle_times = pd.date_range("2025-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "candle_time_kst": candle_times,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": [1000.0] * n,
    })


def test_backtest_entry_matches_production():
    """백테스트(detect_buy_signals)와 실전(check_smi_signal)의 진입 규칙 정합성.

    동일한 OHLC에서 백테스트가 잡아낸 모든 매수 시그널은, 해당 봉을 마지막 봉으로
    하는 구간에 대해 실전 감지기도 동일하게 잡아내야 한다. (단일 규칙 smi_rule 공용)
    """
    from batch.signal_detector import check_smi_signal
    from src.signals.signal_engine import detect_buy_signals

    df_ohlc = _make_oscillating_ohlc(n=300)
    df_smi = calculate_smi(df_ohlc.copy())

    bt_signals = detect_buy_signals(
        df_ohlc.copy(), "TEST", "4h", use_saved_smi=False
    )

    # 진동 데이터는 최소 한 개 이상의 매수 시그널을 만들어야 테스트가 유의미
    assert len(bt_signals) > 0

    for s in bt_signals:
        t = s["signal_time_kst"]
        # 시그널 봉을 마지막 봉으로 하는 구간 슬라이스
        mask = df_smi["candle_time_kst"].astype(str) == t
        pos = int(np.flatnonzero(mask.to_numpy())[0])
        sub = df_smi.iloc[: pos + 1].copy()

        prod = check_smi_signal(sub, "4h")
        assert prod is not None, f"실전 감지기가 {t} 시그널을 놓침"
        assert prod["signal_time_kst"] == t
        # 핵심 SMI 값도 일치
        assert prod["smi_m_i"] == pytest.approx(s["smi_m_i"])
        assert prod["smi_m_i2"] == pytest.approx(s["smi_m_i2"])

