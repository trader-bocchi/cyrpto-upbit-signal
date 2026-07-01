"""포지션 청산 규칙 — 동적(모멘텀 기반) 타임스탑 (동적A).

동적A: SMI 모멘텀이 살아있는 동안 보유하고, 2봉 연속 하락하면 청산한다.
단, 진입 후 최소 min_bars는 보유(휩쏘 방지)하고, 최대 max_bars에서 강제 청산(상한 캡).
손절/익절이 이 타임스탑보다 우선하며, 이 함수는 '타임스탑' 부분만 판정한다.

주의: 이 규칙은 백테스트/CLI 포지션 청산 모델용이다. 실전 텔레그램 매수/매도
신호(batch/signal_detector, 상태 없는 알림)와는 별개다.
"""
from typing import Sequence, Tuple

import pandas as pd

from src.config import (
    BACKTEST_TIME_STOP_MIN_BARS_4H,
    BACKTEST_TIME_STOP_MAX_BARS_4H,
    BACKTEST_TIME_STOP_MIN_BARS_1D,
    BACKTEST_TIME_STOP_MAX_BARS_1D,
)


def adaptive_bounds(timeframe: str) -> Tuple[int, int]:
    """타임프레임별 (최소 보유 봉, 최대 보유 봉) 반환."""
    if timeframe in ("1d", "24h", "d"):
        return BACKTEST_TIME_STOP_MIN_BARS_1D, BACKTEST_TIME_STOP_MAX_BARS_1D
    return BACKTEST_TIME_STOP_MIN_BARS_4H, BACKTEST_TIME_STOP_MAX_BARS_4H


def momentum_time_stop_hit(
    smi: Sequence[float],
    cur_pos: int,
    hold_bars: int,
    min_bars: int,
    max_bars: int,
) -> bool:
    """동적A 타임스탑 발동 여부.

    Args:
        smi: SMI 모멘텀 시계열(positional)
        cur_pos: 현재 봉의 positional 인덱스
        hold_bars: 진입 후 보유 봉 수
        min_bars: 최소 보유(이 전엔 타임스탑 발동 안 함)
        max_bars: 최대 보유(이 이상이면 강제 발동)
    """
    if hold_bars < min_bars:
        return False
    if hold_bars >= max_bars:
        return True
    if cur_pos >= 2:
        a, b, c = smi[cur_pos - 2], smi[cur_pos - 1], smi[cur_pos]
        if pd.notna(a) and pd.notna(b) and pd.notna(c) and c < b < a:
            return True
    return False
