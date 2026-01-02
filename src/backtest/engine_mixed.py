"""혼합 전략 백테스팅 엔진 (4h + 1d 시그널 동시 활용)"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime
from rich.console import Console

from src.config import (
    BACKTEST_INITIAL_CASH,
    BACKTEST_POSITION_SIZE_PCT,
    BACKTEST_FEE_RATE,
    BACKTEST_STOP_LOSS_PCT,
    BACKTEST_TAKE_PROFIT_PCT,
    BACKTEST_ENTRY_USE_OPEN,
    BACKTEST_REGIME_ENABLED,
    BACKTEST_REGIME_MODE,
    BACKTEST_REGIME_REDUCE_SIZE_FACTOR,
    BACKTEST_TIME_STOP_ENABLED,
    BACKTEST_TIME_STOP_BARS_4H,
    BACKTEST_TIME_STOP_BARS_1D,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_MAX_SINGLE_POSITION_WEIGHT,
    BACKTEST_MIN_INVEST_AMOUNT,
)
from src.signals.signal_engine import detect_buy_signals
from src.upbit_client import UpbitClient
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.config import DERIVED_DATA_PATH

console = Console()


class Portfolio:
    """포트폴리오 상태"""
    
    def __init__(self, initial_cash: float):
        self.cash_krw = initial_cash
        self.positions: Dict[str, Dict] = {}  # key: market, value: position dict (단일 포지션)
        self.trades: List[Dict] = []
    
    def get_total_value(self, current_prices: Dict[str, float]) -> float:
        """총 자산 계산"""
        total = self.cash_krw
        for market, pos in self.positions.items():
            if market in current_prices:
                total += pos["qty"] * current_prices[market]
        return total
    
    def add_position(
        self,
        market: str,
        entry_time_kst: str,
        entry_price: float,
        qty: float,
        invested_krw: float,
        signal_timeframes: List[str],  # 어떤 timeframe 시그널로 진입했는지
    ):
        """포지션 추가 (혼합 전략: 같은 마켓에 하나의 포지션만)"""
        if market in self.positions:
            # 기존 포지션과 병합 (가중평균)
            old_pos = self.positions[market]
            old_qty = old_pos["qty"]
            old_invested = old_pos["invested_krw"]
            
            total_qty = old_qty + qty
            total_invested = old_invested + invested_krw
            
            # 가중평균 진입가
            avg_entry = total_invested / total_qty if total_qty > 0 else entry_price
            
            # 시그널 timeframe 병합
            combined_timeframes = list(set(old_pos.get("signal_timeframes", []) + signal_timeframes))
            
            self.positions[market] = {
                "market": market,
                "entry_time_kst": old_pos["entry_time_kst"],  # 첫 진입 시각 유지
                "entry_price": avg_entry,
                "qty": total_qty,
                "invested_krw": total_invested,
                "signal_timeframes": combined_timeframes,
                "entry_bar_index": old_pos.get("entry_bar_index"),  # 첫 진입 bar index 유지
                "max_favorable_close_pct": old_pos.get("max_favorable_close_pct", 0.0),
                "max_adverse_close_pct": old_pos.get("max_adverse_close_pct", 0.0),
            }
        else:
            self.positions[market] = {
                "market": market,
                "entry_time_kst": entry_time_kst,
                "entry_price": entry_price,
                "qty": qty,
                "invested_krw": invested_krw,
                "signal_timeframes": signal_timeframes,
            }
    
    def remove_position(self, market: str):
        """포지션 제거"""
        if market in self.positions:
            del self.positions[market]


def run_mixed_backtest(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    last_days: Optional[int] = None,
) -> Dict:
    """
    혼합 전략 백테스팅 실행 (4h + 1d 시그널 동시 활용)
    
    전략:
    - 4h 또는 1d 중 하나라도 매수 시그널이 있으면 매수
    - 같은 마켓에 대해 하나의 포지션만 유지
    - 손절/익절 조건 만족 시 매도
    
    Args:
        start_date: 시작일 (KST, YYYY-MM-DD)
        end_date: 종료일 (KST, YYYY-MM-DD)
        last_days: 최근 N일 (start_date/end_date 우선)
    
    Returns:
        백테스팅 결과 딕셔너리
    """
    portfolio = Portfolio(BACKTEST_INITIAL_CASH)
    client = UpbitClient()
    
    # 날짜 필터링
    if last_days:
        end_dt = pd.Timestamp.now(tz="Asia/Seoul")
        start_dt = end_dt - pd.Timedelta(days=last_days)
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")
    
    # 4h와 1d 데이터 로드
    timeframes = ["4h", "1d"]
    all_market_dfs: Dict[str, Dict[str, pd.DataFrame]] = {}  # {market: {timeframe: df}}
    
    for timeframe in timeframes:
        tf_path = DERIVED_DATA_PATH / f"candles_{timeframe}"
        if not tf_path.exists():
            continue
        
        for market_dir in tf_path.iterdir():
            if market_dir.is_dir() and market_dir.name.startswith("market="):
                market = market_dir.name.replace("market=", "")
                filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
                df = read_csv_safe(filepath)
                
                if df.empty:
                    continue
                
                df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
                
                # 날짜 필터링
                if start_date:
                    df = df[df["candle_time_kst"] >= start_date]
                if end_date:
                    df = df[df["candle_time_kst"] <= end_date]
                
                if not df.empty:
                    if market not in all_market_dfs:
                        all_market_dfs[market] = {}
                    all_market_dfs[market][timeframe] = df
    
    if not all_market_dfs:
        console.print("[red]백테스팅할 데이터가 없습니다.[/red]")
        return {
            "initial_cash": BACKTEST_INITIAL_CASH,
            "final_value": BACKTEST_INITIAL_CASH,
            "total_return_pct": 0.0,
            "trades": [],
            "final_positions": [],
        }
    
    # 모든 캔들을 시간순으로 정렬
    all_bars = []
    
    for market, tf_dfs in all_market_dfs.items():
        for timeframe, df in tf_dfs.items():
            for idx, row in df.iterrows():
                all_bars.append({
                    "timestamp": row["candle_time_kst"],
                    "market": market,
                    "timeframe": timeframe,
                    "row": row,
                })
    
    # 시간순 정렬
    all_bars.sort(key=lambda x: x["timestamp"])
    
    # 시그널 사전 계산 (4h와 1d 모두)
    console.print("[cyan]시그널 사전 계산 중... (4h + 1d)[/cyan]")
    buy_signals_by_time: Dict[str, Dict[str, List[Dict]]] = {}  # {timestamp: {market: [signals]}}
    
    for market, tf_dfs in all_market_dfs.items():
        for timeframe, df in tf_dfs.items():
            # 저장된 SMI 사용하여 시그널 감지
            signals = detect_buy_signals(df, market, timeframe, use_saved_smi=True)
            
            for signal in signals:
                time_key = signal["signal_time_kst"]
                if time_key not in buy_signals_by_time:
                    buy_signals_by_time[time_key] = {}
                if market not in buy_signals_by_time[time_key]:
                    buy_signals_by_time[time_key][market] = []
                buy_signals_by_time[time_key][market].append(signal)
    
    # Ticker 데이터 조회
    console.print("[cyan]Ticker 정보 조회 중...[/cyan]")
    tickers = client.get_ticker_all_krw()
    ticker_data = {t["market"]: t for t in tickers}
    
    # 거래대금 기준 정렬 함수
    def sort_signals_by_volume(signals: List[Dict]) -> List[Dict]:
        def get_sort_key(signal):
            market = signal["market"]
            ticker = ticker_data.get(market, {})
            trade_price_24h = ticker.get("acc_trade_price_24h", 0)
            return (-trade_price_24h, market)
        return sorted(signals, key=get_sort_key)
    
    # BTC 1D 데이터 로드 (레짐 필터용)
    btc_1d_df = pd.DataFrame()
    if BACKTEST_REGIME_ENABLED:
        from src.backtest.regime_filter import load_btc_1d_data
        btc_1d_df = load_btc_1d_data(start_date=start_date, end_date=end_date)
        if not btc_1d_df.empty:
            console.print(f"[cyan]BTC 1D 데이터 로드 완료: {len(btc_1d_df)}개 캔들[/cyan]")
        else:
            console.print("[yellow]경고: BTC 1D 데이터를 로드할 수 없습니다. 레짐 필터가 비활성화됩니다.[/yellow]")
    
    # 통계 추적
    stats = {
        "skipped_regime_off": 0,
        "skipped_max_positions": 0,
        "skipped_max_weight": 0,
        "reduced_size_entries": 0,
        "time_stop_count": 0,
    }
    
    # 시간순 시뮬레이션
    console.print("[cyan]백테스팅 시뮬레이션 실행 중... (혼합 전략)[/cyan]")
    console.print(f"[dim]총 {len(all_bars)}개 캔들 처리 예정[/dim]")
    
    sell_count = 0
    buy_count = 0
    
    # 타임스탑 계산용: 각 timeframe별로 bar 인덱스 추적 (혼합 전략은 4h와 1d 모두 추적)
    timeframe_bar_counters: Dict[str, int] = {}  # {timeframe: current_bar_count}
    
    for bar_info in all_bars:
        timestamp = bar_info["timestamp"]
        market = bar_info["market"]
        timeframe = bar_info["timeframe"]
        row = bar_info["row"]
        
        # 해당 timeframe의 bar 카운터 증가
        if timeframe not in timeframe_bar_counters:
            timeframe_bar_counters[timeframe] = 0
        timeframe_bar_counters[timeframe] += 1
        current_tf_bar_index = timeframe_bar_counters[timeframe]
        
        # 매도 체크 (기존 포지션)
        if market in portfolio.positions:
            pos = portfolio.positions[market]
            entry_price = pos["entry_price"]
            close = float(row["close"])
            
            # max_favorable_close_pct, max_adverse_close_pct 업데이트
            close_pct = ((close - entry_price) / entry_price) * 100
            if close_pct > pos.get("max_favorable_close_pct", 0.0):
                pos["max_favorable_close_pct"] = close_pct
            if close_pct < pos.get("max_adverse_close_pct", 0.0):
                pos["max_adverse_close_pct"] = close_pct
            
            # 매도 조건 체크
            sell_reason = None
            pnl_pct = 0.0
            holding_bars = None
            
            # holding_bars 계산 (혼합 전략: 4h와 1d 중 더 짧은 timeframe의 bar 사용)
            entry_bar_index = pos.get("entry_bar_index")
            holding_bars = None
            
            if entry_bar_index is not None:
                # 혼합 전략은 4h와 1d 중 더 짧은 타임스탑을 사용하므로, 더 짧은 timeframe의 bar 인덱스 사용
                # 4h와 1d 중 더 짧은 것은 1d (7 bars)이지만, 실제로는 4h bar 인덱스를 사용
                # 또는 두 timeframe 모두 체크하여 더 짧은 것이 먼저 도달하면 타임스탑
                time_stop_bars_4h = BACKTEST_TIME_STOP_BARS_4H
                time_stop_bars_1d = BACKTEST_TIME_STOP_BARS_1D
                min_time_stop_bars = min(time_stop_bars_4h, time_stop_bars_1d)
                
                # 혼합 전략은 4h와 1d 중 더 짧은 것을 사용하므로, 현재 bar의 timeframe에 따라 결정
                # 하지만 실제로는 두 timeframe 모두 체크해야 함
                # 간단하게: 현재 bar의 timeframe을 사용
                if timeframe == "4h":
                    current_bar_idx = timeframe_bar_counters.get("4h", 0)
                    holding_bars = current_bar_idx - entry_bar_index if entry_bar_index is not None else None
                elif timeframe in ["1d", "24h", "d"]:
                    current_bar_idx = timeframe_bar_counters.get("1d", 0)
                    holding_bars = current_bar_idx - entry_bar_index if entry_bar_index is not None else None
                else:
                    # 기본적으로 4h bar 인덱스 사용
                    current_bar_idx = timeframe_bar_counters.get("4h", 0)
                    holding_bars = current_bar_idx - entry_bar_index if entry_bar_index is not None else None
            
            # 타임스탑 체크 (혼합 전략: 4h와 1d 중 더 짧은 것을 사용)
            if BACKTEST_TIME_STOP_ENABLED and entry_bar_index is not None and holding_bars is not None:
                # 혼합 전략은 4h와 1d 중 더 짧은 타임스탑 사용
                time_stop_bars = min(BACKTEST_TIME_STOP_BARS_4H, BACKTEST_TIME_STOP_BARS_1D)
                
                # N bars 경과했고, TAKE가 발생하지 않았으면 타임스탑
                if holding_bars >= time_stop_bars:
                    if pos.get("max_favorable_close_pct", 0.0) < BACKTEST_TAKE_PROFIT_PCT * 100:
                        sell_reason = "TIME_STOP"
                        pnl_pct = close_pct
                        stats["time_stop_count"] += 1
            
            # 손절 체크 (타임스탑보다 우선)
            if not sell_reason and close <= entry_price * (1 - BACKTEST_STOP_LOSS_PCT):
                sell_reason = "STOP"
                pnl_pct = ((close - entry_price) / entry_price) * 100
                if holding_bars is None and entry_bar_index is not None:
                    if timeframe == "4h":
                        current_bar_idx = timeframe_bar_counters.get("4h", 0)
                        holding_bars = current_bar_idx - entry_bar_index
                    elif timeframe in ["1d", "24h", "d"]:
                        current_bar_idx = timeframe_bar_counters.get("1d", 0)
                        holding_bars = current_bar_idx - entry_bar_index
            
            # 익절 체크 (타임스탑보다 우선)
            if not sell_reason and close >= entry_price * (1 + BACKTEST_TAKE_PROFIT_PCT):
                sell_reason = "TAKE"
                pnl_pct = ((close - entry_price) / entry_price) * 100
                if holding_bars is None and entry_bar_index is not None:
                    if timeframe == "4h":
                        current_bar_idx = timeframe_bar_counters.get("4h", 0)
                        holding_bars = current_bar_idx - entry_bar_index
                    elif timeframe in ["1d", "24h", "d"]:
                        current_bar_idx = timeframe_bar_counters.get("1d", 0)
                        holding_bars = current_bar_idx - entry_bar_index
            
            # 매도 실행
            if sell_reason:
                exit_price = close
                qty = pos["qty"]
                
                # 매도 처리
                gross = qty * exit_price
                fee_sell = gross * BACKTEST_FEE_RATE
                portfolio.cash_krw += (gross - fee_sell)
                
                # 거래 기록
                exit_time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(timestamp, 'strftime') else str(timestamp)
                trade = {
                    "market": market,
                    "timeframe": "mixed",  # 혼합 전략
                    "side": "SELL",
                    "entry_time_kst": pos["entry_time_kst"],
                    "exit_time_kst": exit_time_str,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "qty": qty,
                    "pnl_pct": pnl_pct,
                    "reason": sell_reason,
                    "signal_timeframes": ",".join(pos.get("signal_timeframes", [])),
                    "holding_bars": holding_bars,
                    "max_favorable_close_pct": pos.get("max_favorable_close_pct", 0.0),
                    "max_adverse_close_pct": pos.get("max_adverse_close_pct", 0.0),
                }
                portfolio.trades.append(trade)
                portfolio.remove_position(market)
                sell_count += 1
                console.print(f"[dim]  매도: {market} {sell_reason} ({pnl_pct:+.2f}%)[/dim]")
        
        # 매수 시그널 처리 (4h 또는 1d 중 하나라도 있으면, 포지션이 있어도 추가 매수 가능)
        time_key = str(timestamp)
        if time_key in buy_signals_by_time and market in buy_signals_by_time[time_key]:
            # 해당 마켓의 모든 timeframe 시그널 수집
            signals = buy_signals_by_time[time_key][market]
            
            # 포지션 체크: 포지션이 있으면 추가 매수 (가중평균 병합)
            is_new_position = market not in portfolio.positions
            
            # [개선 규칙 1] 시장 레짐 필터 체크
            if BACKTEST_REGIME_ENABLED and is_new_position:
                from src.backtest.regime_filter import get_regime_status
                regime_on = get_regime_status(btc_1d_df, timestamp)
                
                if not regime_on:
                    if BACKTEST_REGIME_MODE == "BLOCK_ENTRY":
                        stats["skipped_regime_off"] += 1
                        continue
                    elif BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                        stats["reduced_size_entries"] += 1
            
            # [개선 규칙 3-A] 동시 보유 종목 수 상한 체크
            if is_new_position:
                current_positions_count = len(portfolio.positions)
                if current_positions_count >= BACKTEST_MAX_POSITIONS:
                    stats["skipped_max_positions"] += 1
                    continue
            
            # 거래대금 기준 정렬
            sorted_signals = sort_signals_by_volume(signals)
            
            # 첫 번째 시그널로 매수 (혼합 전략: 하나의 시그널만으로도 매수)
            signal = sorted_signals[0]
            
            # 진입가 결정
            if BACKTEST_ENTRY_USE_OPEN:
                entry_price = row["close"]
            else:
                entry_price = signal["close"]
            
            # 매수 금액 계산 (기본)
            invest_amount = portfolio.cash_krw * BACKTEST_POSITION_SIZE_PCT
            
            # [개선 규칙 1] 레짐 OFF + REDUCE_SIZE 모드일 때 축소
            if BACKTEST_REGIME_ENABLED and is_new_position:
                from src.backtest.regime_filter import get_regime_status
                regime_on = get_regime_status(btc_1d_df, timestamp)
                if not regime_on and BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                    invest_amount *= BACKTEST_REGIME_REDUCE_SIZE_FACTOR
            
            # [개선 규칙 3-B] 한 종목 최대 비중 상한 체크
            if is_new_position:
                # 현재 equity 계산
                current_prices_temp = {}
                for market_temp, pos_temp in portfolio.positions.items():
                    if market_temp not in current_prices_temp:
                        ticker = ticker_data.get(market_temp, {})
                        current_prices_temp[market_temp] = ticker.get("trade_price", entry_price)
                
                equity = portfolio.get_total_value(current_prices_temp)
                
                # 기존 포지션 가치 (없으면 0)
                existing_value = 0.0
                if market in portfolio.positions:
                    existing_pos = portfolio.positions[market]
                    existing_value = existing_pos["qty"] * entry_price
                
                # 허용 가능한 최대 가치
                max_allowed_value = equity * BACKTEST_MAX_SINGLE_POSITION_WEIGHT
                max_additional_value = max(0, max_allowed_value - existing_value)
                
                # invest_amount를 max_additional_value로 제한
                if invest_amount > max_additional_value:
                    if max_additional_value < BACKTEST_MIN_INVEST_AMOUNT:
                        stats["skipped_max_weight"] += 1
                        continue
                    invest_amount = max_additional_value
            
            if invest_amount < BACKTEST_MIN_INVEST_AMOUNT:
                continue
            
            fee_buy = invest_amount * BACKTEST_FEE_RATE
            net_invest = invest_amount - fee_buy
            qty = net_invest / entry_price
            
            if qty <= 0:
                continue
            
            portfolio.cash_krw -= invest_amount
            
            # entry_time_kst를 datetime 문자열로 통일
            entry_time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(timestamp, 'strftime') else str(timestamp)
            
            # 모든 시그널의 timeframe 수집
            signal_timeframes = [s["timeframe"] for s in sorted_signals]
            
            portfolio.add_position(
                market=market,
                entry_time_kst=entry_time_str,
                entry_price=entry_price,
                qty=qty,
                invested_krw=net_invest,
                signal_timeframes=signal_timeframes,
            )
            
            # entry_bar_index 설정 (타임스탑 계산용 - 혼합 전략은 4h bar 인덱스 사용)
            if is_new_position:
                # 혼합 전략은 4h와 1d 중 더 짧은 타임스탑을 사용하므로, 4h bar 인덱스를 사용
                entry_bar_idx = timeframe_bar_counters.get("4h", 0)
                portfolio.positions[market]["entry_bar_index"] = entry_bar_idx
                portfolio.positions[market]["max_favorable_close_pct"] = 0.0
                portfolio.positions[market]["max_adverse_close_pct"] = 0.0
            
            # 거래 기록
            trade = {
                "market": market,
                "timeframe": "mixed",
                "side": "BUY",
                "entry_time_kst": entry_time_str,
                "entry_price": entry_price,
                "qty": qty,
                "invested_krw": net_invest,
                "signal_timeframes": ",".join(signal_timeframes),
            }
            portfolio.trades.append(trade)
            buy_count += 1
            
            # 디버깅 로그 (매수 발생 시)
            if is_new_position:
                console.print(f"[dim]  매수: {market} mixed @ {entry_price:,.0f}원 (신규, {','.join(signal_timeframes)})[/dim]")
            else:
                console.print(f"[dim]  매수: {market} mixed @ {entry_price:,.0f}원 (추가, {','.join(signal_timeframes)})[/dim]")
    
    # 최종 결과
    console.print(f"[dim]매수: {buy_count}회, 매도: {sell_count}회[/dim]")
    console.print(f"[dim]통계: 레짐 OFF 스킵 {stats['skipped_regime_off']}회, "
                  f"최대 포지션 스킵 {stats['skipped_max_positions']}회, "
                  f"최대 비중 스킵 {stats['skipped_max_weight']}회, "
                  f"축소 진입 {stats['reduced_size_entries']}회, "
                  f"타임스탑 {stats['time_stop_count']}회[/dim]")
    
    all_markets = list(set([b["market"] for b in all_bars]))
    current_prices = {}
    for market in all_markets:
        ticker = ticker_data.get(market, {})
        current_prices[market] = ticker.get("trade_price", 0)
    
    final_value = portfolio.get_total_value(current_prices)
    total_return = ((final_value - BACKTEST_INITIAL_CASH) / BACKTEST_INITIAL_CASH) * 100
    
    return {
        "initial_cash": BACKTEST_INITIAL_CASH,
        "final_value": final_value,
        "total_return_pct": total_return,
        "trades": portfolio.trades,
        "final_positions": list(portfolio.positions.values()),
        "stats": stats,  # 통계 추가
    }

