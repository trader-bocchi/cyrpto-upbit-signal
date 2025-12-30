"""일일 증분 1시간 캔들 수집"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from rich.console import Console

from src.config import RAW_DATA_PATH, KST_OFFSET_HOURS
import pandas as pd
from src.upbit_client import UpbitClient
from src.storage.csv_store import (
    get_candle_filepath,
    read_csv_safe,
    atomic_write_csv,
    ensure_candle_dtypes,
)
from src.storage.dedup import dedup_candles
from src.storage.checkpoints import load_checkpoints, save_checkpoint

console = Console()


def kst_to_utc_timestamp(kst_str: str) -> int:
    """KST 시간 문자열을 UTC 타임스탬프(ms)로 변환"""
    dt = pd.to_datetime(kst_str)
    utc_dt = dt - pd.Timedelta(hours=KST_OFFSET_HOURS)
    return int(utc_dt.timestamp() * 1000)
    return int(utc_dt.timestamp() * 1000)


def fetch_1h_incremental():
    """체크포인트 이후 구간만 수집"""
    client = UpbitClient()
    checkpoints = load_checkpoints()
    
    # 모든 KRW 마켓 조회
    all_markets = client.get_markets()
    markets = [m["market"] for m in all_markets]
    
    console.print(f"[green]증분 수집 대상: {len(markets)}개 마켓[/green]")
    
    # 현재 시각 (KST)
    now_kst = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for market in markets:
        checkpoint = checkpoints.get(market)
        
        if checkpoint:
            # 체크포인트 이후부터 수집
            start_kst = checkpoint
            console.print(f"[cyan]{market}: {start_kst} 이후 수집[/cyan]")
        else:
            # 체크포인트가 없으면 2025-01-01부터
            start_kst = "2025-01-01 00:00:00"
            console.print(f"[cyan]{market}: 체크포인트 없음, 2025-01-01부터 수집[/cyan]")
        
        # 기존 데이터 로드
        filepath = get_candle_filepath(RAW_DATA_PATH, market, "1h", year=2025)
        existing_df = read_csv_safe(filepath)
        
        all_candles = []
        current_ts = kst_to_utc_timestamp(now_kst)
        start_ts = kst_to_utc_timestamp(start_kst)
        
        # 최근 1일치만 수집 (증분)
        while current_ts >= start_ts:
            to_str = datetime.fromtimestamp(current_ts / 1000).isoformat() + "Z"
            
            candles = client.get_candles_minutes(
                market=market,
                unit=60,
                to=to_str,
                count=200,
            )
            
            if not candles:
                break
            
            df = pd.DataFrame(candles)
            if df.empty:
                break
            
            # Upbit API는 candle_date_time_kst를 제공하지만, 없으면 candle_date_time_utc 사용
            if "candle_date_time_kst" in df.columns:
                df["candle_time_kst"] = df["candle_date_time_kst"]
            elif "candle_date_time_utc" in df.columns:
                df["candle_time_kst"] = pd.to_datetime(df["candle_date_time_utc"]) + pd.Timedelta(hours=KST_OFFSET_HOURS)
                df["candle_time_kst"] = df["candle_time_kst"].dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                df["candle_time_kst"] = pd.to_datetime(df["candle_date_time"], unit="ms") + pd.Timedelta(hours=KST_OFFSET_HOURS)
                df["candle_time_kst"] = df["candle_time_kst"].dt.strftime("%Y-%m-%d %H:%M:%S")
            
            df["market"] = market
            df["ingest_time_kst"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
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
            
            if "candle_date_time" in df.columns:
                min_ts_ms = df["candle_date_time"].min()
                current_ts = min_ts_ms - 1
            else:
                min_ts = pd.to_datetime(df["candle_time_kst"]).min()
                min_ts_utc = kst_to_utc_timestamp(str(min_ts))
                if min_ts_utc >= current_ts:
                    break
                current_ts = min_ts_utc - 1
            
            import time
            time.sleep(0.1)
        
        if not all_candles:
            continue
        
        new_df = pd.concat(all_candles, ignore_index=True)
        if not existing_df.empty:
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        combined_df = dedup_candles(combined_df)
        combined_df = ensure_candle_dtypes(combined_df)
        
        # 저장
        atomic_write_csv(combined_df, filepath)
        
        # 체크포인트 업데이트
        if not combined_df.empty:
            last_time = combined_df["candle_time_kst"].max()
            save_checkpoint(market, str(last_time))
        
        console.print(f"[green]  {market}: {len(new_df)}개 새 캔들 추가[/green]")

