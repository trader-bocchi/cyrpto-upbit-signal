"""1시간 캔들을 4시간/24시간으로 집계"""
import pandas as pd
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from rich.progress import track

from src.config import RAW_DATA_PATH, DERIVED_DATA_PATH, KST_OFFSET_HOURS
from src.storage.csv_store import (
    get_candle_filepath,
    read_csv_safe,
    atomic_write_csv,
    ensure_candle_dtypes,
)
from src.storage.dedup import dedup_candles

console = Console()


def aggregate_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """1시간 캔들을 4시간으로 집계 (KST 기준: 00,04,08,12,16,20)"""
    if df_1h.empty:
        return pd.DataFrame()
    
    df = df_1h.copy()
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    
    # 4시간 바운더리로 그룹화 (KST 기준)
    df["hour"] = df["candle_time_kst"].dt.hour
    df["date"] = df["candle_time_kst"].dt.date
    
    # 4시간 구간 할당 (0-3: 0, 4-7: 4, 8-11: 8, 12-15: 12, 16-19: 16, 20-23: 20)
    df["boundary_hour"] = (df["hour"] // 4) * 4
    
    # 그룹 키: (date, boundary_hour)
    df["group_key"] = df["date"].astype(str) + " " + df["boundary_hour"].astype(str).str.zfill(2) + ":00:00"
    df["group_key"] = pd.to_datetime(df["group_key"])
    
    # 집계
    agg_df = df.groupby(["market", "group_key"], as_index=False).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    
    agg_df.rename(columns={"group_key": "candle_time_kst"}, inplace=True)
    
    return agg_df


def aggregate_1d(df_1h: pd.DataFrame) -> pd.DataFrame:
    """1시간 캔들을 24시간(일봉)으로 집계"""
    if df_1h.empty:
        return pd.DataFrame()
    
    df = df_1h.copy()
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    
    # 일자별 그룹화
    df["date"] = df["candle_time_kst"].dt.date
    df["group_key"] = pd.to_datetime(df["date"].astype(str) + " 00:00:00")
    
    # 집계
    agg_df = df.groupby(["market", "group_key"], as_index=False).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    
    agg_df.rename(columns={"group_key": "candle_time_kst"}, inplace=True)
    
    return agg_df


def aggregate_timeframes(markets: Optional[List[str]] = None, timeframes: List[str] = ["4h", "1d"]):
    """
    시간프레임별 집계 실행
    
    Args:
        markets: 마켓 리스트 (None이면 모든 마켓)
        timeframes: 집계할 시간프레임 리스트
    """
    # 마켓 목록 조회
    if markets is None:
        # data/raw/candles_1h에서 마켓 목록 추출
        base_path = RAW_DATA_PATH / "candles_1h"
        if base_path.exists():
            markets = [
                d.name.replace("market=", "")
                for d in base_path.iterdir()
                if d.is_dir() and d.name.startswith("market=")
            ]
        else:
            console.print("[red]1시간 캔들 데이터가 없습니다.[/red]")
            return
    
    console.print(f"[green]집계 대상: {len(markets)}개 마켓, {timeframes} 시간프레임[/green]")
    
    for market in track(markets, description="집계 중..."):
        # 1시간 데이터 로드
        filepath_1h = get_candle_filepath(RAW_DATA_PATH, market, "1h", year=2025)
        df_1h = read_csv_safe(filepath_1h)
        
        if df_1h.empty:
            console.print(f"[yellow]  {market}: 1시간 데이터 없음[/yellow]")
            continue
        
        df_1h = ensure_candle_dtypes(df_1h)
        
        for timeframe in timeframes:
            if timeframe == "4h":
                agg_df = aggregate_4h(df_1h)
            elif timeframe == "1d":
                agg_df = aggregate_1d(df_1h)
            else:
                console.print(f"[red]  알 수 없는 시간프레임: {timeframe}[/red]")
                continue
            
            if agg_df.empty:
                continue
            
            # 기존 데이터와 병합 및 중복 제거
            filepath_agg = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
            existing_df = read_csv_safe(filepath_agg)
            
            if not existing_df.empty:
                combined_df = pd.concat([existing_df, agg_df], ignore_index=True)
            else:
                combined_df = agg_df
            
            combined_df = dedup_candles(combined_df)
            combined_df = ensure_candle_dtypes(combined_df)
            
            # 저장
            atomic_write_csv(combined_df, filepath_agg)
            
            console.print(f"[green]  {market} {timeframe}: {len(combined_df)}개 캔들[/green]")

