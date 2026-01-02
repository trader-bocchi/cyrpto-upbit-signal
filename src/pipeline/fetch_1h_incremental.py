"""일일 증분 1시간 캔들 수집 (월 단위 저장)"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from rich.console import Console

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


def get_last_collected_time(market: str) -> Optional[datetime]:
    """마켓별 마지막 수집 시각 조회 (월 단위 파일에서)"""
    # 월 단위 파일들을 스캔하여 가장 최근 데이터의 최대 시각 찾기
    max_time = None
    
    # 2025년 1월부터 현재까지 월별로 확인
    current_date = datetime(2025, 1, 1)
    now = datetime.now()
    
    while current_date <= now:
        df = load_monthly_csv(RAW_DATA_PATH_1H, market, current_date)
        if not df.empty and "candle_time_kst" in df.columns:
            df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
            market_max = df["candle_time_kst"].max()
            if max_time is None or market_max > max_time:
                max_time = market_max
        
        # 다음 달로 이동
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)
    
    return max_time


def fetch_1h_incremental():
    """체크포인트 이후 구간만 수집 (월 단위 저장) + 4h/1d 집계"""
    client = FastUpbitClient(base_sleep=0.1)
    
    # 모든 KRW 마켓 조회
    all_markets_data = client.get_markets()
    markets = [m["market"] for m in all_markets_data]
    
    console.print(f"[green]증분 수집 대상: {len(markets)}개 마켓[/green]")
    
    # 현재 시각 (KST)
    now_kst = datetime.now()
    end_kst = now_kst.replace(hour=23, minute=59, second=59)
    
    updated_markets = []
    
    for market in markets:
        console.print(f"\n[cyan]{market}[/cyan]")
        
        # 마지막 수집 시각 확인
        last_time = get_last_collected_time(market)
        if last_time:
            # 마지막 수집 시각 이후부터 수집 (1시간 추가 안전 마진)
            start_kst = last_time + timedelta(hours=1)
            console.print(f"  [재개] 마지막 수집: {last_time.strftime('%Y-%m-%d %H:%M')}, 이후부터 수집")
        else:
            # 데이터가 없으면 최근 7일치 수집 (안전 마진)
            start_kst = (now_kst - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            console.print(f"  [신규] 최근 7일치 수집")
        
        # 수집 범위 체크
        if start_kst >= end_kst:
            console.print(f"  [스킵] 이미 최신 데이터")
            continue
        
        # 수집 루프
        current_to = end_kst
        monthly_chunks: dict[str, List[pd.DataFrame]] = {}
        
        # 무한 루프 방지
        last_min_time = None
        same_time_count = 0
        MAX_SAME_TIME_COUNT = 3
        
        while current_to >= start_kst:
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
            
            # 다음 배치
            min_time = new_df["candle_time_kst"].min()
            
            # 무한 루프 방지: 같은 시간대가 연속으로 나오는지 체크
            if last_min_time is not None:
                if min_time >= last_min_time:
                    same_time_count += 1
                    if same_time_count >= MAX_SAME_TIME_COUNT:
                        console.print(f"  [완료] 더 이상 과거 데이터 없음")
                        break
                else:
                    same_time_count = 0
            
            last_min_time = min_time
            current_to = min_time - timedelta(seconds=1)
            
            if min_time < start_kst:
                break
        
        # 월별로 저장
        saved_count = 0
        has_updates = False
        
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
            has_updates = True
        
        if has_updates:
            updated_markets.append(market)
            console.print(f"[green]  {market}: {saved_count}개월 업데이트 완료[/green]")
        else:
            console.print(f"[yellow]  {market}: 업데이트 없음[/yellow]")
    
    # 집계는 더 이상 자동 실행하지 않음 (fetch-daily --direct-4h-1d 사용)
    if updated_markets:
        console.print(f"\n[cyan]업데이트된 마켓: {len(updated_markets)}개[/cyan]")
        console.print("[yellow]힌트: 4h/1d 데이터가 필요하면 'python -m src.cli fetch-daily --direct-4h-1d'를 실행하세요.[/yellow]")
    else:
        console.print("[yellow]업데이트된 마켓이 없습니다.[/yellow]")
