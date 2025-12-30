"""CLI 인터페이스"""
import argparse
from typing import List, Optional
from rich.console import Console

from src.pipeline.fetch_1h_history_2025 import fetch_1h_candles_2025
from src.pipeline.fetch_1h_incremental import fetch_1h_incremental
from src.pipeline.aggregate_4h_1d import aggregate_timeframes
from src.signals.signal_engine import detect_buy_signals
from src.signals.sell_engine import check_sell_signals
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.storage.positions_store import add_position, get_all_positions
from src.storage.sent_store import is_signal_sent, mark_signal_sent
from src.telegram.notifier import TelegramNotifier
from src.upbit_client import UpbitClient
from src.config import DERIVED_DATA_PATH, RAW_DATA_PATH

console = Console()


def cmd_fetch_2025(args):
    """2025년 전체 1시간 캔들 수집"""
    markets = None
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    
    fetch_1h_candles_2025(markets)


def cmd_fetch_daily(args):
    """일일 증분 수집"""
    fetch_1h_incremental()


def cmd_aggregate(args):
    """4시간/24시간 캔들 집계"""
    timeframes = ["4h", "1d"]
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    
    aggregate_timeframes(markets=None, timeframes=timeframes)


def cmd_run_signals(args):
    """매수 시그널 실행 및 알림"""
    timeframes = ["4h", "1d"]
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    
    notifier = TelegramNotifier()
    client = UpbitClient()
    
    # 모든 마켓 조회
    base_path = DERIVED_DATA_PATH
    markets = []
    
    for timeframe in timeframes:
        tf_path = base_path / f"candles_{timeframe}"
        if tf_path.exists():
            for market_dir in tf_path.iterdir():
                if market_dir.is_dir() and market_dir.name.startswith("market="):
                    market = market_dir.name.replace("market=", "")
                    if market not in markets:
                        markets.append(market)
    
    console.print(f"[green]시그널 검사 대상: {len(markets)}개 마켓, {timeframes} 시간프레임[/green]")
    
    # Ticker 정보 조회 (24시간 거래대금)
    console.print("[cyan]Ticker 정보 조회 중...[/cyan]")
    tickers = client.get_ticker_all_krw()
    ticker_dict = {t["market"]: t for t in tickers}
    
    # 거래대금 기준 순위 계산
    sorted_tickers = sorted(tickers, key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    for idx, ticker in enumerate(sorted_tickers, 1):
        ticker["rank"] = idx
        ticker["top_20"] = idx <= 20
        ticker["top_50"] = idx <= 50
        ticker["total_markets"] = len(sorted_tickers)
    
    signal_count = 0
    
    for market in markets:
        for timeframe in timeframes:
            filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
            df = read_csv_safe(filepath)
            
            if df.empty:
                continue
            
            # 시그널 감지
            signals = detect_buy_signals(df, market, timeframe)
            
            for signal in signals:
                # 중복 체크
                if is_signal_sent(
                    market=signal["market"],
                    timeframe=signal["timeframe"],
                    signal_time_kst=signal["signal_time_kst"],
                    side="BUY",
                ):
                    continue
                
                # 알림 전송
                ticker_info = ticker_dict.get(market, {})
                if notifier.send_buy_signal(signal, ticker_info):
                    mark_signal_sent(
                        market=signal["market"],
                        timeframe=signal["timeframe"],
                        signal_time_kst=signal["signal_time_kst"],
                        side="BUY",
                    )
                    
                    # 포지션 추가 (실시간 트래킹용)
                    # 다음 캔들 시가로 진입 (간소화: 현재 종가 사용)
                    entry_price = signal["close"]
                    # 가상 수량 (실제 매수하지 않음)
                    add_position(
                        market=market,
                        timeframe=timeframe,
                        entry_time_kst=signal["signal_time_kst"],
                        entry_price=entry_price,
                        qty=0.0,  # 실제 매수하지 않으므로 0
                        invested_krw=0.0,
                    )
                    
                    signal_count += 1
                    console.print(f"[green]✅ {market} {timeframe} 매수 시그널 전송[/green]")
    
    console.print(f"\n[bold green]총 {signal_count}개 매수 시그널 전송 완료[/bold green]")


def cmd_run_sell_check(args):
    """매도 시그널 체크 및 알림"""
    timeframes = ["4h", "1d"]
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    
    notifier = TelegramNotifier()
    client = UpbitClient()
    
    # 모든 포지션 조회
    positions = get_all_positions()
    
    if not positions:
        console.print("[yellow]활성 포지션이 없습니다.[/yellow]")
        return
    
    console.print(f"[green]매도 체크 대상: {len(positions)}개 포지션[/green]")
    
    # Ticker 정보 조회
    markets = list(set([p["market"] for p in positions]))
    tickers = client.get_ticker(markets)
    ticker_dict = {t["market"]: t for t in tickers}
    
    # 거래대금 기준 순위 계산
    all_tickers = client.get_ticker_all_krw()
    sorted_tickers = sorted(all_tickers, key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    rank_dict = {}
    for idx, ticker in enumerate(sorted_tickers, 1):
        rank_dict[ticker["market"]] = {
            "rank": idx,
            "top_20": idx <= 20,
            "top_50": idx <= 50,
            "total_markets": len(sorted_tickers),
        }
    
    signal_count = 0
    
    for position in positions:
        market = position["market"]
        timeframe = position["timeframe"]
        
        # 최신 데이터 로드
        filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
        df = read_csv_safe(filepath)
        
        if df.empty:
            continue
        
        # 매도 시그널 체크
        signals = check_sell_signals(df, market, timeframe)
        
        for signal in signals:
            # 중복 체크
            if is_signal_sent(
                market=signal["market"],
                timeframe=signal["timeframe"],
                signal_time_kst=signal["signal_time_kst"],
                side="SELL",
                reason=signal["reason"],
            ):
                continue
            
            # 알림 전송
            ticker_info = ticker_dict.get(market, {})
            rank_info = rank_dict.get(market, {})
            ticker_info.update(rank_info)
            
            if notifier.send_sell_signal(signal, ticker_info):
                mark_signal_sent(
                    market=signal["market"],
                    timeframe=signal["timeframe"],
                    signal_time_kst=signal["signal_time_kst"],
                    side="SELL",
                    reason=signal["reason"],
                )
                
                # 포지션 제거
                from src.storage.positions_store import remove_position
                remove_position(market, timeframe)
                
                signal_count += 1
                console.print(f"[green]🟥 {market} {timeframe} 매도 시그널 전송 ({signal['reason']})[/green]")
    
    console.print(f"\n[bold green]총 {signal_count}개 매도 시그널 전송 완료[/bold green]")


def cmd_backtest(args):
    """백테스팅 실행"""
    from src.backtest.engine import run_backtest
    from src.backtest.report import save_backtest_results, generate_monthly_report
    
    timeframes = ["4h", "1d"]
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    
    # 마켓 데이터 로드
    market_dfs = {}
    base_path = DERIVED_DATA_PATH
    
    for timeframe in timeframes:
        tf_path = base_path / f"candles_{timeframe}"
        if tf_path.exists():
            for market_dir in tf_path.iterdir():
                if market_dir.is_dir() and market_dir.name.startswith("market="):
                    market = market_dir.name.replace("market=", "")
                    if market not in market_dfs:
                        filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
                        df = read_csv_safe(filepath)
                        if not df.empty:
                            market_dfs[market] = df
    
    if not market_dfs:
        console.print("[red]백테스팅할 데이터가 없습니다.[/red]")
        return
    
    console.print(f"[green]백테스팅 대상: {len(market_dfs)}개 마켓[/green]")
    
    # 백테스팅 실행
    start_date = None
    end_date = None
    last_days = None
    period_label = "2025"
    
    if args.year:
        start_date = f"{args.year}-01-01"
        end_date = f"{args.year}-12-31"
        period_label = str(args.year)
    elif args.last_days:
        last_days = args.last_days
        period_label = f"last{last_days}"
    
    for timeframe in timeframes:
        console.print(f"\n[bold cyan]백테스팅 실행: {timeframe}[/bold cyan]")
        
        result = run_backtest(
            market_dfs=market_dfs,
            timeframes=[timeframe],
            start_date=start_date,
            end_date=end_date,
            last_days=last_days,
        )
        
        # 결과 저장
        trades_df = pd.DataFrame(result["trades"])
        monthly_df = generate_monthly_report(trades_df, [])
        
        save_backtest_results(result, timeframe, period_label, trades_df, monthly_df)


def main():
    import pandas as pd  # CLI에서 사용
    
    parser = argparse.ArgumentParser(description="Upbit Crypto Signal System")
    subparsers = parser.add_subparsers(dest="command", help="명령어")
    
    # fetch-2025
    parser_fetch_2025 = subparsers.add_parser("fetch-2025", help="2025년 전체 1시간 캔들 수집")
    parser_fetch_2025.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분, 예: KRW-BTC,KRW-ETH)")
    
    # fetch-daily
    parser_fetch_daily = subparsers.add_parser("fetch-daily", help="일일 증분 수집")
    
    # aggregate
    parser_aggregate = subparsers.add_parser("aggregate", help="4시간/24시간 캔들 집계")
    parser_aggregate.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # run-signals
    parser_signals = subparsers.add_parser("run-signals", help="매수 시그널 실행 및 알림")
    parser_signals.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # run-sell-check
    parser_sell = subparsers.add_parser("run-sell-check", help="매도 시그널 체크 및 알림")
    parser_sell.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # backtest
    parser_backtest = subparsers.add_parser("backtest", help="백테스팅 실행")
    parser_backtest.add_argument("--year", type=int, help="연도 (예: 2025)")
    parser_backtest.add_argument("--last-days", type=int, help="최근 N일")
    parser_backtest.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "fetch-2025":
        cmd_fetch_2025(args)
    elif args.command == "fetch-daily":
        cmd_fetch_daily(args)
    elif args.command == "aggregate":
        cmd_aggregate(args)
    elif args.command == "run-signals":
        cmd_run_signals(args)
    elif args.command == "run-sell-check":
        cmd_run_sell_check(args)
    elif args.command == "backtest":
        cmd_backtest(args)


if __name__ == "__main__":
    main()

