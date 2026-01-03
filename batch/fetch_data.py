"""배치용 데이터 수집 (최근 90일치, 메모리에만 저장)
참조: python -m src.cli fetch-4h-1d-direct
"""
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

# 참조 명령어의 로직을 최대한 준수하기 위해 동일한 함수 사용
from src.pipeline.fetch_4h_1d_direct import (
    parse_kst_string,
    process_candle_data,
    fetch_timeframe_direct as ref_fetch_timeframe_direct,
)
from src.upbit_client_fast import FastUpbitClient

console = Console()


def fetch_timeframe_direct(
    market: str,
    timeframe: str,
    start_kst: datetime,
    end_kst: datetime,
) -> pd.DataFrame:
    """
    특정 시간프레임 캔들 직접 수집 (메모리에만 저장)
    
    Args:
        market: 마켓 코드
        timeframe: "4h" 또는 "1d"
        start_kst: 시작 시각
        end_kst: 종료 시각
    """
    client = FastUpbitClient(base_sleep=0.1)
    
    # 시간프레임별 unit 설정
    if timeframe == "4h":
        unit = 240  # 4시간 = 240분
    elif timeframe == "1d":
        unit = 1440  # 1일 = 1440분
    else:
        raise ValueError(f"지원하지 않는 시간프레임: {timeframe}")
    
    # 수집 루프
    current_to = end_kst
    all_dfs = []
    
    # 무한 루프 방지
    last_min_time = None
    same_time_count = 0
    MAX_SAME_TIME_COUNT = 3
    
    max_iterations = 1000  # 무한 루프 방지
    iteration_count = 0
    
    while current_to >= start_kst and iteration_count < max_iterations:
        iteration_count += 1
        
        try:
            # API 호출
            to_str = current_to.strftime("%Y-%m-%dT%H:%M:%S")
            candles = client.get_candles_minutes(market, unit=unit, to=to_str, count=200)
            
            if not candles or len(candles) == 0:
                break
            
            # DataFrame 변환
            df = process_candle_data(candles, market)
            
            if df.empty:
                break
            
            # 최소 시간 확인
            min_time = df["candle_time_kst"].min()
            
            # 무한 루프 방지
            if last_min_time is not None:
                if min_time >= last_min_time:
                    same_time_count += 1
                    if same_time_count >= MAX_SAME_TIME_COUNT:
                        break
                else:
                    same_time_count = 0
            
            last_min_time = min_time
            all_dfs.append(df)
            
            # 다음 배치
            current_to = min_time - timedelta(seconds=1)
            
            if min_time < start_kst:
                break
                
        except Exception as e:
            console.print(f"[yellow]  [{timeframe}] API 호출 오류 (반복 {iteration_count}): {e}[/yellow]")
            # 오류 발생 시 루프 종료
            break
    
    if not all_dfs:
        return pd.DataFrame()
    
    combined_df = pd.concat(all_dfs, ignore_index=True)
    return combined_df


def fetch_recent_90days(
    markets: Optional[List[str]] = None,
    timeframes: List[str] = ["4h", "1d"],
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    최근 90일치 4h/1d 캔들 수집 (메모리에만 저장)
    
    Args:
        markets: 마켓 리스트 (None이면 모든 KRW 마켓)
        timeframes: 시간프레임 리스트 (["4h", "1d"])
    
    Returns:
        {market: {timeframe: DataFrame}} 형태의 딕셔너리
    """
    client = FastUpbitClient(base_sleep=0.1)
    
    # 마켓 목록 조회 (KRW 마켓만)
    if markets is None:
        all_markets_data = client.get_markets()
        markets = [m["market"] for m in all_markets_data if m.get("market", "").startswith("KRW-")]
    
    console.print(f"[green]최근 90일치 데이터 수집 대상: {len(markets)}개 마켓[/green]")
    
    # 시간 범위 설정 (최근 90일)
    end_kst = datetime.now()
    start_kst = end_kst - timedelta(days=90)
    
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    
    # 진행 상황 표시 추가
    total_tasks = len(markets) * len(timeframes)
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]데이터 수집 중...", total=total_tasks)
        
        for market in markets:
            result[market] = {}
            
            for timeframe in timeframes:
                try:
                    progress.update(task, description=f"[cyan]{market} {timeframe} 수집 중...")
                    
                    # 수집 (참조 함수 사용)
                    df = fetch_timeframe_direct(market, timeframe, start_kst, end_kst)
                    
                    if df.empty:
                        result[market][timeframe] = pd.DataFrame()
                    else:
                        # 정렬
                        df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                        result[market][timeframe] = df
                    
                    progress.update(task, advance=1)
                    
                except Exception as e:
                    console.print(f"[red]  [{market} {timeframe}] 오류: {e}[/red]")
                    result[market][timeframe] = pd.DataFrame()
                    progress.update(task, advance=1)
    
    # 수집 결과 요약
    total_4h = sum(1 for m in result.values() if not m.get("4h", pd.DataFrame()).empty)
    total_1d = sum(1 for m in result.values() if not m.get("1d", pd.DataFrame()).empty)
    total_4h_rows = sum(len(m.get("4h", pd.DataFrame())) for m in result.values())
    total_1d_rows = sum(len(m.get("1d", pd.DataFrame())) for m in result.values())
    
    console.print(f"\n[green]최근 90일치 데이터 수집 완료[/green]")
    console.print(f"  4h 데이터: {total_4h}개 마켓, 총 {total_4h_rows:,}개 캔들")
    console.print(f"  1d 데이터: {total_1d}개 마켓, 총 {total_1d_rows:,}개 캔들")
    
    return result

