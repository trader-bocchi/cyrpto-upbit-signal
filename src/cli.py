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
    
    # 전체 거래대금 계산 (업비트 전체 시장 규모)
    total_market_volume = sum(t.get("acc_trade_price_24h", 0) for t in tickers)
    
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
    skipped_duplicate = 0
    reduced_size_entries = 0
    
    # 필터링 전 시그널 추적 (상세 정보용)
    raw_signals = []  # SMI로 잡힌 모든 시그널 (필터링 전)
    filtered_signals = []  # 필터링된 시그널 정보 (종목, 사유)
    
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
                
                # 캔들 시간을 datetime으로 변환
                df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
                
                ticker_info = ticker_dict.get(market, {})
                
                # verify_smi_signals.py와 동일한 로직으로 시그널 조건 체크
                from src.indicators.extrema import find_pivot_min
                from src.indicators.moving_averages import calculate_sma
                from src.storage.smi_store import load_smi, merge_smi_with_candles
                from src.config import SMI_LOCAL_MIN_WINDOW, SMI_REQUIRE_NEGATIVE_PIVOT, SIGNAL_ENABLE_SMA50_FILTER
                
                # 1. 저장된 SMI 데이터 로드 (verify_smi_signals.py와 동일 - 완성된 캔들 체크 전에 먼저 확인)
                smi_df = load_smi(market, timeframe_norm, year=None)
                
                if smi_df.empty:
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 2. 마지막 SMI 값 확인 (verify_smi_signals.py와 동일 - 완성된 캔들 체크 전에 먼저 확인)
                smi_df_sorted = smi_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                last_smi = smi_df_sorted.iloc[-1]["smi_momentum"]
                
                if pd.isna(last_smi):
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 마지막 SMI 값이 양수/0이면 시그널 없음 (verify_smi_signals.py와 동일)
                if last_smi >= 0:
                    filtered_signals.append({
                        "market": market,
                        "timeframe": timeframe_norm,
                        "reason": "SMI 양수/0 (회복 패턴 아님)",
                        "ticker_info": ticker_info,
                    })
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # verify_smi_signals.py와 동일하게 모든 캔들로 시그널 조건 체크 (완성된 캔들 체크 없음)
                # 3. 캔들 데이터와 SMI 병합 (verify_smi_signals.py와 동일)
                df_sorted = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                smi_df_sorted["candle_time_kst"] = pd.to_datetime(smi_df_sorted["candle_time_kst"])
                merged_df = df_sorted.merge(
                    smi_df_sorted[["candle_time_kst", "smi_momentum"]],
                    on="candle_time_kst",
                    how="left"
                )
                
                if merged_df.empty or len(merged_df) < SMI_LOCAL_MIN_WINDOW + 3:
                    filtered_signals.append({
                        "market": market,
                        "timeframe": timeframe_norm,
                        "reason": "데이터 부족",
                        "ticker_info": ticker_info,
                    })
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 4. 이동평균 계산 (verify_smi_signals.py와 동일)
                merged_df = calculate_sma(merged_df, periods=[50, 200])
                
                # 5. 피벗 찾기 (verify_smi_signals.py와 동일)
                pivot_info = find_pivot_min(
                    merged_df["smi_momentum"],
                    window=SMI_LOCAL_MIN_WINDOW,
                    require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
                )
                merged_df = pd.concat([merged_df, pivot_info], axis=1)
                
                # 6. 마지막 행 체크 (verify_smi_signals.py와 동일)
                i = len(merged_df) - 1
                m_i2 = merged_df.iloc[i]["smi_momentum"]
                
                if pd.isna(m_i2):
                    filtered_signals.append({
                        "market": market,
                        "timeframe": timeframe_norm,
                        "reason": "SMI 값 없음",
                        "ticker_info": ticker_info,
                    })
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 7. 피벗 정보 확인 (verify_smi_signals.py와 동일)
                pivot_info_row = merged_df.iloc[i]
                pivot_idx_loc = int(pivot_info_row["pivot_idx"])
                
                filter_reason = None
                signal_eligible = False
                
                if pivot_idx_loc < 0:
                    filter_reason = "SMI 최저점 없음"
                elif pivot_idx_loc != i - 2:
                    filter_reason = "SMI 회복 패턴 불일치 (최저점이 2칸 전이 아님)"
                else:
                    m_i = merged_df.iloc[pivot_idx_loc]["smi_momentum"]
                    m_i1 = merged_df.iloc[pivot_idx_loc + 1]["smi_momentum"]
                    
                    if pd.isna(m_i) or pd.isna(m_i1):
                        filter_reason = "SMI 값 없음"
                    elif not (m_i2 > m_i1 > m_i):
                        filter_reason = "SMI 상승 패턴 미충족 (연속 상승 아님)"
                    elif SMI_REQUIRE_NEGATIVE_PIVOT and m_i >= 0:
                        filter_reason = "SMI 최저점이 음수가 아님"
                    elif SIGNAL_ENABLE_SMA50_FILTER:
                        signal_row = merged_df.iloc[i]
                        if pd.isna(signal_row["sma_50"]) or signal_row["close"] <= signal_row["sma_50"]:
                            filter_reason = "SMA50 필터 (현재가 <= SMA50)"
                        else:
                            # 모든 조건 만족
                            signal_eligible = True
                    else:
                        # SMA50 필터가 비활성화된 경우
                        signal_eligible = True
                
                # 8. 시그널 조건 만족 여부에 따라 처리
                if not signal_eligible:
                    if filter_reason:
                        filtered_signals.append({
                            "market": market,
                            "timeframe": timeframe_norm,
                            "reason": filter_reason,
                            "ticker_info": ticker_info,
                        })
                        # raw_signals에도 추가
                        raw_signals.append({
                            "signal": None,
                            "market": market,
                            "timeframe": timeframe_norm,
                            "ticker_info": ticker_info,
                        })
                    processed_count += 1
                    progress.update(task, advance=1)
                    continue
                
                # 9. 시그널 조건 만족 - 시그널 생성 (verify_smi_signals.py와 동일 - 완성된 캔들 체크 제거)
                signal_row = merged_df.iloc[i]
                signal_time_kst = signal_row["candle_time_kst"]
                
                # 시그널 생성
                signal = {
                    "market": market,
                    "timeframe": timeframe_norm,
                    "signal_time_kst": str(signal_time_kst),
                    "side": "BUY",
                    "close": float(signal_row["close"]),
                    "smi_pivot_min": float(m_i),
                    "smi_m_i": float(m_i),
                    "smi_m_i1": float(m_i1),
                    "smi_m_i2": float(m_i2),
                    "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
                    "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
                    "sma200_above": bool(signal_row["close"] > signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
                }
                
                signals = [signal]
                
                # 시그널 처리 (중복, 레짐 필터 등)
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
                        filtered_signals.append({
                            "market": market,
                            "timeframe": timeframe_norm,
                            "reason": "중복 시그널",
                            "ticker_info": ticker_info,
                        })
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
                                filtered_signals.append({
                                    "market": market,
                                    "timeframe": timeframe_norm,
                                    "reason": "레짐 필터 (BTC 하락장)",
                                    "ticker_info": ticker_info,
                                })
                                continue
                            elif BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                                reduced_size_entries += 1
                                # 실시간에서는 알림만 보내므로 플래그만 설정
                                regime_blocked = True
                    
                    # 4h 시그널인 경우, 해당 종목의 1d 시그널도 체크
                    # 1d 시그널인 경우, 해당 종목의 4h 시그널도 체크
                    has_1d_signal = False
                    has_4h_signal = False
                    
                    if timeframe_norm == "4h":
                        # 1d 시그널 체크 (verify_smi_signals.py와 동일한 로직)
                        smi_df_1d = load_smi(market, "1d", year=None)
                        if not smi_df_1d.empty:
                            smi_df_1d_sorted = smi_df_1d.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                            last_smi_1d = smi_df_1d_sorted.iloc[-1]["smi_momentum"]
                            
                            if not pd.isna(last_smi_1d) and last_smi_1d < 0:
                                # 1d 캔들 로드
                                filepath_1d = get_candle_filepath(DERIVED_DATA_PATH, market, "1d", year=2025)
                                df_1d = read_csv_safe(filepath_1d)
                                
                                if not df_1d.empty:
                                    df_1d["candle_time_kst"] = pd.to_datetime(df_1d["candle_time_kst"])
                                    df_1d_sorted = df_1d.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                                    
                                    # SMI 병합
                                    smi_df_1d_sorted["candle_time_kst"] = pd.to_datetime(smi_df_1d_sorted["candle_time_kst"])
                                    merged_df_1d = df_1d_sorted.merge(
                                        smi_df_1d_sorted[["candle_time_kst", "smi_momentum"]],
                                        on="candle_time_kst",
                                        how="left"
                                    )
                                    
                                    if not merged_df_1d.empty and len(merged_df_1d) >= SMI_LOCAL_MIN_WINDOW + 3:
                                        merged_df_1d = calculate_sma(merged_df_1d, periods=[50, 200])
                                        pivot_info_1d = find_pivot_min(
                                            merged_df_1d["smi_momentum"],
                                            window=SMI_LOCAL_MIN_WINDOW,
                                            require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
                                        )
                                        merged_df_1d = pd.concat([merged_df_1d, pivot_info_1d], axis=1)
                                        
                                        i_1d = len(merged_df_1d) - 1
                                        m_i2_1d = merged_df_1d.iloc[i_1d]["smi_momentum"]
                                        
                                        if not pd.isna(m_i2_1d):
                                            pivot_info_row_1d = merged_df_1d.iloc[i_1d]
                                            pivot_idx_loc_1d = int(pivot_info_row_1d["pivot_idx"])
                                            
                                            if pivot_idx_loc_1d >= 0 and pivot_idx_loc_1d == i_1d - 2:
                                                m_i_1d = merged_df_1d.iloc[pivot_idx_loc_1d]["smi_momentum"]
                                                m_i1_1d = merged_df_1d.iloc[pivot_idx_loc_1d + 1]["smi_momentum"]
                                                
                                                if (not pd.isna(m_i_1d) and not pd.isna(m_i1_1d) and
                                                    m_i2_1d > m_i1_1d > m_i_1d and
                                                    (not SMI_REQUIRE_NEGATIVE_PIVOT or m_i_1d < 0)):
                                                    signal_row_1d = merged_df_1d.iloc[i_1d]
                                                    if (not SIGNAL_ENABLE_SMA50_FILTER or
                                                        (not pd.isna(signal_row_1d["sma_50"]) and signal_row_1d["close"] > signal_row_1d["sma_50"])):
                                                        has_1d_signal = True
                    
                    elif timeframe_norm == "1d":
                        # 4h 시그널 체크 (verify_smi_signals.py와 동일한 로직)
                        smi_df_4h = load_smi(market, "4h", year=None)
                        if not smi_df_4h.empty:
                            smi_df_4h_sorted = smi_df_4h.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                            last_smi_4h = smi_df_4h_sorted.iloc[-1]["smi_momentum"]
                            
                            if not pd.isna(last_smi_4h) and last_smi_4h < 0:
                                # 4h 캔들 로드
                                filepath_4h = get_candle_filepath(DERIVED_DATA_PATH, market, "4h", year=2025)
                                df_4h = read_csv_safe(filepath_4h)
                                
                                if not df_4h.empty:
                                    df_4h["candle_time_kst"] = pd.to_datetime(df_4h["candle_time_kst"])
                                    df_4h_sorted = df_4h.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                                    
                                    # SMI 병합
                                    smi_df_4h_sorted["candle_time_kst"] = pd.to_datetime(smi_df_4h_sorted["candle_time_kst"])
                                    merged_df_4h = df_4h_sorted.merge(
                                        smi_df_4h_sorted[["candle_time_kst", "smi_momentum"]],
                                        on="candle_time_kst",
                                        how="left"
                                    )
                                    
                                    if not merged_df_4h.empty and len(merged_df_4h) >= SMI_LOCAL_MIN_WINDOW + 3:
                                        merged_df_4h = calculate_sma(merged_df_4h, periods=[50, 200])
                                        pivot_info_4h = find_pivot_min(
                                            merged_df_4h["smi_momentum"],
                                            window=SMI_LOCAL_MIN_WINDOW,
                                            require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
                                        )
                                        merged_df_4h = pd.concat([merged_df_4h, pivot_info_4h], axis=1)
                                        
                                        i_4h = len(merged_df_4h) - 1
                                        m_i2_4h = merged_df_4h.iloc[i_4h]["smi_momentum"]
                                        
                                        if not pd.isna(m_i2_4h):
                                            pivot_info_row_4h = merged_df_4h.iloc[i_4h]
                                            pivot_idx_loc_4h = int(pivot_info_row_4h["pivot_idx"])
                                            
                                            if pivot_idx_loc_4h >= 0 and pivot_idx_loc_4h == i_4h - 2:
                                                m_i_4h = merged_df_4h.iloc[pivot_idx_loc_4h]["smi_momentum"]
                                                m_i1_4h = merged_df_4h.iloc[pivot_idx_loc_4h + 1]["smi_momentum"]
                                                
                                                if (not pd.isna(m_i_4h) and not pd.isna(m_i1_4h) and
                                                    m_i2_4h > m_i1_4h > m_i_4h and
                                                    (not SMI_REQUIRE_NEGATIVE_PIVOT or m_i_4h < 0)):
                                                    signal_row_4h = merged_df_4h.iloc[i_4h]
                                                    if (not SIGNAL_ENABLE_SMA50_FILTER or
                                                        (not pd.isna(signal_row_4h["sma_50"]) and signal_row_4h["close"] > signal_row_4h["sma_50"])):
                                                        has_4h_signal = True
                    
                    all_signals.append((signal, market, timeframe_norm, ticker_info, regime_blocked, has_1d_signal, has_4h_signal))
                
                processed_count += 1
                progress.update(task, advance=1)
    
    console.print(f"[green]시그널 검사 완료: {len(all_signals)}개 시그널 발견[/green]")
    
    # 거래대금 기준 정렬
    all_signals.sort(key=lambda x: x[3].get("acc_trade_price_24h", 0), reverse=True)
    
    # 요청4: 거래대금 기준 상위 5개만 전송
    signals_to_send = all_signals[:5] if len(all_signals) > 5 else all_signals
    
    # 요청1: 시그널이 여러 개일 경우 하나로 묶어서 전송
    if signals_to_send:
        if len(all_signals) > 5:
            console.print(f"[cyan]시그널 전송 중... (전체 {len(all_signals)}개 중 상위 5개만 전송)[/cyan]")
        else:
            console.print(f"[cyan]시그널 전송 중... ({len(signals_to_send)}개)[/cyan]")
        
        # 현재 시점 (KST)
        from datetime import datetime
        current_time = datetime.now().strftime("%Y년 %m월 %d일 %H시 %M분")
        
        # 모든 시그널을 하나의 메시지로 묶어서 전송
        if notifier.send_buy_signals_batch(signals_to_send, current_time, total_market_volume):
            # 각 시그널을 마킹
            for signal, market, timeframe, ticker_info, regime_blocked, has_1d_signal, has_4h_signal in signals_to_send:
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
            
            console.print(f"[green]✅ {len(signals_to_send)}개 매수 시그널 일괄 전송 완료[/green]")
            if len(all_signals) > 5:
                console.print(f"[yellow]  (전체 {len(all_signals)}개 중 상위 5개만 전송됨)[/yellow]")
    
    if skipped_regime_off > 0:
        console.print(f"[yellow]BTC 1D close <= SMA200 (레짐 OFF)로 인한 스킵: {skipped_regime_off}개[/yellow]")
    if reduced_size_entries > 0:
        console.print(f"[yellow]BTC 1D close <= SMA200 (레짐 OFF)로 인한 축소 진입: {reduced_size_entries}개[/yellow]")
    
    console.print(f"\n[bold green]총 {signal_count}개 매수 시그널 전송 완료[/bold green]")
    
    # 시그널이 없을 때 텔레그램 메시지 전송
    if signal_count == 0:
        from datetime import datetime
        date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if notifier.send_no_signal(date_time, timeframes):
            console.print(f"[green]✅ 시그널 없음 메시지 전송: {date_time}[/green]")


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

