"""2025년 전체 1시간 캔들 수집 (월 단위 저장)"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from rich.console import Console
from rich.progress import track

from src.config import RAW_DATA_PATH_1H, META_DATA_PATH
from src.upbit_client_fast import FastUpbitClient
from src.storage.monthly_store import (
    load_monthly_csv,
    merge_monthly_data,
)
from src.storage.missing_logger import log_missing_summary

console = Console()

# Meta 파일 저장 경로 (CSV와 분리)
META_ROOT = META_DATA_PATH / "candles_1h"
META_ROOT.mkdir(parents=True, exist_ok=True)


def parse_kst_string(s: str) -> datetime:
    """KST 문자열을 datetime으로 파싱"""
    s = s.replace("T", " ").strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(f"날짜 파싱 실패: {s}")


def process_candle_data(candles: List[dict], market: str) -> pd.DataFrame:
    """캔들 데이터를 DataFrame으로 변환"""
    rows = []
    ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for candle in candles:
        if "candle_date_time_kst" not in candle:
            continue
        
        candle_time_kst = parse_kst_string(candle["candle_date_time_kst"])
        
        row = {
            "market": market,
            "candle_time_kst": candle_time_kst,
            "open": float(candle.get("opening_price", 0)),
            "high": float(candle.get("high_price", 0)),
            "low": float(candle.get("low_price", 0)),
            "close": float(candle.get("trade_price", 0)),
            "volume": float(candle.get("candle_acc_trade_volume", 0)),
            "trade_value": float(candle.get("candle_acc_trade_price", 0)),
            "ingest_time_kst": ingest_time,
        }
        rows.append(row)
    
    return pd.DataFrame(rows)


def fetch_1h_candles_2025(markets: Optional[List[str]] = None):
    """
    2025년 전체 1시간 캔들 수집 (월 단위 저장)
    
    Args:
        markets: 마켓 리스트 (None이면 모든 KRW 마켓)
    """
    client = FastUpbitClient(base_sleep=0.1)
    
    if markets is None:
        all_markets_data = client.get_markets()
        markets = [m["market"] for m in all_markets_data]
    
    console.print(f"[green]수집 대상: {len(markets)}개 마켓[/green]")
    
    # 2025년 범위 (KST)
    start_kst = datetime(2025, 1, 1, 0, 0, 0)
    end_kst = datetime(2025, 12, 31, 23, 0, 0)
    
    for market in track(markets, description="수집 중..."):
        console.print(f"\n[cyan]처리 중: {market}[/cyan]")
        
        # 수집 루프
        current_to = end_kst
        monthly_chunks: dict[str, List[pd.DataFrame]] = {}
        request_count = 0
        
        while current_to >= start_kst:
            request_count += 1
            
            # API 호출
            to_str = current_to.strftime("%Y-%m-%dT%H:%M:%S")
            candles = client.get_candles_minutes(market, unit=60, to=to_str, count=200)
            
            if not candles:
                break
            
            # DataFrame 변환
            new_df = process_candle_data(candles, market)
            
            if new_df.empty:
                break
            
            # 월별로 그룹화
            new_df["month_str"] = new_df["candle_time_kst"].dt.strftime("%Y-%m")
            for month_str, group_df in new_df.groupby("month_str"):
                if month_str not in monthly_chunks:
                    monthly_chunks[month_str] = []
                monthly_chunks[month_str].append(group_df.drop(columns=["month_str"]))
            
            # 다음 배치를 위한 타임스탬프 업데이트
            min_time = new_df["candle_time_kst"].min()
            current_to = min_time - timedelta(seconds=1)
            
            if min_time < start_kst:
                break
        
        # 월별로 저장
        saved_count = 0
        for month_str, chunks in monthly_chunks.items():
            # 월의 첫 날짜로 datetime 생성
            date_kst = datetime.strptime(month_str + "-01", "%Y-%m-%d")
            
            # 기존 데이터 로드
            existing_df = load_monthly_csv(RAW_DATA_PATH_1H, market, date_kst)
            
            # 병합 및 저장
            new_df = pd.concat(chunks, ignore_index=True)
            combined_df, meta = merge_monthly_data(existing_df, new_df, RAW_DATA_PATH_1H, META_ROOT, market, date_kst)
            
            # 미수집 로깅 (날짜별로)
            missing_hours_list = meta.get("missing_hours", [])
            if missing_hours_list:
                for missing_info in missing_hours_list:
                    date_str = missing_info.get("date", "")
                    hours = missing_info.get("hours", [])
                    if hours:
                        try:
                            date_kst_for_log = datetime.strptime(date_str, "%Y-%m-%d")
                            log_missing_summary(
                                META_DATA_PATH,
                                market,
                                date_kst_for_log,
                                hours,
                                meta.get("rows_saved", 0),
                            )
                        except:
                            pass
            
            saved_count += 1
        
        console.print(f"[green]  {market}: {saved_count}개월 저장 완료[/green]")
