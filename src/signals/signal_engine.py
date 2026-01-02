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
    use_saved_smi: bool = True,
    check_latest_only: bool = False,
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
        use_saved_smi: 저장된 SMI 사용 여부 (기본 True)
    
    Returns:
        시그널 리스트 (각 시그널은 Dict)
    """
    if df.empty or len(df) < SMI_LOCAL_MIN_WINDOW + 3:
        return []
    
    # SMI 로드 또는 계산
    if use_saved_smi:
        from src.storage.smi_store import load_smi, merge_smi_with_candles
        smi_df = load_smi(market, timeframe, year=2025)
        if not smi_df.empty:
            df = merge_smi_with_candles(df, smi_df)
            # SMI가 없는 행은 계산
            missing_smi = df["smi_momentum"].isna()
            if missing_smi.any():
                df_missing = df[missing_smi].copy()
                df_missing = calculate_smi(df_missing)
                df.loc[missing_smi, "smi_momentum"] = df_missing["smi_momentum"]
                if "squeeze_on" in df_missing.columns:
                    df.loc[missing_smi, "squeeze_on"] = df_missing["squeeze_on"]
        else:
            # 저장된 SMI가 없으면 계산
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
        # 마지막 행만 체크
        i = len(df) - 1
        pivot_idx_loc = int(df.iloc[i]["pivot_idx"])
        
        if pivot_idx_loc >= 0 and pivot_idx_loc < i - 1:
            # 피벗 + 2 시점이 현재 시점과 일치하는지 확인
            if pivot_idx_loc + 2 == i:
                pivot_idx = df.index[pivot_idx_loc]
                pivot_value = df.loc[pivot_idx, "smi_momentum"]
                
                m_i = df.loc[pivot_idx, "smi_momentum"]
                m_i1_idx = df.index[pivot_idx_loc + 1]
                m_i2_idx = df.index[pivot_idx_loc + 2]  # 현재 i
                
                m_i1 = df.loc[m_i1_idx, "smi_momentum"]
                m_i2 = df.loc[m_i2_idx, "smi_momentum"]
                
                # 2단계 회복 조건
                if m_i2 > m_i1 > m_i:
                    # 시그널 시점 = i+2 bar (현재 i)
                    signal_idx = m_i2_idx
                    signal_row = df.loc[signal_idx]
                    
                    # SMA50 필터
                    if not SIGNAL_ENABLE_SMA50_FILTER or (not pd.isna(signal_row["sma_50"]) and signal_row["close"] > signal_row["sma_50"]):
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
    
    # 피벗 정보를 기반으로 시그널 탐색 (과거 모든 시그널)
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

