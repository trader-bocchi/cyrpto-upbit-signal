"""CLI 인터페이스"""
import argparse
from typing import List, Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.pipeline.fetch_1h_history_2025 import fetch_1h_candles_2025
from src.pipeline.fetch_1h_incremental import fetch_1h_incremental
from src.pipeline.aggregate_4h_1d import aggregate_timeframes
from src.pipeline.fetch_4h_1d_direct import fetch_4h_1d_direct
from src.signals.signal_engine import detect_buy_signals
from src.signals.sell_engine import check_sell_signals
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.storage.positions_store import add_position, get_all_positions
from src.storage.sent_store import is_signal_sent, mark_signal_sent
from src.telegram.notifier import TelegramNotifier
from src.upbit_client import UpbitClient
from src.config import (
    DERIVED_DATA_PATH, 
    RAW_DATA_PATH, 
    RAW_DATA_PATH_1H,
    BACKTEST_REGIME_ENABLED,
    BACKTEST_REGIME_MODE,
    BACKTEST_REGIME_REDUCE_SIZE_FACTOR,
    BACKTEST_MAX_POSITIONS,
    BACKTEST_MAX_SINGLE_POSITION_WEIGHT,
    BACKTEST_MIN_INVEST_AMOUNT,
)

import pandas as pd  # CLI에서 사용

console = Console()


def cmd_fetch_2025(args):
    """2025년 전체 1시간 캔들 수집"""
    markets = None
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    
    fetch_1h_candles_2025(markets)


def cmd_fetch_daily(args):
    """일일 증분 수집 (1시간봉만 수집)"""
    fetch_1h_incremental()


def cmd_fetch_4h_1d_direct(args):
    """4h/1d 캔들 직접 수집"""
    markets = None
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    
    # 시간프레임 파싱 및 정규화
    timeframes = ["4h", "1d"]
    if args.timeframes:
        parsed = []
        for part in args.timeframes.split(","):
            part = part.strip()
            if not part:
                continue
            
            # 정규화: "1", "24h", "d" -> "1d"
            if part == "1" or part == "24h" or part == "d":
                part = "1d"
            
            # 중복 제거하면서 추가
            if part not in parsed:
                parsed.append(part)
        
        timeframes = parsed if parsed else ["4h", "1d"]
    
    fetch_4h_1d_direct(markets=markets, timeframes=timeframes)


def cmd_aggregate(args):
    """1시간 캔들을 4h/1d로 집계"""
    markets = None
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    
    timeframes = ["4h", "1d"]
    if args.timeframes:
        # 시간프레임 파싱 및 정규화
        timeframes_str = args.timeframes
        parsed = []
        for part in timeframes_str.split(","):
            part = part.strip()
            if part == "1" or part == "24h" or part == "d":
                part = "1d"
            if part and part not in parsed:
                parsed.append(part)
        timeframes = parsed if parsed else ["4h", "1d"]
    
    aggregate_timeframes(markets=markets, timeframes=timeframes)


def cmd_calculate_smi(args):
    """SMI 지표 계산 및 저장"""
    # 시간프레임 파싱 및 정규화
    timeframes_str = args.timeframes if args.timeframes else "4h,1d"
    
    parsed = []
    for part in timeframes_str.split(","):
        part = part.strip()
        if not part:
            continue
        
        # 정규화: "1", "24h", "d" -> "1d"
        if part == "1" or part == "24h" or part == "d":
            part = "1d"
        
        # 중복 제거하면서 추가
        if part not in parsed:
            parsed.append(part)
    
    timeframes = parsed
    
    # 기본값 보장
    if not timeframes:
        timeframes = ["4h", "1d"]
    
    # 마켓 리스트 파싱
    markets = None
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    
    from src.pipeline.calculate_smi import calculate_smi_for_markets
    calculate_smi_for_markets(markets=markets, timeframes=timeframes)


def cmd_run_signals(args):
    """매수/매도 시그널 실행 및 알림 (통합)"""
    # 시간프레임 파싱 (견고하게)
    timeframes_str = args.timeframes if args.timeframes else "4h,1d"
    
    # 시간프레임 파싱 및 정규화
    parsed = []
    for part in timeframes_str.split(","):
        part = part.strip()
        if not part:
            continue
        
        # 정규화: "1", "24h", "d" -> "1d"
        if part == "1" or part == "24h" or part == "d":
            part = "1d"
        
        # 중복 제거하면서 추가
        if part not in parsed:
            parsed.append(part)
    
    timeframes = parsed
    
    # 기본값 보장
    if not timeframes:
        timeframes = ["4h", "1d"]
    
    # 최종 정규화 (24h -> 1d, d -> 1d)
    timeframes = [tf if tf not in ["24h", "d"] else "1d" for tf in timeframes]
    
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
    
    # BTC 1D 데이터 로드 (레짐 필터용)
    btc_1d_df = pd.DataFrame()
    if BACKTEST_REGIME_ENABLED:
        from src.backtest.regime_filter import load_btc_1d_data
        btc_1d_df = load_btc_1d_data()
        if not btc_1d_df.empty:
            console.print(f"[cyan]BTC 1D 데이터 로드 완료: {len(btc_1d_df)}개 캔들[/cyan]")
    
    # 현재 포지션 수 확인 (노출 상한 체크용)
    from src.storage.positions_store import get_all_positions
    current_positions = get_all_positions()
    current_positions_count = len(current_positions)
    
    signal_count = 0
    skipped_regime_off = 0
    skipped_max_positions = 0
    skipped_duplicate = 0
    reduced_size_entries = 0
    
    # 필터링 전 시그널 추적 (상세 정보용)
    raw_signals = []  # SMI로 잡힌 모든 시그널 (필터링 전)
    
    # 모든 시그널을 먼저 수집하고 거래대금 기준 정렬
    console.print("[cyan]시그널 검사 중...[/cyan]")
    all_signals = []
    
    total_tasks = len(markets) * len(timeframes)
    processed_count = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]시그널 검사 중...", total=total_tasks)
        
        for market in markets:
            for timeframe in timeframes:
                # 시간프레임 정규화 (24h -> 1d, d -> 1d)
                timeframe_norm = timeframe
                if timeframe_norm == "24h" or timeframe_norm == "d":
                    timeframe_norm = "1d"
                
                progress.update(task, description=f"[cyan]{market} {timeframe_norm} 검사 중...")
                
                filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe_norm, year=2025)
                df = read_csv_safe(filepath)
                
                if df.empty:
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 시그널 감지 (저장된 SMI 사용, 현재 마지막 SMI만 체크)
                signals = detect_buy_signals(df, market, timeframe_norm, use_saved_smi=True, check_latest_only=True)
                
                for signal in signals:
                    # 필터링 전 시그널 저장 (상세 정보용)
                    ticker_info = ticker_dict.get(market, {})
                    raw_signals.append({
                        "signal": signal,
                        "market": market,
                        "timeframe": timeframe_norm,
                        "ticker_info": ticker_info,
                    })
                    
                    # 중복 체크
                    if is_signal_sent(
                        market=signal["market"],
                        timeframe=signal["timeframe"],
                        signal_time_kst=signal["signal_time_kst"],
                        side="BUY",
                    ):
                        skipped_duplicate += 1
                        continue
                    
                    # [개선 규칙 1] 시장 레짐 필터 체크
                    regime_blocked = False
                    if BACKTEST_REGIME_ENABLED and not btc_1d_df.empty:
                        from src.backtest.regime_filter import get_regime_status
                        from datetime import datetime
                        signal_time = pd.to_datetime(signal["signal_time_kst"])
                        regime_on = get_regime_status(btc_1d_df, signal_time)
                        
                        if not regime_on:
                            # 레짐 OFF: BTC 1D close <= SMA200
                            if BACKTEST_REGIME_MODE == "BLOCK_ENTRY":
                                skipped_regime_off += 1
                                continue
                            elif BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                                reduced_size_entries += 1
                                # 실시간에서는 알림만 보내므로 플래그만 설정
                                regime_blocked = True
                    
                    # [개선 규칙 3-A] 동시 보유 종목 수 상한 체크
                    # 현재 포지션에 없는 마켓만 체크
                    existing_position = any(p["market"] == market for p in current_positions)
                    if not existing_position:
                        if current_positions_count >= BACKTEST_MAX_POSITIONS:
                            skipped_max_positions += 1
                            continue
                    
                    all_signals.append((signal, market, timeframe_norm, ticker_info, regime_blocked))
                
                processed_count += 1
                progress.update(task, advance=1)
    
    console.print(f"[green]시그널 검사 완료: {len(all_signals)}개 시그널 발견[/green]")
    
    # 거래대금 기준 정렬
    all_signals.sort(key=lambda x: x[3].get("acc_trade_price_24h", 0), reverse=True)
    
    # 순차적으로 처리
    if all_signals:
        console.print(f"[cyan]시그널 전송 중... ({len(all_signals)}개)[/cyan]")
    
    for signal, market, timeframe, ticker_info, regime_blocked in all_signals:
        # 알림 전송
        if notifier.send_buy_signal(signal, ticker_info):
            mark_signal_sent(
                market=signal["market"],
                timeframe=signal["timeframe"],
                signal_time_kst=signal["signal_time_kst"],
                side="BUY",
            )
            
            # 포지션 추가 (실시간 트래킹용)
            entry_price = signal["close"]
            # 기존 포지션이 있으면 가중평균 병합 (실시간에서는 간단히 업데이트)
            from src.storage.positions_store import get_position, load_positions, save_positions, get_position_key
            existing_pos = get_position(market, timeframe)
            if existing_pos:
                # 가중평균 계산
                old_qty = existing_pos.get("qty", 0.0)
                old_invested = existing_pos.get("invested_krw", 0.0)
                new_qty = 0.0  # 실제 매수하지 않으므로 0
                new_invested = 0.0
                
                total_qty = old_qty + new_qty
                total_invested = old_invested + new_invested
                avg_entry = total_invested / total_qty if total_qty > 0 else entry_price
                
                # 포지션 업데이트 (가중평균)
                positions = load_positions()
                key = get_position_key(market, timeframe)
                positions[key]["entry_price"] = avg_entry
                positions[key]["qty"] = total_qty
                positions[key]["invested_krw"] = total_invested
                save_positions(positions)
            else:
                # 신규 포지션 추가
                add_position(
                    market=market,
                    timeframe=timeframe,
                    entry_time_kst=signal["signal_time_kst"],
                    entry_price=entry_price,
                    qty=0.0,  # 실제 매수하지 않으므로 0
                    invested_krw=0.0,
                )
                # entry_bar_index 설정 (타임스탑 계산용)
                positions = load_positions()
                key = get_position_key(market, timeframe)
                positions[key]["entry_bar_index"] = 0  # 실시간에서는 0부터 시작
                positions[key]["max_favorable_close_pct"] = 0.0
                positions[key]["max_adverse_close_pct"] = 0.0
                save_positions(positions)
                # 포지션 수 업데이트
                current_positions_count += 1
            
            signal_count += 1
            console.print(f"[green]✅ {market} {timeframe} 매수 시그널 전송[/green]")
    
    if skipped_regime_off > 0:
        console.print(f"[yellow]BTC 1D close <= SMA200 (레짐 OFF)로 인한 스킵: {skipped_regime_off}개[/yellow]")
    if skipped_max_positions > 0:
        console.print(f"[yellow]최대 포지션 초과로 인한 스킵: {skipped_max_positions}개[/yellow]")
    if reduced_size_entries > 0:
        console.print(f"[yellow]BTC 1D close <= SMA200 (레짐 OFF)로 인한 축소 진입: {reduced_size_entries}개[/yellow]")
    
    console.print(f"\n[bold green]총 {signal_count}개 매수 시그널 전송 완료[/bold green]")
    
    # 시그널이 없을 때 텔레그램 메시지 전송
    if signal_count == 0:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 상세 정보 수집
        smi_signal_count = len(raw_signals)
        top_signal = None
        
        if raw_signals:
            # 거래대금 기준으로 정렬하여 TOP 1 찾기
            sorted_raw = sorted(
                raw_signals,
                key=lambda x: x["ticker_info"].get("acc_trade_price_24h", 0),
                reverse=True
            )
            top_signal = sorted_raw[0]
        
        # 필터별 통계
        filter_stats = {
            "레짐 필터 (BTC 1D close <= SMA200)": skipped_regime_off,
            "최대 포지션 수 초과": skipped_max_positions,
            "중복 시그널 (이미 전송됨)": skipped_duplicate,
        }
        
        # 시그널이 없는 사유 파악
        reasons = []
        if skipped_regime_off > 0:
            reasons.append(f"BTC 1D close <= SMA200 (레짐 OFF)로 인한 스킵 {skipped_regime_off}개")
        if skipped_max_positions > 0:
            reasons.append(f"최대 포지션 초과로 인한 스킵 {skipped_max_positions}개")
        if not reasons and smi_signal_count == 0:
            reasons.append("매수 조건을 만족하는 시그널 없음")
        elif not reasons:
            reasons.append("모든 시그널이 필터링됨")
        
        reason_text = ", ".join(reasons)
        
        if notifier.send_no_signal(today, reason_text, smi_signal_count, top_signal, filter_stats):
            console.print(f"[green]✅ 시그널 없음 메시지 전송: {today}, {reason_text}[/green]")


def cmd_run_sell_check(args):
    """매도 시그널 체크 및 알림"""
    # 시간프레임 파싱 및 정규화
    timeframes_str = args.timeframes if args.timeframes else "4h,1d"
    
    parsed = []
    for part in timeframes_str.split(","):
        part = part.strip()
        if not part:
            continue
        
        # 정규화: "1", "24h", "d" -> "1d"
        if part == "1" or part == "24h" or part == "d":
            part = "1d"
        
        # 중복 제거하면서 추가
        if part not in parsed:
            parsed.append(part)
    
    timeframes = parsed
    
    # 기본값 보장
    if not timeframes:
        timeframes = ["4h", "1d"]
    
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
    
    import pandas as pd

    """백테스팅 실행"""
    from src.backtest.engine import run_backtest
    from src.backtest.report import save_backtest_results, generate_monthly_report
    
    # 시간프레임 파싱 및 정규화
    timeframes_str = args.timeframes if args.timeframes else "4h,1d"
    
    parsed = []
    for part in timeframes_str.split(","):
        part = part.strip()
        if not part:
            continue
        
        # 정규화: "1", "24h", "d" -> "1d"
        if part == "1" or part == "24h" or part == "d":
            part = "1d"
        
        # 중복 제거하면서 추가
        if part not in parsed:
            parsed.append(part)
    
    timeframes = parsed
    
    # 기본값 보장
    if not timeframes:
        timeframes = ["1h", "4h", "1d"]
    
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
        # 시간프레임 정규화 및 자동 수정
        timeframe_orig = timeframe
        timeframe_norm = timeframe.strip().lower()
        
        # "1"이 단독으로 오면 "1d"로 자동 수정
        if timeframe_norm == "1":
            timeframe_norm = "1d"
        
        # "24h"도 "1d"로 변환
        if timeframe_norm == "24h":
            timeframe_norm = "1d"
        
        console.print(f"\n[bold cyan]백테스팅 실행: {timeframe_norm}[/bold cyan]")
        
        # 각 timeframe별로 마켓 데이터 로드 (전체 데이터 사용)
        timeframe_market_dfs = {}
        
        # 1h는 월 단위 파일에서 로드
        if timeframe_norm == "1h":
            from src.config import RAW_DATA_PATH_1H
            from src.storage.data_summary import get_markets_from_monthly_files
            from src.pipeline.aggregate_4h_1d import load_1h_data_for_market
            
            # 월 단위 파일에서 마켓 목록 추출
            markets = get_markets_from_monthly_files(RAW_DATA_PATH_1H)
            
            console.print(f"[cyan]  1h 마켓 목록: {len(markets)}개[/cyan]")
            
            for market in markets:
                # 2025년 전체 데이터 로드 (월별 파일 합치기)
                start_dt = pd.to_datetime(start_date) if start_date else None
                end_dt = pd.to_datetime(end_date) if end_date else None
                df = load_1h_data_for_market(RAW_DATA_PATH_1H, market, start_date=start_dt, end_date=end_dt)
                
                if not df.empty:
                    # 날짜 필터링 적용 (이미 load_1h_data_for_market에서 처리되지만 다시 확인)
                    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
                    if start_date:
                        df = df[df["candle_time_kst"] >= start_date]
                    if end_date:
                        df = df[df["candle_time_kst"] <= end_date]
                    if not df.empty:
                        timeframe_market_dfs[market] = df
        else:
            # 4h, 1d는 derived 경로에서 로드
            tf_path = DERIVED_DATA_PATH / f"candles_{timeframe_norm}"
            if tf_path.exists():
                for market_dir in tf_path.iterdir():
                    if market_dir.is_dir() and market_dir.name.startswith("market="):
                        market = market_dir.name.replace("market=", "")
                        filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe_norm, year=2025)
                        df = read_csv_safe(filepath)
                        if not df.empty:
                            # 날짜 필터링 적용
                            df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
                            if start_date:
                                df = df[df["candle_time_kst"] >= start_date]
                            if end_date:
                                df = df[df["candle_time_kst"] <= end_date]
                            if not df.empty:
                                timeframe_market_dfs[market] = df
        
        if not timeframe_market_dfs:
            console.print(f"[yellow]  {timeframe_norm} 데이터가 없습니다. 건너뜁니다.[/yellow]")
            continue
        
        console.print(f"[cyan]  {timeframe_norm} 데이터: {len(timeframe_market_dfs)}개 마켓[/cyan]")
        
        # 데이터 범위 확인
        all_dates = []
        for df in timeframe_market_dfs.values():
            if "candle_time_kst" in df.columns:
                all_dates.extend(df["candle_time_kst"].tolist())
        if all_dates:
            min_date = min(all_dates)
            max_date = max(all_dates)
            console.print(f"[dim]  데이터 범위: {min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}[/dim]")
        
        result = run_backtest(
            market_dfs=timeframe_market_dfs,
            timeframes=[timeframe_norm],
            start_date=start_date,
            end_date=end_date,
            last_days=last_days,
        )
        
        # 결과 저장
        trades_df = pd.DataFrame(result["trades"])
        monthly_df = generate_monthly_report(trades_df, [], start_date=start_date, end_date=end_date, stats=result.get("stats"))
        
        save_backtest_results(result, timeframe_norm, period_label, trades_df, monthly_df)


def cmd_backtest_mixed(args):
    """혼합 전략 백테스팅 실행 (4h + 1d 시그널 동시 활용)"""
    import pandas as pd
    from src.backtest.engine_mixed import run_mixed_backtest
    from src.backtest.report import save_backtest_results, generate_monthly_report
    
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
    
    console.print(f"\n[bold cyan]혼합 전략 백테스팅 실행 (4h + 1d)[/bold cyan]")
    
    result = run_mixed_backtest(
        start_date=start_date,
        end_date=end_date,
        last_days=last_days,
    )
    
    # 결과 저장
    trades_df = pd.DataFrame(result["trades"])
    monthly_df = generate_monthly_report(trades_df, [], start_date=start_date, end_date=end_date, stats=result.get("stats"))
    
    save_backtest_results(result, "mixed", period_label, trades_df, monthly_df)


def main():
    
    parser = argparse.ArgumentParser(description="Upbit Crypto Signal System")
    subparsers = parser.add_subparsers(dest="command", help="명령어")
    
    # fetch-2025
    parser_fetch_2025 = subparsers.add_parser("fetch-2025", help="2025년 전체 1시간 캔들 수집")
    parser_fetch_2025.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분, 예: KRW-BTC,KRW-ETH)")
    
    # fetch-daily
    parser_fetch_daily = subparsers.add_parser("fetch-daily", help="일일 증분 수집 (1시간봉만 수집)")
    parser_fetch_daily.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분, 예: KRW-BTC,KRW-ETH)")
    
    # fetch-4h-1d-direct
    parser_fetch_4h_1d = subparsers.add_parser("fetch-4h-1d-direct", help="4h/1d 캔들 직접 수집 (Upbit API)")
    parser_fetch_4h_1d.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분)")
    parser_fetch_4h_1d.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # aggregate
    parser_aggregate = subparsers.add_parser("aggregate", help="1시간 캔들을 4h/1d로 집계")
    parser_aggregate.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분, 예: KRW-BTC,KRW-ETH)")
    parser_aggregate.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분, 예: 4h,1d)")
    
    # calculate-smi
    parser_calculate_smi = subparsers.add_parser("calculate-smi", help="SMI 지표 계산 및 저장")
    parser_calculate_smi.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분, 예: 4h,1d)")
    parser_calculate_smi.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분, 예: KRW-BTC,KRW-ETH)")
    
    # verify-1d
    parser_verify = subparsers.add_parser("verify-1d", help="1d(24h) 집계 검증")
    parser_verify.add_argument("--markets", type=str, help="마켓 리스트 (쉼표 구분)")
    parser_verify.add_argument("--sample-days", type=int, default=5, help="검증할 샘플 일수")
    
    # data-summary
    parser_summary = subparsers.add_parser("data-summary", help="수집된 데이터 요약 출력")
    
    # run-signals
    parser_signals = subparsers.add_parser("run-signals", help="매수/매도 시그널 실행 및 알림 (통합)")
    parser_signals.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # run-sell-check
    parser_sell = subparsers.add_parser("run-sell-check", help="매도 시그널 체크 및 알림")
    parser_sell.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분)")
    
    # backtest
    parser_backtest = subparsers.add_parser("backtest", help="백테스팅 실행")
    parser_backtest.add_argument("--year", type=int, help="연도 (예: 2025)")
    parser_backtest.add_argument("--last-days", type=int, help="최근 N일")
    parser_backtest.add_argument("--timeframes", type=str, default="1h,4h,1d", help="시간프레임 (쉼표 구분, 예: 1h,4h,1d)")
    parser_backtest.add_argument("--mixed", action="store_true", help="혼합 전략 사용 (4h + 1d 시그널 동시 활용)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "fetch-2025":
        cmd_fetch_2025(args)
    elif args.command == "fetch-daily":
        cmd_fetch_daily(args)
    elif args.command == "fetch-4h-1d-direct":
        cmd_fetch_4h_1d_direct(args)
    elif args.command == "aggregate":
        cmd_aggregate(args)
    elif args.command == "calculate-smi":
        cmd_calculate_smi(args)
    elif args.command == "data-summary":
        from src.storage.data_summary import print_data_summary
        print_data_summary()
    elif args.command == "verify-1d":
        from src.pipeline.verify_1d_aggregation import print_verification_report
        markets = None
        if args.markets:
            markets = [m.strip() for m in args.markets.split(",") if m.strip()]
        print_verification_report(markets=markets, sample_days=args.sample_days)
    elif args.command == "run-signals":
        cmd_run_signals(args)
    elif args.command == "run-sell-check":
        cmd_run_sell_check(args)
    elif args.command == "backtest":
        if args.mixed:
            cmd_backtest_mixed(args)
        else:
            cmd_backtest(args)


if __name__ == "__main__":
    main()

