"""SMI 2봉 회복/반전 시그널의 단일 판정 규칙.

실전(batch/signal_detector)과 백테스트(src/backtest)가 **동일한** 진입 조건을
쓰도록 per-bar 판정을 한 곳에 모은다. 추세·거래량·SMA 필터 없음 — 실전 발신과
정확히 같은 규칙임을 코드 레벨에서 보장한다.

매수: SMI 모멘텀이 음수 국소바닥(pivot)을 i-2에서 찍고 pivot < i-1 < i 2봉 연속 상승.
매도: SMI 모멘텀이 양수 국소천장(pivot)을 i-2에서 찍고 pivot > i-1 > i 2봉 연속 하락.

입력 df는 positional(iloc) 접근만 사용하므로 인덱스 형태와 무관하게 동작한다.
df에는 smi_momentum 과 pivot 위치 컬럼(매수=pivot_idx, 매도=pivot_max_idx)이
이미 계산되어 있어야 한다.
"""
import pandas as pd
from typing import Optional, Tuple

from src.config import (
    SMI_REQUIRE_NEGATIVE_PIVOT,
    SMI_REQUIRE_POSITIVE_PIVOT,
)


def buy_signal_fields(df: pd.DataFrame, i: int) -> Optional[Tuple[float, float, float]]:
    """완성된 봉 i가 매수 조건을 만족하면 (m_i, m_i1, m_i2), 아니면 None.

    m_i = pivot(국소바닥) 값, m_i1 = pivot+1, m_i2 = 현재봉 i(=pivot+2).
    """
    m_i2 = df.iloc[i]["smi_momentum"]
    if pd.isna(m_i2):
        return None

    pivot_idx_loc = int(df.iloc[i]["pivot_idx"])
    if pivot_idx_loc < 0 or pivot_idx_loc != i - 2:
        return None

    m_i = df.iloc[pivot_idx_loc]["smi_momentum"]
    m_i1 = df.iloc[pivot_idx_loc + 1]["smi_momentum"]
    if pd.isna(m_i) or pd.isna(m_i1):
        return None

    # 2봉 연속 상승: pivot < pivot+1 < 현재
    if not (m_i2 > m_i1 > m_i):
        return None

    if SMI_REQUIRE_NEGATIVE_PIVOT and m_i >= 0:
        return None

    return float(m_i), float(m_i1), float(m_i2)


def sell_signal_fields(df: pd.DataFrame, i: int) -> Optional[Tuple[float, float, float]]:
    """완성된 봉 i가 매도 조건을 만족하면 (m_i, m_i1, m_i2), 아니면 None.

    m_i = pivot(국소천장) 값, m_i1 = pivot+1, m_i2 = 현재봉 i(=pivot+2).
    """
    m_i2 = df.iloc[i]["smi_momentum"]
    if pd.isna(m_i2):
        return None

    pivot_idx_loc = int(df.iloc[i]["pivot_max_idx"])
    if pivot_idx_loc < 0 or pivot_idx_loc != i - 2:
        return None

    m_i = df.iloc[pivot_idx_loc]["smi_momentum"]
    m_i1 = df.iloc[pivot_idx_loc + 1]["smi_momentum"]
    if pd.isna(m_i) or pd.isna(m_i1):
        return None

    # 2봉 연속 하락: pivot > pivot+1 > 현재
    if not (m_i2 < m_i1 < m_i):
        return None

    if SMI_REQUIRE_POSITIVE_PIVOT and m_i <= 0:
        return None

    return float(m_i), float(m_i1), float(m_i2)
