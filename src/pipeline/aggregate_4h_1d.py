"""1시간 캔들을 4시간/24시간으로 집계 (월 단위 파일 입력)"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional
from rich.console import Console
from rich.progress import track

from src.config import RAW_DATA_PATH_1H, DERIVED_DATA_PATH
from src.storage.csv_store import (
    read_csv_safe,
    atomic_write_csv,
    ensure_candle_dtypes,
)
from src.storage.monthly_store import load_monthly_csv
from src.storage.dedup import dedup_candles

console = Console()


def load_1h_data_for_market(
    data_root: Path,
    market: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    월 단위 1시간 CSV 파일들을 로드하여 하나의 DataFrame으로 반환
    
    Args:
        data_root: 기본 경로
        market: 마켓 코드
        start_date: 시작 날짜 (None이면 전체)
        end_date: 종료 날짜 (None이면 전체)
    """
    all_dfs = []
    
    # 월 단위 파일 스캔
    if start_date is None:
        start_date = datetime(2025, 1, 1)
    if end_date is None:
        end_date = datetime(2025, 12, 31)
    
    # 월별로 반복
    current_date = start_date.replace(day=1)
    while current_date <= end_date:
        df = load_monthly_csv(data_root, market, current_date)
        if not df.empty:
            all_dfs.append(df)
        
        # 다음 달로 이동
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)
    
    if not all_dfs:
        return pd.DataFrame()
    
    combined_df = pd.concat(all_dfs, ignore_index=True)
    
    # 중복 제거
    combined_df = dedup_candles(combined_df)
    
    # 정렬
    if "candle_time_kst" in combined_df.columns:
        combined_df["candle_time_kst"] = pd.to_datetime(combined_df["candle_time_kst"])
        combined_df = combined_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return combined_df


def aggregate_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    1시간 캔들을 4시간으로 집계 (KST 기준: 00,04,08,12,16,20)
    
    비규격 시간대 처리:
    - 신규 상장 등으로 17시, 13시 등 비규격 시간대가 생기면
    - 다음 규격 시간대(0시/4시/8시/12시/16시/20시)까지만 집계
    - 그 다음 시간부터는 규격 시간대로 요약
    """
    if df_1h.empty:
        return pd.DataFrame()
    
    df = df_1h.copy()
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    df = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # 규격 시간대: 0, 4, 8, 12, 16, 20
    STANDARD_HOURS = [0, 4, 8, 12, 16, 20]
    
    # 각 행에 대해 그룹 키 할당 (벡터화된 방식)
    def get_boundary_hour(hour: int) -> int:
        """시간에 대해 해당하는 규격 시간대 반환"""
        if hour in STANDARD_HOURS:
            return hour
        # 비규격 시간대: 이전 규격 시간대 찾기
        for std_hour in reversed(STANDARD_HOURS):
            if std_hour <= hour:
                return std_hour
        # 0시 이전이면 전날 20시 구간에 포함 (실제로는 발생하지 않지만 안전장치)
        return 20
    
    df["hour"] = df["candle_time_kst"].dt.hour
    df["date"] = df["candle_time_kst"].dt.date
    df["boundary_hour"] = df["hour"].apply(get_boundary_hour)
    
    # 그룹 키 생성
    df["group_key"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["boundary_hour"].astype(str).str.zfill(2) + ":00:00"
    )
    
    # 각 그룹에 대해 비규격 시간대 처리
    result_rows = []
    
    for (market, group_key), group_df in df.groupby(["market", "group_key"]):
        group_df = group_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
        
        # 그룹 키의 시간대 확인
        group_hour = group_key.hour
        
        # 규격 시간대인지 확인
        if group_hour in STANDARD_HOURS:
            # 규격 시간대: 다음 규격 시간대 전까지만 집계
            next_standard_hour = None
            for std_hour in STANDARD_HOURS:
                if std_hour > group_hour:
                    next_standard_hour = std_hour
                    break
            
            if next_standard_hour is None:
                # 20시 이후면 다음날 0시
                next_boundary = group_key.replace(hour=0) + timedelta(days=1)
            else:
                next_boundary = group_key.replace(hour=next_standard_hour)
            
            # 다음 규격 시간대 전까지만 포함
            valid_df = group_df[group_df["candle_time_kst"] < next_boundary]
            
            if not valid_df.empty:
                # 집계
                result_rows.append({
                    "market": market,
                    "candle_time_kst": group_key,
                    "open": valid_df["open"].iloc[0],
                    "high": valid_df["high"].max(),
                    "low": valid_df["low"].min(),
                    "close": valid_df["close"].iloc[-1],
                    "volume": valid_df["volume"].sum(),
                })
        else:
            # 비규격 시간대: 이미 assign_group_key에서 처리됨
            # 여기서는 일반 집계만 수행
            result_rows.append({
                "market": market,
                "candle_time_kst": group_key,
                "open": group_df["open"].iloc[0],
                "high": group_df["high"].max(),
                "low": group_df["low"].min(),
                "close": group_df["close"].iloc[-1],
                "volume": group_df["volume"].sum(),
            })
    
    if not result_rows:
        return pd.DataFrame()
    
    agg_df = pd.DataFrame(result_rows)
    agg_df = agg_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return agg_df


def aggregate_1d(df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    1시간 캔들을 24시간(일봉)으로 집계
    
    집계 규칙:
    - KST 기준 일자별로 그룹화 (00:00:00 ~ 23:59:59)
    - open: 해당 일의 첫 번째 1시간 캔들의 open
    - high: 해당 일의 모든 1시간 캔들 중 최고가
    - low: 해당 일의 모든 1시간 캔들 중 최저가
    - close: 해당 일의 마지막 1시간 캔들의 close
    - volume: 해당 일의 모든 1시간 캔들의 volume 합계
    """
    if df_1h.empty:
        return pd.DataFrame()
    
    df = df_1h.copy()
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    
    # 필요한 컬럼 확인
    required_cols = ["market", "open", "high", "low", "close", "volume"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        console.print(f"[red]경고: 필수 컬럼 누락: {missing_cols}[/red]")
        return pd.DataFrame()
    
    # 일자별 그룹화 (KST 기준)
    # 각 캔들의 날짜를 추출하여 같은 날짜의 캔들을 그룹화
    df["date"] = df["candle_time_kst"].dt.date
    df["group_key"] = pd.to_datetime(df["date"].astype(str) + " 00:00:00")
    
    # 날짜별로 정렬 (같은 날짜 내에서도 시간순)
    df = df.sort_values(["group_key", "candle_time_kst"], ascending=True)
    
    # 집계 (같은 날짜의 모든 1시간 캔들을 하나의 일봉으로)
    agg_df = df.groupby(["market", "group_key"], as_index=False).agg(
        {
            "open": "first",   # 해당 일의 첫 번째 캔들의 시가
            "high": "max",     # 해당 일의 최고가
            "low": "min",      # 해당 일의 최저가
            "close": "last",  # 해당 일의 마지막 캔들의 종가
            "volume": "sum",  # 해당 일의 거래량 합계
        }
    )
    
    agg_df.rename(columns={"group_key": "candle_time_kst"}, inplace=True)
    
    # 정렬
    agg_df = agg_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    return agg_df


def get_markets_from_monthly_files(data_root: Path) -> List[str]:
    """월 단위 파일에서 마켓 목록 추출"""
    markets = set()
    
    if not data_root.exists():
        return []
    
    for file_path in data_root.glob("upbit_*.csv"):
        # upbit_KRW-BTC_202507.csv 형식
        parts = file_path.stem.split("_")
        if len(parts) >= 3:
            market = parts[1]  # KRW-BTC
            markets.add(market)
    
    return sorted(list(markets))


def aggregate_timeframes(markets: Optional[List[str]] = None, timeframes: List[str] = ["4h", "1d"]):
    """
    시간프레임별 집계 실행 (월 단위 파일 입력)
    
    Args:
        markets: 마켓 리스트 (None이면 모든 마켓)
        timeframes: 집계할 시간프레임 리스트
    """
    # 마켓 목록 조회
    if markets is None:
        markets = get_markets_from_monthly_files(RAW_DATA_PATH_1H)
        
        if not markets:
            console.print(f"[red]1시간 캔들 데이터가 없습니다. (경로: {RAW_DATA_PATH_1H})[/red]")
            console.print(f"[yellow]힌트: python scripts/collect_upbit_1h_2025.py를 먼저 실행하세요.[/yellow]")
            return
    
    console.print(f"[green]집계 대상: {len(markets)}개 마켓, {timeframes} 시간프레임[/green]")
    console.print(f"[cyan]데이터 경로: {RAW_DATA_PATH_1H}[/cyan]")
    
    for market in track(markets, description="집계 중..."):
        # 월 단위 파일들에서 1시간 데이터 로드
        df_1h = load_1h_data_for_market(RAW_DATA_PATH_1H, market)
        
        if df_1h.empty:
            console.print(f"[yellow]  {market}: 1시간 데이터 없음[/yellow]")
            continue
        
        df_1h = ensure_candle_dtypes(df_1h)
        
        # 입력 데이터 확인
        console.print(f"[dim]  {market}: 입력 데이터 {len(df_1h)}개 행[/dim]")
        
        for timeframe in timeframes:
            # 시간프레임 정규화 및 자동 수정
            timeframe_orig = timeframe
            timeframe = timeframe.strip().lower()
            
            # "1"이 단독으로 오면 "1d"로 자동 수정
            if timeframe == "1":
                timeframe = "1d"
            
            if timeframe == "4h":
                agg_df = aggregate_4h(df_1h)
            elif timeframe in ["1d", "24h", "d"]:
                # 1d = 24h = 일봉 (24시간 단위)
                agg_df = aggregate_1d(df_1h)
            else:
                console.print(f"[red]  알 수 없는 시간프레임: '{timeframe_orig}'[/red]")
                continue
            
            if agg_df.empty:
                console.print(f"[yellow]  {market} {timeframe}: 집계 결과가 비어있음 (입력 데이터: {len(df_1h)}개 행)[/yellow]")
                # 디버깅: 컬럼 확인
                if not df_1h.empty:
                    console.print(f"[dim]    입력 컬럼: {list(df_1h.columns)}[/dim]")
                continue
            
            # 기존 데이터와 병합 및 중복 제거
            # 집계 결과는 기존 방식 유지 (단일 파일)
            from src.storage.csv_store import get_candle_filepath
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
            
            # 저장 확인
            if filepath_agg.exists():
                console.print(f"[green]  {market} {timeframe}: {len(combined_df)}개 캔들 저장 완료 ({filepath_agg})[/green]")
            else:
                console.print(f"[red]  {market} {timeframe}: 저장 실패! ({filepath_agg})[/red]")
