"""매도 시그널 엔진 (손절/익절)"""
import pandas as pd
from typing import List, Dict, Optional
from rich.console import Console

from src.config import (
    BACKTEST_STOP_LOSS_PCT,
    BACKTEST_TAKE_PROFIT_PCT,
)
from src.storage.positions_store import get_all_positions, remove_position
from src.indicators.moving_averages import calculate_sma

console = Console()


def check_sell_signals(
    df: pd.DataFrame,
    market: str,
    timeframe: str,
) -> List[Dict]:
    """
    매도 시그널 체크 (손절/익절)
    
    규칙:
    - 손절: close <= entry_price * (1 - 0.02)
    - 익절: close >= entry_price * (1 + 0.05)
    - 판단 기준: close (high/low 아님)
    
    Args:
        df: OHLCV DataFrame
        market: 마켓 코드
        timeframe: 시간프레임
    
    Returns:
        시그널 리스트
    """
    from src.storage.positions_store import get_position
    
    position = get_position(market, timeframe)
    if not position:
        return []
    
    entry_price = float(position["entry_price"])
    entry_time_kst = position["entry_time_kst"]
    
    # 이동평균 계산 (메시지용)
    df = calculate_sma(df, periods=[200])
    
    signals = []
    
    for idx, row in df.iterrows():
        candle_time = str(row["candle_time_kst"])
        close = float(row["close"])
        
        # 손절 체크
        if close <= entry_price * (1 - BACKTEST_STOP_LOSS_PCT):
            signal = {
                "market": market,
                "timeframe": timeframe,
                "signal_time_kst": candle_time,
                "side": "SELL",
                "reason": "STOP",
                "entry_price": entry_price,
                "entry_time_kst": entry_time_kst,
                "exit_price": close,
                "pnl_pct": ((close - entry_price) / entry_price) * 100,
                "sma200": float(row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
                "sma200_above": bool(close > row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
            }
            signals.append(signal)
            break  # 첫 번째 시그널만
        
        # 익절 체크
        elif close >= entry_price * (1 + BACKTEST_TAKE_PROFIT_PCT):
            signal = {
                "market": market,
                "timeframe": timeframe,
                "signal_time_kst": candle_time,
                "side": "SELL",
                "reason": "TAKE",
                "entry_price": entry_price,
                "entry_time_kst": entry_time_kst,
                "exit_price": close,
                "pnl_pct": ((close - entry_price) / entry_price) * 100,
                "sma200": float(row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
                "sma200_above": bool(close > row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
            }
            signals.append(signal)
            break  # 첫 번째 시그널만
    
    return signals

