"""4h/1d 캔들 직접 수집 (Upbit API에서 직접 가져오기)"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from rich.console import Console

from src.config import RAW_DATA_PATH
from src.upbit_client_fast import FastUpbitClient
from src.storage.csv_store import (
    read_csv_safe,
    atomic_write_csv,
    ensure_candle_dtypes,
    get_candle_filepath,
)
from src.storage.dedup import dedup_candles

console = Console()


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


def get_last_collected_time(market: str, timeframe: str) -> Optional[datetime]:
    """마켓별 마지막 수집 시각 조회"""
    filepath = get_candle_filepath(RAW_DATA_PATH, market, timeframe, year=2025)
    df = read_csv_safe(filepath)
    
    if df.empty or "candle_time_kst" not in df.columns:
        return None
    
    df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
    return df["candle_time_kst"].max()


def fetch_timeframe_direct(
    market: str,
    timeframe: str,
    start_kst: Optional[datetime] = None,
    end_kst: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    특정 시간프레임 캔들 직접 수집
    
    Args:
        market: 마켓 코드
        timeframe: "4h" 또는 "1d"
        start_kst: 시작 시각 (None이면 마지막 수집 시각 이후)
        end_kst: 종료 시각 (None이면 현재)
    """
    # 1d 캔들 수집 시 더 긴 타임아웃 사용
    timeout = 30 if timeframe == "1d" else 20
    client = FastUpbitClient(base_sleep=0.1, request_timeout=timeout)
    
    # 시간프레임별 unit 설정
    if timeframe == "4h":
        unit = 240  # 4시간 = 240분
    elif timeframe == "1d":
        unit = 1440  # 1일 = 1440분
    else:
        raise ValueError(f"지원하지 않는 시간프레임: {timeframe}")
    
    # 시작/종료 시각 결정
    if end_kst is None:
        end_kst = datetime.now()
    
    if start_kst is None:
        last_time = get_last_collected_time(market, timeframe)
        if last_time:
            start_kst = last_time + timedelta(hours=4 if timeframe == "4h" else 24)
        else:
            # 데이터가 없으면 최근 30일치 수집
            start_kst = end_kst - timedelta(days=30)
    
    console.print(f"  [{timeframe}] {start_kst.strftime('%Y-%m-%d %H:%M')} ~ {end_kst.strftime('%Y-%m-%d %H:%M')}")
    
    # 수집 루프
    current_to = end_kst
    all_dfs = []
    
    # 무한 루프 방지
    last_min_time = None
    same_time_count = 0
    MAX_SAME_TIME_COUNT = 3
    max_iterations = 1000  # 최대 반복 횟수 제한
    iteration_count = 0
    
    while current_to >= start_kst and iteration_count < max_iterations:
        iteration_count += 1
        
        try:
            # API 호출
            # 1d 캔들도 ISO8601 형식(YYYY-MM-DDTHH:MM:SS) 사용
            # 참고: https://api.upbit.com/v1/candles/days?market=KRW-WAXP&count=200&to=2026-01-04T00:00:00
            to_str = current_to.strftime("%Y-%m-%dT%H:%M:%S")
            console.print(f"    [API 호출 {iteration_count}] {to_str}")
            candles = client.get_candles_minutes(market, unit=unit, to=to_str, count=200)
            
            if not candles or len(candles) == 0:
                console.print(f"    [완료] 더 이상 데이터 없음")
                break
            
            # DataFrame 변환
            df = process_candle_data(candles, market)
            
            if df.empty:
                console.print(f"    [완료] 변환된 데이터 없음")
                break
            
            # 최소 시간 확인
            min_time = df["candle_time_kst"].min()
            
            # 무한 루프 방지
            if last_min_time is not None:
                if min_time >= last_min_time:
                    same_time_count += 1
                    if same_time_count >= MAX_SAME_TIME_COUNT:
                        console.print(f"    [완료] 같은 시간대 반복 ({same_time_count}회), 수집 중단")
                        break
                else:
                    same_time_count = 0
            
            last_min_time = min_time
            all_dfs.append(df)
            
            console.print(f"    [진행] {len(df)}개 캔들 수집, 총 {len(all_dfs)}회 API 호출")
            
            # 다음 배치
            # 1d 캔들은 하루씩 이동
            if timeframe == "1d":
                current_to = min_time - timedelta(days=1)
            else:
                current_to = min_time - timedelta(seconds=1)
            
            if min_time < start_kst:
                break
                
        except Exception as e:
            console.print(f"    [오류] API 호출 실패: {e}")
            import traceback
            console.print(f"[red]{traceback.format_exc()}[/red]")
            break
    
    if iteration_count >= max_iterations:
        console.print(f"    [경고] 최대 반복 횟수({max_iterations}) 도달, 수집 중단")
    
    if not all_dfs:
        return pd.DataFrame()
    
    combined_df = pd.concat(all_dfs, ignore_index=True)
    return combined_df


def fetch_4h_1d_direct(markets: Optional[List[str]] = None, timeframes: List[str] = ["4h", "1d"]):
    """
    4h/1d 캔들 직접 수집 (Upbit API에서 직접)
    
    Args:
        markets: 마켓 리스트 (None이면 모든 KRW 마켓)
        timeframes: 시간프레임 리스트 (["4h", "1d"])
    """
    client = FastUpbitClient(base_sleep=0.1)
    
    # 마켓 목록 조회 (KRW 마켓만)
    if markets is None:
        all_markets_data = client.get_markets()
        markets = [m["market"] for m in all_markets_data if m.get("market", "").startswith("KRW-")]
    
    console.print(f"[green]4h/1d 직접 수집 대상: {len(markets)}개 마켓[/green]")
    
    for market in markets:
        console.print(f"\n[cyan]{market}[/cyan]")
        
        for timeframe in timeframes:
            try:
                # 수집
                new_df = fetch_timeframe_direct(market, timeframe)
                
                if new_df.empty:
                    console.print(f"  [{timeframe}] 데이터 없음")
                    continue
                
                # 기존 데이터와 병합
                filepath = get_candle_filepath(RAW_DATA_PATH, market, timeframe, year=2025)
                existing_df = read_csv_safe(filepath)
                
                if not existing_df.empty:
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                else:
                    combined_df = new_df
                
                # 중복 제거 및 정렬
                combined_df = dedup_candles(combined_df)
                combined_df = ensure_candle_dtypes(combined_df)
                combined_df = combined_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
                
                # 저장
                atomic_write_csv(combined_df, filepath)
                
                console.print(f"  [{timeframe}] {len(new_df)}개 수집, 총 {len(combined_df)}개 저장")
                
            except Exception as e:
                console.print(f"  [{timeframe}] 오류: {e}")
    
    console.print("\n[green]4h/1d 직접 수집 완료[/green]")

