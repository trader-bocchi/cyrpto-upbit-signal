"""배치용 시그널 실행 (메모리 데이터 사용)
참조1: python -m src.cli run-signals --timeframes 4h
참조2: python -m src.cli run-signals --timeframes 1d
"""
import pandas as pd
from typing import Dict, List, Tuple
from datetime import datetime, timezone, timedelta
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.telegram.notifier import TelegramNotifier
from src.upbit_client import UpbitClient
from src.storage.sent_store import is_signal_sent, mark_signal_sent
from src.storage.positions_store import add_position, get_all_positions, get_position, load_positions, save_positions, get_position_key
from src.indicators.extrema import find_pivot_min
from src.indicators.moving_averages import calculate_sma
from src.config import (
    BACKTEST_REGIME_ENABLED,
    BACKTEST_REGIME_MODE,
    SMI_LOCAL_MIN_WINDOW,
    SMI_REQUIRE_NEGATIVE_PIVOT,
    SIGNAL_ENABLE_SMA50_FILTER,
)

console = Console()


def run_signals_for_timeframe(
    market_data: Dict[str, Dict[str, pd.DataFrame]],
    timeframe: str,
    timeframes: List[str],
) -> Tuple[List, int]:
    """
    특정 시간프레임에 대한 시그널 실행
    
    Args:
        market_data: {market: {timeframe: DataFrame}} 형태의 딕셔너리 (SMI 포함)
        timeframe: 실행할 시간프레임 ("4h" 또는 "1d")
        timeframes: 전체 시간프레임 리스트 (크로스 체크용)
    
    Returns:
        (all_signals, signal_count) 튜플
    """
    notifier = TelegramNotifier()
    client = UpbitClient()
    
    # Ticker 정보 조회
    console.print(f"[cyan]Ticker 정보 조회 중... ({timeframe})[/cyan]")
    tickers = client.get_ticker_all_krw()
    
    # 전체 거래대금 계산
    total_market_volume = sum(t.get("acc_trade_price_24h", 0) for t in tickers)
    
    # 거래대금 기준 순위 계산
    sorted_tickers = sorted(tickers, key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
    for idx, ticker in enumerate(sorted_tickers, 1):
        ticker["rank"] = idx
        ticker["top_20"] = idx <= 20
        ticker["top_50"] = idx <= 50
        ticker["total_markets"] = len(sorted_tickers)
    
    # ticker_dict 생성 (순위 계산 후)
    ticker_dict = {t["market"]: t for t in sorted_tickers}
    
    # BTC 1D 데이터 로드 (레짐 필터용)
    btc_1d_df = pd.DataFrame()
    if BACKTEST_REGIME_ENABLED:
        from src.backtest.regime_filter import load_btc_1d_data
        btc_1d_df = load_btc_1d_data()
    
    signal_count = 0
    skipped_regime_off = 0
    skipped_duplicate = 0
    
    all_signals = []
    
    # 해당 시간프레임 데이터가 있는 마켓만 필터링
    markets = [m for m in market_data.keys() if timeframe in market_data[m] and not market_data[m][timeframe].empty]
    
    console.print(f"[green]시그널 검사 대상: {len(markets)}개 마켓 ({timeframe})[/green]")
    
    total_tasks = len(markets)
    processed_count = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"[cyan]{timeframe} 시그널 검사 중...", total=total_tasks)
        
        for market in markets:
            df = market_data[market][timeframe].copy()
            
            if df.empty:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            ticker_info = ticker_dict.get(market, {})
            
            # verify_smi_signals.py와 동일한 로직으로 시그널 조건 체크
            df_sorted = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
            
            # SMI가 이미 계산되어 있는지 확인
            if "smi_momentum" not in df_sorted.columns:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 마지막 SMI 값 확인
            smi_values = df_sorted["smi_momentum"].dropna()
            if smi_values.empty:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            last_smi = smi_values.iloc[-1]
            
            # 마지막 SMI 값이 양수/0이면 시그널 없음
            if last_smi >= 0:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 데이터 부족 체크
            if len(df_sorted) < SMI_LOCAL_MIN_WINDOW + 3:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 이동평균 계산
            merged_df = calculate_sma(df_sorted, periods=[50, 200])
            
            # 피벗 찾기
            pivot_info = find_pivot_min(
                merged_df["smi_momentum"],
                window=SMI_LOCAL_MIN_WINDOW,
                require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
            )
            merged_df = pd.concat([merged_df, pivot_info], axis=1)
            
            # 마지막 행 체크
            i = len(merged_df) - 1
            m_i2 = merged_df.iloc[i]["smi_momentum"]
            
            if pd.isna(m_i2):
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 피벗 정보 확인
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
                        signal_eligible = True
                else:
                    signal_eligible = True
            
            # 시그널 조건 만족 여부에 따라 처리
            if not signal_eligible:
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 시그널 생성
            signal_row = merged_df.iloc[i]
            signal_time_kst = signal_row["candle_time_kst"]
            
            signal = {
                "market": market,
                "timeframe": timeframe,
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
            
            # 중복 체크
            if is_signal_sent(
                market=signal["market"],
                timeframe=signal["timeframe"],
                signal_time_kst=signal["signal_time_kst"],
                side="BUY",
            ):
                skipped_duplicate += 1
                processed_count += 1
                progress.update(task, advance=1)
                continue
            
            # 레짐 필터 체크
            regime_blocked = False
            if BACKTEST_REGIME_ENABLED and not btc_1d_df.empty:
                from src.backtest.regime_filter import get_regime_status
                signal_time = pd.to_datetime(signal["signal_time_kst"])
                regime_on = get_regime_status(btc_1d_df, signal_time)
                
                if not regime_on:
                    if BACKTEST_REGIME_MODE == "BLOCK_ENTRY":
                        skipped_regime_off += 1
                        processed_count += 1
                        progress.update(task, advance=1)
                        continue
                    elif BACKTEST_REGIME_MODE == "REDUCE_SIZE":
                        regime_blocked = True
            
            # 크로스 시간프레임 시그널 체크
            has_1d_signal = False
            has_4h_signal = False
            
            if timeframe == "4h" and "1d" in timeframes and "1d" in market_data[market]:
                # 1d 시그널 체크
                df_1d = market_data[market]["1d"].copy()
                if not df_1d.empty and "smi_momentum" in df_1d.columns:
                    df_1d_sorted = df_1d.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                    smi_values_1d = df_1d_sorted["smi_momentum"].dropna()
                    if not smi_values_1d.empty:
                        last_smi_1d = smi_values_1d.iloc[-1]
                        if not pd.isna(last_smi_1d) and last_smi_1d < 0:
                            if len(df_1d_sorted) >= SMI_LOCAL_MIN_WINDOW + 3:
                                merged_df_1d = calculate_sma(df_1d_sorted, periods=[50, 200])
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
            
            elif timeframe == "1d" and "4h" in timeframes and "4h" in market_data[market]:
                # 4h 시그널 체크
                df_4h = market_data[market]["4h"].copy()
                if not df_4h.empty and "smi_momentum" in df_4h.columns:
                    df_4h_sorted = df_4h.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                    smi_values_4h = df_4h_sorted["smi_momentum"].dropna()
                    if not smi_values_4h.empty:
                        last_smi_4h = smi_values_4h.iloc[-1]
                        if not pd.isna(last_smi_4h) and last_smi_4h < 0:
                            if len(df_4h_sorted) >= SMI_LOCAL_MIN_WINDOW + 3:
                                merged_df_4h = calculate_sma(df_4h_sorted, periods=[50, 200])
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
            
            all_signals.append((signal, market, timeframe, ticker_info, regime_blocked, has_1d_signal, has_4h_signal))
            
            processed_count += 1
            progress.update(task, advance=1)
    
    console.print(f"[green]{timeframe} 시그널 검사 완료: {len(all_signals)}개 시그널 발견[/green]")
    
    # 거래대금 기준 정렬
    all_signals.sort(key=lambda x: x[3].get("acc_trade_price_24h", 0), reverse=True)
    
    # 상위 5개만 전송
    signals_to_send = all_signals[:5] if len(all_signals) > 5 else all_signals
    
    # 시그널 전송
    if signals_to_send:
        if len(all_signals) > 5:
            console.print(f"[cyan]시그널 전송 중... (전체 {len(all_signals)}개 중 상위 5개만 전송)[/cyan]")
        else:
            console.print(f"[cyan]시그널 전송 중... ({len(signals_to_send)}개)[/cyan]")
        
        # 현재 시점 (KST)
        kst = timezone(timedelta(hours=9))
        current_time = datetime.now(kst).strftime("%Y년 %m월 %d일 %H시 %M분")
        
        # 모든 시그널을 하나의 메시지로 묶어서 전송
        if notifier.send_buy_signals_batch(signals_to_send, current_time, total_market_volume):
            # 각 시그널을 마킹
            for signal, market, tf, ticker_info, regime_blocked, has_1d_signal, has_4h_signal in signals_to_send:
                mark_signal_sent(
                    market=signal["market"],
                    timeframe=signal["timeframe"],
                    signal_time_kst=signal["signal_time_kst"],
                    side="BUY",
                )
                
                # 포지션 추가 (실시간 트래킹용)
                entry_price = signal["close"]
                existing_pos = get_position(market, tf)
                if existing_pos:
                    # 가중평균 계산
                    old_qty = existing_pos.get("qty", 0.0)
                    old_invested = existing_pos.get("invested_krw", 0.0)
                    new_qty = 0.0
                    new_invested = 0.0
                    
                    total_qty = old_qty + new_qty
                    total_invested = old_invested + new_invested
                    avg_entry = total_invested / total_qty if total_qty > 0 else entry_price
                    
                    # 포지션 업데이트
                    positions = load_positions()
                    key = get_position_key(market, tf)
                    positions[key]["entry_price"] = avg_entry
                    positions[key]["qty"] = total_qty
                    positions[key]["invested_krw"] = total_invested
                    save_positions(positions)
                else:
                    # 신규 포지션 추가
                    add_position(
                        market=market,
                        timeframe=tf,
                        entry_time_kst=signal["signal_time_kst"],
                        entry_price=entry_price,
                        qty=0.0,
                        invested_krw=0.0,
                    )
                    positions = load_positions()
                    key = get_position_key(market, tf)
                    positions[key]["entry_bar_index"] = 0
                    positions[key]["max_favorable_close_pct"] = 0.0
                    positions[key]["max_adverse_close_pct"] = 0.0
                    save_positions(positions)
                
                signal_count += 1
            
            console.print(f"[green]✅ {len(signals_to_send)}개 매수 시그널 일괄 전송 완료[/green]")
            if len(all_signals) > 5:
                console.print(f"[yellow]  (전체 {len(all_signals)}개 중 상위 5개만 전송됨)[/yellow]")
    
    # 시그널이 없을 때 텔레그램 메시지 전송
    if signal_count == 0:
        kst = timezone(timedelta(hours=9))
        date_time = datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")
        
        if notifier.send_no_signal(date_time, [timeframe]):
            console.print(f"[green]✅ 시그널 없음 메시지 전송: {date_time}[/green]")
    
    return all_signals, signal_count

