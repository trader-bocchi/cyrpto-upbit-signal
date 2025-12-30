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
)
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
            }
        else:
            self.positions[key] = {
                "market": market,
                "timeframe": timeframe,
                "entry_time_kst": entry_time_kst,
                "entry_price": entry_price,
                "qty": qty,
                "invested_krw": invested_krw,
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
            # 시간프레임별 파일 로드
            from src.storage.csv_store import get_candle_filepath, read_csv_safe
            from src.config import DERIVED_DATA_PATH
            
            filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
            tf_df = read_csv_safe(filepath)
            
            if tf_df.empty:
                continue
            
            tf_df["candle_time_kst"] = pd.to_datetime(tf_df["candle_time_kst"])
            tf_df["market"] = market
            tf_df["timeframe"] = timeframe
            
            # 날짜 필터링
            if start_date:
                tf_df = tf_df[tf_df["candle_time_kst"] >= start_date]
            if end_date:
                tf_df = tf_df[tf_df["candle_time_kst"] <= end_date]
            
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
        filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
        tf_df = read_csv_safe(filepath)
        
        if tf_df.empty:
            continue
        
        # 날짜 필터링
        if start_date:
            tf_df["candle_time_kst"] = pd.to_datetime(tf_df["candle_time_kst"])
            tf_df = tf_df[tf_df["candle_time_kst"] >= start_date]
        if end_date:
            tf_df["candle_time_kst"] = pd.to_datetime(tf_df["candle_time_kst"])
            tf_df = tf_df[tf_df["candle_time_kst"] <= end_date]
        
        if tf_df.empty:
            continue
        
        signals = detect_buy_signals(tf_df, market, timeframe)
        
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
    
    # 시간순 시뮬레이션
    console.print("[cyan]백테스팅 시뮬레이션 실행 중...[/cyan]")
    
    for bar_info in all_bars:
        timestamp = bar_info["timestamp"]
        market = bar_info["market"]
        timeframe = bar_info["timeframe"]
        row = bar_info["row"]
        
        # 매도 체크 (기존 포지션)
        if f"{market}_{timeframe}" in portfolio.positions:
            sell_signals = check_sell_signals(
                pd.DataFrame([row]),
                market,
                timeframe,
            )
            
            for sell_signal in sell_signals:
                pos = portfolio.positions[f"{market}_{timeframe}"]
                exit_price = sell_signal["exit_price"]
                qty = pos["qty"]
                
                # 매도 처리
                gross = qty * exit_price
                fee_sell = gross * BACKTEST_FEE_RATE
                portfolio.cash_krw += (gross - fee_sell)
                
                # 거래 기록
                trade = {
                    "market": market,
                    "timeframe": timeframe,
                    "side": "SELL",
                    "entry_time_kst": pos["entry_time_kst"],
                    "exit_time_kst": sell_signal["signal_time_kst"],
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "qty": qty,
                    "pnl_pct": sell_signal["pnl_pct"],
                    "reason": sell_signal["reason"],
                }
                portfolio.trades.append(trade)
                portfolio.remove_position(market, timeframe)
        
        # 매수 시그널 처리 (동일 시점)
        time_key = str(timestamp)
        if time_key in buy_signals_by_time:
            signals = buy_signals_by_time[time_key]
            
            # 거래대금 기준 정렬
            sorted_signals = sort_buy_signals_by_volume(signals, ticker_data)
            
            # 순차적으로 매수 처리
            for signal in sorted_signals:
                if signal["market"] != market or signal["timeframe"] != timeframe:
                    continue
                
                # 다음 캔들 시가로 진입 (또는 현재 종가)
                if BACKTEST_ENTRY_USE_OPEN:
                    # 다음 캔들 시가 사용 (간소화: 현재 종가 사용)
                    entry_price = row["close"]
                else:
                    entry_price = signal["close"]
                
                # 매수 금액 계산
                invest_amount = portfolio.cash_krw * BACKTEST_POSITION_SIZE_PCT
                
                if invest_amount < 1000:  # 최소 거래 금액
                    continue
                
                fee_buy = invest_amount * BACKTEST_FEE_RATE
                net_invest = invest_amount - fee_buy
                qty = net_invest / entry_price
                
                if qty <= 0:
                    continue
                
                portfolio.cash_krw -= invest_amount
                portfolio.add_position(
                    market=market,
                    timeframe=timeframe,
                    entry_time_kst=time_key,
                    entry_price=entry_price,
                    qty=qty,
                    invested_krw=net_invest,
                )
                
                # 거래 기록
                trade = {
                    "market": market,
                    "timeframe": timeframe,
                    "side": "BUY",
                    "entry_time_kst": time_key,
                    "entry_price": entry_price,
                    "qty": qty,
                    "invested_krw": net_invest,
                }
                portfolio.trades.append(trade)
    
    # 최종 결과
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
    }

