"""매수 시그널 엔진"""
import pandas as pd
from typing import List, Dict, Optional
from rich.console import Console

from src.config import (
    SMI_LOCAL_MIN_WINDOW,
    SMI_REQUIRE_NEGATIVE_PIVOT,
    SIGNAL_ENABLE_SMA50_FILTER,
)
from src.indicators.squeeze_momentum import calculate_smi
from src.indicators.moving_averages import calculate_sma
from src.indicators.extrema import find_pivot_min

console = Console()


def detect_buy_signals(
    df: pd.DataFrame,
    market: str,
    timeframe: str,
) -> List[Dict]:
    """
    매수 시그널 감지
    
    규칙:
    1. SMI momentum의 로컬 미니멈 중 최저값 pivot_min 선택 (최근 N개 내)
    2. "2단계 회복": m[i+2] > m[i+1] > m[i]
    3. 시그널 시점 = i+2 bar
    4. 추가 조건: m[i] < 0 (기본 on)
    5. SMA50 필터: close > SMA50 (기본 on)
    
    Args:
        df: OHLCV + 지표 DataFrame
        market: 마켓 코드
        timeframe: 시간프레임
    
    Returns:
        시그널 리스트 (각 시그널은 Dict)
    """
    if df.empty or len(df) < SMI_LOCAL_MIN_WINDOW + 3:
        return []
    
    # 지표 계산
    df = calculate_smi(df)
    df = calculate_sma(df, periods=[50, 200])
    
    # 피벗 찾기
    pivot_info = find_pivot_min(
        df["smi_momentum"],
        window=SMI_LOCAL_MIN_WINDOW,
        require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
    )
    
    df = pd.concat([df, pivot_info], axis=1)
    
    signals = []
    
    # 피벗 정보를 기반으로 시그널 탐색
    for i in range(SMI_LOCAL_MIN_WINDOW + 2, len(df)):
        pivot_idx_loc = int(df.iloc[i]["pivot_idx"])
        
        if pivot_idx_loc < 0:
            continue
        
        # 피벗이 현재 시점보다 최소 2칸 이전이어야 함
        if pivot_idx_loc >= i - 1:
            continue
        
        # 피벗 시점
        pivot_idx = df.index[pivot_idx_loc]
        pivot_value = df.loc[pivot_idx, "smi_momentum"]
        
        # 피벗 + 2 시점이 현재 시점과 일치하는지 확인
        if pivot_idx_loc + 2 != i:
            continue
        
        m_i = df.loc[pivot_idx, "smi_momentum"]
        m_i1_idx = df.index[pivot_idx_loc + 1]
        m_i2_idx = df.index[pivot_idx_loc + 2]  # 현재 i
        
        m_i1 = df.loc[m_i1_idx, "smi_momentum"]
        m_i2 = df.loc[m_i2_idx, "smi_momentum"]
        
        # 2단계 회복 조건
        if not (m_i2 > m_i1 > m_i):
            continue
        
        # 시그널 시점 = i+2 bar (현재 i)
        signal_idx = m_i2_idx
        signal_row = df.loc[signal_idx]
        
        # SMA50 필터
        if SIGNAL_ENABLE_SMA50_FILTER:
            if pd.isna(signal_row["sma_50"]) or signal_row["close"] <= signal_row["sma_50"]:
                continue
        
        # 시그널 생성
        signal = {
            "market": market,
            "timeframe": timeframe,
            "signal_time_kst": str(signal_row["candle_time_kst"]),
            "side": "BUY",
            "close": float(signal_row["close"]),
            "smi_pivot_min": float(pivot_value),
            "smi_m_i": float(m_i),
            "smi_m_i1": float(m_i1),
            "smi_m_i2": float(m_i2),
            "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
            "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
            "sma200_above": bool(signal_row["close"] > signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
        }
        
        signals.append(signal)
    
    return signals

