"""배치용 데이터 수집 (최근 120일치, 메모리에만 저장)
참조: python -m src.cli fetch-daily
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
    fetch_timeframe_direct,
)
from src.upbit_client_fast import FastUpbitClient

console = Console()


def drop_incomplete_candle(df: pd.DataFrame) -> pd.DataFrame:
    """가장 최근(진행 중) 캔들을 제거해 완성된 봉만 남긴다.

    업비트 캔들 API는 현재 진행 중인 캔들을 가장 최근 행으로 반환한다.
    이 행으로 시그널을 판정하면 캔들이 닫히며 SMI 값이 바뀌어 알림 후
    시그널이 사라지는 리페인팅이 발생한다. 바이낸스 수집 경로와 동일하게
    마지막 봉을 버려 완성된 봉만으로 판정한다.
    """
    if df.empty:
        return df
    return df.iloc[:-1].reset_index(drop=True)


def fetch_recent_90days(
    markets: Optional[List[str]] = None,
    timeframes: List[str] = ["4h", "1d"],
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    최근 120일치 4h/1d 캔들 수집 (메모리에만 저장)
    참조: python -m src.cli fetch-daily (fetch_1h_incremental 로직 참조)
    
    Args:
        markets: 마켓 리스트 (None이면 모든 KRW 마켓)
        timeframes: 시간프레임 리스트 (["4h", "1d"])
    
    Returns:
        {market: {timeframe: DataFrame}} 형태의 딕셔너리
    """
    client = FastUpbitClient(base_sleep=0.1)
    
    # 마켓 목록 조회 (KRW 마켓만) - fetch-daily 스타일 참조
    if markets is None:
        console.print("[cyan]마켓 목록 조회 중...[/cyan]")
        try:
            all_markets_data = client.get_markets()
            markets = [m["market"] for m in all_markets_data if m.get("market", "").startswith("KRW-")]
            console.print(f"[green]증분 수집 대상: {len(markets)}개 마켓[/green]")
        except Exception as e:
            console.print(f"[red]마켓 목록 조회 실패: {e}[/red]")
            return {}
    
    # 시간 범위 설정 (최근 120일) - 시그널 검증을 위해 103개 이상 필요 (SMI_LOCAL_MIN_WINDOW + 3)
    now_kst = datetime.now()
    end_kst = now_kst
    start_kst = (now_kst - timedelta(days=120)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    
    # fetch-daily 스타일로 각 마켓 처리
    for market in markets:
        console.print(f"\n[cyan]{market}[/cyan]")
        result[market] = {}
        
        for timeframe in timeframes:
            try:
                # fetch_timeframe_direct 사용 (참조: fetch-4h-1d-direct)
                # - 4h: candles/minutes/240 엔드포인트 사용
                # - 1d: candles/days 엔드포인트 사용 (days endpoint에 맞게 자동 처리됨)
                # start_kst와 end_kst를 명시적으로 전달하여 최근 120일치만 수집
                new_df = fetch_timeframe_direct(market, timeframe, start_kst=start_kst, end_kst=end_kst)
                
                if new_df.empty:
                    console.print(f"  [{timeframe}] 데이터 없음")
                    result[market][timeframe] = pd.DataFrame()
                else:
                    # 정렬 (fetch-daily 스타일)
                    new_df = new_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                    # 마지막 미완성(진행 중) 캔들 제거 — 리페인팅 방지
                    new_df = drop_incomplete_candle(new_df)
                    result[market][timeframe] = new_df
                    console.print(f"  [{timeframe}] {len(new_df)}개 수집 완료 (완성 봉만)")
                    
            except Exception as e:
                console.print(f"  [{timeframe}] 오류: {e}")
                import traceback
                console.print(f"[red]{traceback.format_exc()}[/red]")
                result[market][timeframe] = pd.DataFrame()
    
    # 수집 결과 요약 (행 수 포함)
    total_4h = sum(1 for m in result.values() if not m.get("4h", pd.DataFrame()).empty)
    total_1d = sum(1 for m in result.values() if not m.get("1d", pd.DataFrame()).empty)
    total_4h_rows = sum(len(m.get("4h", pd.DataFrame())) for m in result.values())
    total_1d_rows = sum(len(m.get("1d", pd.DataFrame())) for m in result.values())
    
    console.print(f"\n[green]최근 120일치 데이터 수집 완료[/green]")
    console.print(f"  4h 데이터: {total_4h}개 마켓, 총 {total_4h_rows:,}개 행")
    console.print(f"  1d 데이터: {total_1d}개 마켓, 총 {total_1d_rows:,}개 행")
    
    return result

