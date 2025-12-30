"""2025년 전체 1시간 캔들 수집"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional
from rich.console import Console
from rich.progress import track

from src.config import RAW_DATA_PATH, KST_OFFSET_HOURS
from src.upbit_client import UpbitClient
from src.storage.csv_store import (
    get_candle_filepath,
    read_csv_safe,
    atomic_write_csv,
    ensure_candle_dtypes,
)
from src.storage.dedup import dedup_candles
from src.storage.checkpoints import save_checkpoint

console = Console()


def kst_to_utc_timestamp(kst_str: str) -> int:
    """KST 시간 문자열을 UTC 타임스탬프(ms)로 변환"""
    dt = pd.to_datetime(kst_str)
    # KST를 UTC로 변환 (9시간 빼기)
    utc_dt = dt - pd.Timedelta(hours=KST_OFFSET_HOURS)
    return int(utc_dt.timestamp() * 1000)


def utc_timestamp_to_kst_str(timestamp_ms: int) -> str:
    """UTC 타임스탬프(ms)를 KST 문자열로 변환"""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    kst_dt = dt + timedelta(hours=KST_OFFSET_HOURS)
    return kst_dt.strftime("%Y-%m-%d %H:%M:%S")


def fetch_1h_candles_2025(markets: Optional[List[str]] = None):
    """
    2025년 전체 1시간 캔들 수집
    
    Args:
        markets: 마켓 리스트 (None이면 모든 KRW 마켓)
    """
    client = UpbitClient()
    
    if markets is None:
        all_markets = client.get_markets()
        markets = [m["market"] for m in all_markets]
    
    console.print(f"[green]수집 대상: {len(markets)}개 마켓[/green]")
    
    # 2025년 범위 (KST)
    start_kst = "2025-01-01 00:00:00"
    end_kst = "2025-12-31 23:00:00"
    
    start_ts = kst_to_utc_timestamp(start_kst)
    end_ts = kst_to_utc_timestamp(end_kst)
    
    for market in track(markets, description="수집 중..."):
        console.print(f"\n[cyan]처리 중: {market}[/cyan]")
        
        # 기존 데이터 로드
        filepath = get_candle_filepath(RAW_DATA_PATH, market, "1h", year=2025)
        existing_df = read_csv_safe(filepath)
        
        all_candles = []
        current_ts = end_ts
        
        while current_ts >= start_ts:
            # Upbit API는 과거부터 현재 방향으로 조회
            to_str = datetime.fromtimestamp(current_ts / 1000).isoformat() + "Z"
            
            candles = client.get_candles_minutes(
                market=market,
                unit=60,
                to=to_str,
                count=200,
            )
            
            if not candles:
                break
            
            # DataFrame 변환
            df = pd.DataFrame(candles)
            if df.empty:
                break
            
            # 컬럼명 변환 및 KST 변환
            # Upbit API는 candle_date_time_kst를 제공하지만, 없으면 candle_date_time_utc 사용
            if "candle_date_time_kst" in df.columns:
                df["candle_time_kst"] = df["candle_date_time_kst"]
            elif "candle_date_time_utc" in df.columns:
                # UTC를 KST로 변환
                df["candle_time_kst"] = pd.to_datetime(df["candle_date_time_utc"]) + pd.Timedelta(hours=KST_OFFSET_HOURS)
                df["candle_time_kst"] = df["candle_time_kst"].dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # 타임스탬프로부터 변환
                df["candle_time_kst"] = pd.to_datetime(df["candle_date_time"], unit="ms") + pd.Timedelta(hours=KST_OFFSET_HOURS)
                df["candle_time_kst"] = df["candle_time_kst"].dt.strftime("%Y-%m-%d %H:%M:%S")
            
            df["market"] = market
            df["ingest_time_kst"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 필요한 컬럼만 선택
            df = df[
                [
                    "market",
                    "candle_time_kst",
                    "opening_price",
                    "high_price",
                    "low_price",
                    "trade_price",
                    "candle_acc_trade_volume",
                    "ingest_time_kst",
                ]
            ]
            df.columns = [
                "market",
                "candle_time_kst",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "ingest_time_kst",
            ]
            
            all_candles.append(df)
            
            # 다음 배치를 위한 타임스탬프 업데이트
            # 가장 오래된 캔들의 타임스탬프 사용
            if "candle_date_time" in df.columns:
                min_ts_ms = df["candle_date_time"].min()
                current_ts = min_ts_ms - 1
            else:
                min_ts = pd.to_datetime(df["candle_time_kst"]).min()
                min_ts_utc = kst_to_utc_timestamp(str(min_ts))
                if min_ts_utc >= current_ts:
                    break
                current_ts = min_ts_utc - 1
            
            # Rate limit 고려
            import time
            time.sleep(0.1)
        
        if not all_candles:
            console.print(f"[yellow]  {market}: 데이터 없음[/yellow]")
            continue
        
        # 병합 및 중복 제거
        new_df = pd.concat(all_candles, ignore_index=True)
        if not existing_df.empty:
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        combined_df = dedup_candles(combined_df)
        combined_df = ensure_candle_dtypes(combined_df)
        
        # 2025년 데이터만 필터링
        combined_df["candle_time_kst"] = pd.to_datetime(combined_df["candle_time_kst"])
        mask = (combined_df["candle_time_kst"] >= start_kst) & (
            combined_df["candle_time_kst"] <= end_kst
        )
        combined_df = combined_df[mask].copy()
        
        # 저장
        atomic_write_csv(combined_df, filepath)
        
        # 체크포인트 업데이트
        if not combined_df.empty:
            last_time = combined_df["candle_time_kst"].max()
            save_checkpoint(market, str(last_time))
        
        console.print(f"[green]  {market}: {len(combined_df)}개 캔들 저장 완료[/green]")

