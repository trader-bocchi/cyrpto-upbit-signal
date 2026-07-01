"""매수 시그널 엔진"""
import pandas as pd
from typing import List, Dict, Optional
from rich.console import Console

from src.config import (
    SMI_LOCAL_MIN_WINDOW,
    SMI_REQUIRE_NEGATIVE_PIVOT,
)
from src.indicators.squeeze_momentum import calculate_smi
from src.indicators.moving_averages import calculate_sma
from src.indicators.extrema import find_pivot_min
from src.signals.smi_rule import buy_signal_fields

console = Console()


def detect_buy_signals(
    df: pd.DataFrame,
    market: str,
    timeframe: str,
    use_saved_smi: bool = True,
    check_latest_only: bool = False,
) -> List[Dict]:
    """
    매수 시그널 감지
    
    규칙 (실전 batch/signal_detector와 동일한 src.signals.smi_rule 사용):
    1. SMI momentum의 로컬 미니멈(pivot)이 현재 봉 기준 정확히 2칸 전
    2. "2단계 회복": m[i+2] > m[i+1] > m[i]
    3. 시그널 시점 = i+2 bar
    4. 추가 조건: pivot 값 m[i] < 0 (SMI_REQUIRE_NEGATIVE_PIVOT)
    추세/SMA 필터 없음 — 실전 발신과 동일하게 진입만 판정.

    Args:
        df: OHLCV + 지표 DataFrame
        market: 마켓 코드
        timeframe: 시간프레임
        use_saved_smi: 저장된 SMI 사용 여부 (기본 True)
    
    Returns:
        시그널 리스트 (각 시그널은 Dict)
    """
    if df.empty or len(df) < SMI_LOCAL_MIN_WINDOW + 3:
        return []
    
    # SMI 로드 또는 계산 (최적화: 저장된 SMI 우선 사용, 계산 최소화)
    if use_saved_smi:
        from src.storage.smi_store import load_smi, merge_smi_with_candles
        # 모든 연도 데이터 로드 (year=None으로 모든 연도 로드)
        smi_df = load_smi(market, timeframe, year=None)
        if not smi_df.empty:
            # 저장된 SMI와 병합
            df = merge_smi_with_candles(df, smi_df)
            # SMI가 없는 행이 있으면 해당 부분만 계산
            missing_smi = df["smi_momentum"].isna()
            if missing_smi.any():
                # 누락된 부분만 계산 (전체 재계산 방지)
                # SMI 계산을 위해 전체 데이터가 필요하므로, 누락된 부분이 있으면 전체 재계산
                # 하지만 저장된 SMI가 대부분이면 빠를 수 있음
                df = calculate_smi(df)
        else:
            # 저장된 SMI가 없으면 전체 계산
            df = calculate_smi(df)
    else:
        # 저장된 SMI 사용 안 함
        df = calculate_smi(df)
    
    # 이동평균 계산
    df = calculate_sma(df, periods=[50, 200])
    
    # 피벗 찾기
    pivot_info = find_pivot_min(
        df["smi_momentum"],
        window=SMI_LOCAL_MIN_WINDOW,
        require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
    )
    
    df = pd.concat([df, pivot_info], axis=1)
    
    signals = []
    
    # 마지막 행만 체크하는 경우
    if check_latest_only:
        if len(df) < SMI_LOCAL_MIN_WINDOW + 3:
            return []

        # 마지막 행 인덱스
        i = len(df) - 1

        fields = buy_signal_fields(df, i)
        if fields is None:
            return []
        m_i, m_i1, m_i2 = fields

        signal_row = df.iloc[i]

        # 시그널 생성
        signal = {
            "market": market,
            "timeframe": timeframe,
            "signal_time_kst": str(signal_row["candle_time_kst"]),
            "side": "BUY",
            "close": float(signal_row["close"]),
            "smi_pivot_min": float(m_i),  # 피벗 값
            "smi_m_i": float(m_i),
            "smi_m_i1": float(m_i1),
            "smi_m_i2": float(m_i2),
            "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
            "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
            "sma200_above": bool(signal_row["close"] > signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
        }
        signals.append(signal)

        return signals
    
    # 피벗 정보를 기반으로 시그널 탐색 (과거 모든 시그널)
    # buy_signal_fields는 positional(iloc) 접근만 하므로 인덱스 형태와 무관하게 동작.
    for i in range(SMI_LOCAL_MIN_WINDOW + 2, len(df)):
        fields = buy_signal_fields(df, i)
        if fields is None:
            continue
        m_i, m_i1, m_i2 = fields

        # 시그널 시점 = i+2 bar (현재 i)
        signal_row = df.iloc[i]

        # 시그널 생성
        signal = {
            "market": market,
            "timeframe": timeframe,
            "signal_time_kst": str(signal_row["candle_time_kst"]),
            "side": "BUY",
            "close": float(signal_row["close"]),
            "smi_pivot_min": float(m_i),
            "smi_m_i": float(m_i),
            "smi_m_i1": float(m_i1),
            "smi_m_i2": float(m_i2),
            "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
            "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
            "sma200_above": bool(signal_row["close"] > signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
        }

        signals.append(signal)

    return signals

