"""백테스팅 엔진"""
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
    BACKTEST_TIME_STOP_BARS_1H,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_MAX_SINGLE_POSITION_WEIGHT,
    BACKTEST_MIN_INVEST_AMOUNT,
    MA_SMA200,
    DERIVED_DATA_PATH,
)
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.indicators.moving_averages import calculate_sma
from src.signals.signal_engine import detect_buy_signals
from src.signals.sell_engine import check_sell_signals
from src.upbit_client import UpbitClient

console = Console()


class Portfolio:
    """포트폴리오 상태"""
    
    def __init__(self, initial_cash: float):
        self.cash_krw = initial_cash
        self.positions: Dict[str, Dict] = {}  # key: market_timeframe, value: position dict
        self.trades: List[Dict] = []
    
    def get_total_value(self, current_prices: Dict[str, float]) -> float:
        """총 자산 계산"""
        total = self.cash_krw
        for key, pos in self.positions.items():
            market = pos["market"]
            if market in current_prices:
                total += pos["qty"] * current_prices[market]
        return total
    
    def add_position(
        self,
        market: str,
        timeframe: str,
        entry_time_kst: str,
        entry_price: float,
        qty: float,
        invested_krw: float,
    ):
        """포지션 추가 (기존 포지션이 있으면 가중평균)"""
        key = f"{market}_{timeframe}"
        
        if key in self.positions:
            # 기존 포지션과 병합 (가중평균)
            old_pos = self.positions[key]
            old_qty = old_pos["qty"]
            old_invested = old_pos["invested_krw"]
            
            total_qty = old_qty + qty
            total_invested = old_invested + invested_krw
            
            # 가중평균 진입가
            avg_entry = total_invested / total_qty if total_qty > 0 else entry_price
            
            self.positions[key] = {
                "market": market,
                "timeframe": timeframe,
                "entry_time_kst": old_pos["entry_time_kst"],  # 첫 진입 시각 유지
                "entry_price": avg_entry,
                "qty": total_qty,
                "invested_krw": total_invested,
                "entry_bar_index": old_pos.get("entry_bar_index"),  # 첫 진입 bar index 유지
                "max_favorable_close_pct": old_pos.get("max_favorable_close_pct", 0.0),
                "max_adverse_close_pct": old_pos.get("max_adverse_close_pct", 0.0),
            }
        else:
            self.positions[key] = {
                "market": market,
                "timeframe": timeframe,
                "entry_time_kst": entry_time_kst,
                "entry_price": entry_price,
                "qty": qty,
                "invested_krw": invested_krw,
                "entry_bar_index": None,  # 타임스탑 계산용 (외부에서 설정)
                "max_favorable_close_pct": 0.0,
                "max_adverse_close_pct": 0.0,
            }
    
    def remove_position(self, market: str, timeframe: str):
        """포지션 제거"""
        key = f"{market}_{timeframe}"
        if key in self.positions:
            del self.positions[key]


def sort_buy_signals_by_volume(signals: List[Dict], ticker_data: Dict[str, Dict]) -> List[Dict]:
    """
    매수 시그널을 거래대금 기준으로 정렬
    
    Args:
        signals: 매수 시그널 리스트
        ticker_data: 마켓별 ticker 정보 (acc_trade_price_24h 포함)
    
    Returns:
        정렬된 시그널 리스트
    """
    def get_sort_key(signal):
        market = signal["market"]
        ticker = ticker_data.get(market, {})
        trade_price_24h = ticker.get("acc_trade_price_24h", 0)
        # 거래대금 내림차순, 동일하면 market 이름 오름차순
        return (-trade_price_24h, market)
    
    return sorted(signals, key=get_sort_key)


def run_backtest(
    market_dfs: Dict[str, pd.DataFrame],
    timeframes: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    last_days: Optional[int] = None,
) -> Dict:
    """
    백테스팅 실행
    
    Args:
        market_dfs: 마켓별 DataFrame 딕셔너리 {market: df}
        timeframes: 시간프레임 리스트
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
    
    # 모든 마켓의 모든 시간프레임 데이터를 시간순으로 정렬
    all_bars = []
    
    for market, df in market_dfs.items():
        for timeframe in timeframes:
            # market_dfs에 이미 필터링된 데이터가 있으므로 그대로 사용
            # (cmd_backtest에서 이미 필터링됨)
            if df.empty:
                continue
            
            # timeframe 컬럼이 없으면 추가
            if "timeframe" not in df.columns:
                df["timeframe"] = timeframe
            
            # market 컬럼이 없으면 추가
            if "market" not in df.columns:
                df["market"] = market
            
            # 날짜 필터링 (이미 필터링되었을 수 있지만 다시 확인)
            tf_df = df.copy()
            if "candle_time_kst" not in tf_df.columns:
                continue
            
            tf_df["candle_time_kst"] = pd.to_datetime(tf_df["candle_time_kst"])
            
            # 날짜 필터링
            if start_date:
                tf_df = tf_df[tf_df["candle_time_kst"] >= start_date]
            if end_date:
                tf_df = tf_df[tf_df["candle_time_kst"] <= end_date]
            
            if tf_df.empty:
                continue
            
            for idx, row in tf_df.iterrows():
                all_bars.append({
                    "timestamp": row["candle_time_kst"],
                    "market": market,
                    "timeframe": timeframe,
                    "row": row,
                })
    
    # 시간순 정렬
    all_bars.sort(key=lambda x: x["timestamp"])
    
    # 시그널 데이터 준비 (사전 계산)
    console.print("[cyan]시그널 사전 계산 중...[/cyan]")
    buy_signals_by_time: Dict[str, List[Dict]] = {}  # timestamp -> signals
    
    # 모든 마켓의 모든 시간프레임에 대해 시그널 계산
    all_markets_set = set()
    for bar_info in all_bars:
        all_markets_set.add((bar_info["market"], bar_info["timeframe"]))
    
    for market, timeframe in all_markets_set:
        # market_dfs에서 데이터 가져오기 (이미 필터링됨)
        if market in market_dfs:
            tf_df = market_dfs[market].copy()
            # timeframe 필터링 (같은 마켓이라도 다른 timeframe일 수 있음)
            if "timeframe" in tf_df.columns:
                tf_df = tf_df[tf_df["timeframe"] == timeframe]
        else:
            # fallback: 파일에서 직접 로드
            filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
            tf_df = read_csv_safe(filepath)
            
            if tf_df.empty:
                continue
            
            tf_df["candle_time_kst"] = pd.to_datetime(tf_df["candle_time_kst"])
            
            # 날짜 필터링
            if start_date:
                tf_df = tf_df[tf_df["candle_time_kst"] >= start_date]
            if end_date:
                tf_df = tf_df[tf_df["candle_time_kst"] <= end_date]
        
        if tf_df.empty:
            continue
        
        # 저장된 SMI 사용하여 시그널 감지
        signals = detect_buy_signals(tf_df, market, timeframe, use_saved_smi=True)
        
        for signal in signals:
            time_key = signal["signal_time_kst"]
            if time_key not in buy_signals_by_time:
                buy_signals_by_time[time_key] = []
            buy_signals_by_time[time_key].append(signal)
    
    # Ticker 데이터 조회 (거래대금 기준 정렬용)
    all_markets = list(set([b["market"] for b in all_bars]))
    ticker_data = {}
    
    # 각 시점별로 ticker 조회 (간소화: 전체 조회 후 재사용)
    console.print("[cyan]Ticker 정보 조회 중...[/cyan]")
    tickers = client.get_ticker_all_krw()
    for ticker in tickers:
        ticker_data[ticker["market"]] = ticker
    
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
        "skipped_regime_off": 0,  # 레짐 OFF로 인한 스킵
        "skipped_max_positions": 0,  # max_positions 초과로 인한 스킵
        "skipped_max_weight": 0,  # max_weight 초과로 인한 스킵
        "reduced_size_entries": 0,  # REDUCE_SIZE 모드로 축소된 진입
        "time_stop_count": 0,  # 타임스탑 청산 횟수
    }
    
    # 시간순 시뮬레이션
    console.print("[cyan]백테스팅 시뮬레이션 실행 중...[/cyan]")
    console.print(f"[dim]총 {len(all_bars)}개 캔들 처리 예정[/dim]")
    
    sell_count = 0
    buy_count = 0
    
    # 타임스탑 계산용: 각 timeframe별로 bar 인덱스 추적
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
        pos_key = f"{market}_{timeframe}"
        if pos_key in portfolio.positions:
            pos = portfolio.positions[pos_key]
            entry_price = pos["entry_price"]
            close = float(row["close"])
            
            # max_favorable_close_pct, max_adverse_close_pct 업데이트
            close_pct = ((close - entry_price) / entry_price) * 100
            if close_pct > pos.get("max_favorable_close_pct", 0.0):
                pos["max_favorable_close_pct"] = close_pct
            if close_pct < pos.get("max_adverse_close_pct", 0.0):
                pos["max_adverse_close_pct"] = close_pct
            
            # 매도 조건 체크 (백테스팅 엔진에서 직접 처리)
            sell_reason = None
            pnl_pct = 0.0
            holding_bars = None
            
            # holding_bars 계산 (해당 timeframe의 bar만 카운트)
            entry_bar_index = pos.get("entry_bar_index")
            if entry_bar_index is not None:
                # 해당 timeframe의 현재 bar 인덱스에서 진입 bar 인덱스를 빼서 holding_bars 계산
                holding_bars = current_tf_bar_index - entry_bar_index
            else:
                holding_bars = None
            
            # 타임스탑 체크 (손절/익절보다 우선하지 않음)
            if BACKTEST_TIME_STOP_ENABLED and entry_bar_index is not None and holding_bars is not None:
                # 타임프레임별 N bars 결정
                if timeframe == "4h":
                    time_stop_bars = BACKTEST_TIME_STOP_BARS_4H
                elif timeframe in ["1d", "24h", "d"]:
                    time_stop_bars = BACKTEST_TIME_STOP_BARS_1D
                elif timeframe == "1h":
                    time_stop_bars = BACKTEST_TIME_STOP_BARS_1H
                else:
                    time_stop_bars = 12  # 기본값
                
                # N bars 경과했고, TAKE가 발생하지 않았으면 타임스탑
                if holding_bars >= time_stop_bars:
                    # TAKE가 발생했는지 확인 (max_favorable_close_pct로 판단)
                    if pos.get("max_favorable_close_pct", 0.0) < BACKTEST_TAKE_PROFIT_PCT * 100:
                        sell_reason = "TIME_STOP"
                        pnl_pct = close_pct
                        stats["time_stop_count"] += 1
            
            # 손절 체크 (타임스탑보다 우선)
            if not sell_reason and close <= entry_price * (1 - BACKTEST_STOP_LOSS_PCT):
                sell_reason = "STOP"
                pnl_pct = ((close - entry_price) / entry_price) * 100
                if holding_bars is None:
                    holding_bars = current_tf_bar_index - entry_bar_index if entry_bar_index is not None else None
            
            # 익절 체크 (타임스탑보다 우선)
            if not sell_reason and close >= entry_price * (1 + BACKTEST_TAKE_PROFIT_PCT):
                sell_reason = "TAKE"
                pnl_pct = ((close - entry_price) / entry_price) * 100
                if holding_bars is None:
                    holding_bars = current_tf_bar_index - entry_bar_index if entry_bar_index is not None else None
            
            # 매도 실행
            if sell_reason:
                exit_price = close
                qty = pos["qty"]
                
                # 매도 처리
                gross = qty * exit_price
                fee_sell = gross * BACKTEST_FEE_RATE
                portfolio.cash_krw += (gross - fee_sell)
                
                # exit_time_kst를 datetime 문자열로 통일
                exit_time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(timestamp, 'strftime') else str(timestamp)
                
                # 거래 기록
                trade = {
                    "market": market,
                    "timeframe": timeframe,
                    "side": "SELL",
                    "entry_time_kst": pos["entry_time_kst"],
                    "exit_time_kst": exit_time_str,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "qty": qty,
                    "pnl_pct": pnl_pct,
                    "reason": sell_reason,
                    "holding_bars": holding_bars,
                    "max_favorable_close_pct": pos.get("max_favorable_close_pct", 0.0),
                    "max_adverse_close_pct": pos.get("max_adverse_close_pct", 0.0),
                }
                portfolio.trades.append(trade)
                portfolio.remove_position(market, timeframe)
                sell_count += 1
                
                # 디버깅 로그 (매도 발생 시)
                console.print(f"[dim]  매도: {market} {timeframe} {sell_reason} ({pnl_pct:+.2f}%)[/dim]")
        
        # 매수 시그널 처리 (동일 시점)
        time_key = str(timestamp)
        if time_key in buy_signals_by_time:
            signals = buy_signals_by_time[time_key]
            
            # 거래대금 기준 정렬
            sorted_signals = sort_buy_signals_by_volume(signals, ticker_data)
            
            # 순차적으로 매수 처리 (포지션이 있더라도 추가 매수 가능)
            for signal in sorted_signals:
                signal_market = signal["market"]
                signal_timeframe = signal["timeframe"]
                
                # 현재 bar의 market/timeframe과 일치하는 시그널만 처리
                if signal_market != market or signal_timeframe != timeframe:
                    continue
                
                # 포지션 체크: 포지션이 있으면 추가 매수 (가중평균 병합)
                pos_key = f"{signal_market}_{signal_timeframe}"
                is_new_position = pos_key not in portfolio.positions
                
                # [개선 규칙 1] 시장 레짐 필터 체크
                if BACKTEST_REGIME_ENABLED and is_new_position and not btc_1d_df.empty:  # 신규 진입만 체크, BTC 데이터가 있어야 함
                    from src.backtest.regime_filter import get_regime_status
                    regime_on = get_regime_status(btc_1d_df, timestamp)
                    
                    if not regime_on:
                        if BACKTEST_REGIME_MODE == "BLOCK_ENTRY":
                            stats["skipped_regime_off"] += 1
                            continue  # 진입 차단
                        elif BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                            # 진입 비중 축소
                            stats["reduced_size_entries"] += 1
                            # invest_amount는 아래에서 축소됨
                
                # [개선 규칙 3-A] 동시 보유 종목 수 상한 체크
                if is_new_position:  # 신규 진입만 체크
                    current_positions_count = len(portfolio.positions)
                    if current_positions_count >= BACKTEST_MAX_POSITIONS:
                        stats["skipped_max_positions"] += 1
                        continue  # 진입 스킵
                
                # 다음 캔들 시가로 진입 (또는 현재 종가)
                if BACKTEST_ENTRY_USE_OPEN:
                    # 다음 캔들 시가 사용 (간소화: 현재 종가 사용)
                    entry_price = row["close"]
                else:
                    entry_price = signal["close"]
                
                # 매수 금액 계산 (기본)
                invest_amount = portfolio.cash_krw * BACKTEST_POSITION_SIZE_PCT
                
                # [개선 규칙 1] 레짐 OFF + REDUCE_SIZE 모드일 때 축소
                if BACKTEST_REGIME_ENABLED and is_new_position and not btc_1d_df.empty:
                    from src.backtest.regime_filter import get_regime_status
                    regime_on = get_regime_status(btc_1d_df, timestamp)
                    if not regime_on and BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                        invest_amount *= BACKTEST_REGIME_REDUCE_SIZE_FACTOR
                
                # [개선 규칙 3-B] 한 종목 최대 비중 상한 체크
                if is_new_position:  # 신규 진입만 체크
                    # 현재 equity 계산
                    current_prices_temp = {}
                    for pos_key_temp, pos_temp in portfolio.positions.items():
                        market_temp = pos_temp["market"]
                        if market_temp not in current_prices_temp:
                            ticker = ticker_data.get(market_temp, {})
                            current_prices_temp[market_temp] = ticker.get("trade_price", entry_price)
                    
                    equity = portfolio.get_total_value(current_prices_temp)
                    
                    # 기존 포지션 가치 (없으면 0)
                    existing_value = 0.0
                    if pos_key in portfolio.positions:
                        existing_pos = portfolio.positions[pos_key]
                        existing_value = existing_pos["qty"] * entry_price
                    
                    # 허용 가능한 최대 가치
                    max_allowed_value = equity * BACKTEST_MAX_SINGLE_POSITION_WEIGHT
                    max_additional_value = max(0, max_allowed_value - existing_value)
                    
                    # invest_amount를 max_additional_value로 제한
                    if invest_amount > max_additional_value:
                        if max_additional_value < BACKTEST_MIN_INVEST_AMOUNT:
                            stats["skipped_max_weight"] += 1
                            continue  # 너무 작으면 스킵
                        invest_amount = max_additional_value
                
                if invest_amount < BACKTEST_MIN_INVEST_AMOUNT:  # 최소 거래 금액
                    continue
                
                fee_buy = invest_amount * BACKTEST_FEE_RATE
                net_invest = invest_amount - fee_buy
                qty = net_invest / entry_price
                
                if qty <= 0:
                    continue
                
                portfolio.cash_krw -= invest_amount
                
                # entry_time_kst를 datetime 문자열로 통일
                entry_time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(timestamp, 'strftime') else str(timestamp)
                
                portfolio.add_position(
                    market=signal_market,
                    timeframe=signal_timeframe,
                    entry_time_kst=entry_time_str,
                    entry_price=entry_price,
                    qty=qty,
                    invested_krw=net_invest,
                )
                
                # entry_bar_index 설정 (타임스탑 계산용 - 해당 timeframe의 현재 bar 인덱스)
                if is_new_position:
                    portfolio.positions[pos_key]["entry_bar_index"] = current_tf_bar_index
                    portfolio.positions[pos_key]["max_favorable_close_pct"] = 0.0
                    portfolio.positions[pos_key]["max_adverse_close_pct"] = 0.0
                
                # 거래 기록
                trade = {
                    "market": signal_market,
                    "timeframe": signal_timeframe,
                    "side": "BUY",
                    "entry_time_kst": entry_time_str,
                    "entry_price": entry_price,
                    "qty": qty,
                    "invested_krw": net_invest,
                }
                portfolio.trades.append(trade)
                buy_count += 1
                
                # 디버깅 로그 (매수 발생 시)
                if is_new_position:
                    console.print(f"[dim]  매수: {signal_market} {signal_timeframe} @ {entry_price:,.0f}원 (신규, bar_idx={current_tf_bar_index})[/dim]")
                else:
                    console.print(f"[dim]  매수: {signal_market} {signal_timeframe} @ {entry_price:,.0f}원 (추가)[/dim]")
    
    # 최종 결과
    console.print(f"[dim]매수: {buy_count}회, 매도: {sell_count}회[/dim]")
    console.print(f"[dim]통계: 레짐 OFF 스킵 {stats['skipped_regime_off']}회, "
                  f"최대 포지션 스킵 {stats['skipped_max_positions']}회, "
                  f"최대 비중 스킵 {stats['skipped_max_weight']}회, "
                  f"축소 진입 {stats['reduced_size_entries']}회, "
                  f"타임스탑 {stats['time_stop_count']}회[/dim]")
    
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

