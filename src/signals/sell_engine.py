"""매도 시그널 엔진 (손절/익절)"""
import pandas as pd
from typing import List, Dict, Optional
from rich.console import Console

from src.config import (
    BACKTEST_STOP_LOSS_PCT,
    BACKTEST_TAKE_PROFIT_PCT,
    BACKTEST_TIME_STOP_ENABLED,
    BACKTEST_TIME_STOP_MODE,
    BACKTEST_TIME_STOP_BARS_4H,
    BACKTEST_TIME_STOP_BARS_1D,
    BACKTEST_TIME_STOP_BARS_1H,
)
from src.storage.positions_store import get_all_positions, remove_position
from src.indicators.moving_averages import calculate_sma
from src.indicators.squeeze_momentum import calculate_smi
from src.signals.exit_rules import adaptive_bounds, momentum_time_stop_hit

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
    entry_time_dt = pd.to_datetime(entry_time_kst)
    
    # 시간순 정렬 후 지표 계산 (동적 타임스탑은 SMI 모멘텀 필요)
    df = df.sort_values("candle_time_kst").reset_index(drop=True)
    df = calculate_sma(df, periods=[200])
    df = calculate_smi(df)
    smi_list = df["smi_momentum"].tolist()

    # entry_bar_index와 max_favorable_close_pct 추적 (타임스탑용)
    entry_bar_index = position.get("entry_bar_index")
    max_favorable_close_pct = position.get("max_favorable_close_pct", 0.0)
    
    signals = []
    
    # 타임스탑 계산용: 해당 timeframe의 bar 인덱스 추적
    timeframe_bar_index = 0
    
    for idx, row in df.iterrows():
        candle_time = str(row["candle_time_kst"])
        candle_time_dt = pd.to_datetime(candle_time)
        close = float(row["close"])
        
        # entry_time 이후의 bar만 체크
        if candle_time_dt <= entry_time_dt:
            continue
        
        # 해당 timeframe의 bar 인덱스 증가
        timeframe_bar_index += 1
        
        # max_favorable_close_pct 업데이트
        close_pct = ((close - entry_price) / entry_price) * 100
        if close_pct > max_favorable_close_pct:
            max_favorable_close_pct = close_pct
        
        sell_reason = None
        pnl_pct = ((close - entry_price) / entry_price) * 100
        
        # [개선 규칙 2] 타임스탑 체크 (손절/익절보다 우선하지 않음)
        if BACKTEST_TIME_STOP_ENABLED and BACKTEST_TIME_STOP_MODE == "adaptive":
            # 동적A: SMI 2봉 연속 하락 시 청산 (최소~최대 봉 사이). timeframe_bar_index = 보유 봉 수
            min_bars, max_bars = adaptive_bounds(timeframe)
            if momentum_time_stop_hit(smi_list, idx, timeframe_bar_index, min_bars, max_bars):
                sell_reason = "TIME_STOP"
        elif BACKTEST_TIME_STOP_ENABLED and entry_bar_index is not None:
            # fixed: 고정 N bars
            if timeframe == "4h":
                time_stop_bars = BACKTEST_TIME_STOP_BARS_4H
            elif timeframe in ["1d", "24h", "d"]:
                time_stop_bars = BACKTEST_TIME_STOP_BARS_1D
            elif timeframe == "1h":
                time_stop_bars = BACKTEST_TIME_STOP_BARS_1H
            else:
                time_stop_bars = 12  # 기본값

            # N bars 경과했고, TAKE가 발생하지 않았으면 타임스탑
            holding_bars = timeframe_bar_index - entry_bar_index
            if holding_bars >= time_stop_bars:
                if max_favorable_close_pct < BACKTEST_TAKE_PROFIT_PCT * 100:
                    sell_reason = "TIME_STOP"
        
        # max_favorable_close_pct를 포지션에 저장 (다음 체크를 위해)
        from src.storage.positions_store import get_position_key, load_positions, save_positions
        positions = load_positions()
        pos_key = get_position_key(market, timeframe)
        if pos_key in positions:
            positions[pos_key]["max_favorable_close_pct"] = max_favorable_close_pct
            positions[pos_key]["max_adverse_close_pct"] = min(
                positions[pos_key].get("max_adverse_close_pct", 0.0),
                close_pct
            )
            save_positions(positions)
        
        # 손절 체크 (타임스탑보다 우선)
        if not sell_reason and close <= entry_price * (1 - BACKTEST_STOP_LOSS_PCT):
            sell_reason = "STOP"
        
        # 익절 체크 (타임스탑보다 우선)
        if not sell_reason and close >= entry_price * (1 + BACKTEST_TAKE_PROFIT_PCT):
            sell_reason = "TAKE"
        
        # 매도 시그널 생성
        if sell_reason:
            signal = {
                "market": market,
                "timeframe": timeframe,
                "signal_time_kst": candle_time,
                "side": "SELL",
                "reason": sell_reason,
                "entry_price": entry_price,
                "entry_time_kst": entry_time_kst,
                "exit_price": close,
                "pnl_pct": pnl_pct,
                "sma200": float(row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
                "sma200_above": bool(close > row["sma_200"]) if not pd.isna(row["sma_200"]) else None,
            }
            signals.append(signal)
            break  # 첫 번째 시그널만
    
    return signals

