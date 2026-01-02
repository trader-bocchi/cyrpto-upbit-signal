"""1d(24h) 집계 검증 유틸리티"""
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List
from rich.console import Console
from rich.table import Table

from src.config import RAW_DATA_PATH_1H, DERIVED_DATA_PATH
from src.storage.csv_store import read_csv_safe, get_candle_filepath
from src.storage.monthly_store import load_monthly_csv
from src.pipeline.aggregate_4h_1d import load_1h_data_for_market

console = Console()


def verify_1d_aggregation(market: str, sample_days: int = 5) -> Dict:
    """
    1d 집계가 24시간 단위로 제대로 되었는지 검증
    
    Args:
        market: 마켓 코드
        sample_days: 검증할 샘플 일수
    
    Returns:
        검증 결과 딕셔너리
    """
    result = {
        "market": market,
        "valid": False,
        "errors": [],
        "warnings": [],
        "sample_dates": [],
    }
    
    # 1d 집계 데이터 로드
    filepath_1d = get_candle_filepath(DERIVED_DATA_PATH, market, "1d", year=2025)
    df_1d = read_csv_safe(filepath_1d)
    
    if df_1d.empty:
        result["errors"].append("1d 집계 데이터가 없습니다")
        return result
    
    df_1d["candle_time_kst"] = pd.to_datetime(df_1d["candle_time_kst"])
    df_1d = df_1d.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # 1h 원본 데이터 로드
    df_1h = load_1h_data_for_market(RAW_DATA_PATH_1H, market)
    
    if df_1h.empty:
        result["errors"].append("1h 원본 데이터가 없습니다")
        return result
    
    df_1h["candle_time_kst"] = pd.to_datetime(df_1h["candle_time_kst"])
    df_1h = df_1h.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # 샘플 날짜 선택 (최근 N일)
    if len(df_1d) > 0:
        sample_dates = df_1d["candle_time_kst"].tail(sample_days).tolist()
    else:
        result["errors"].append("1d 데이터가 비어있습니다")
        return result
    
    # 각 날짜별 검증
    for date_1d in sample_dates:
        date_str = date_1d.strftime("%Y-%m-%d")
        
        # 해당 날짜의 1d 캔들
        candle_1d = df_1d[df_1d["candle_time_kst"].dt.date == date_1d.date()]
        
        if candle_1d.empty:
            result["warnings"].append(f"{date_str}: 1d 캔들이 없습니다")
            continue
        
        candle_1d = candle_1d.iloc[0]
        
        # 해당 날짜의 모든 1h 캔들 (00:00 ~ 23:59)
        start_time = date_1d.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = date_1d.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        candles_1h = df_1h[
            (df_1h["candle_time_kst"] >= start_time) &
            (df_1h["candle_time_kst"] <= end_time)
        ]
        
        if candles_1h.empty:
            result["warnings"].append(f"{date_str}: 해당 날짜의 1h 데이터가 없습니다")
            continue
        
        # 검증: 1d 집계가 1h 데이터와 일치하는지
        expected_open = candles_1h.iloc[0]["open"]
        expected_high = candles_1h["high"].max()
        expected_low = candles_1h["low"].min()
        expected_close = candles_1h.iloc[-1]["close"]
        expected_volume = candles_1h["volume"].sum()
        
        # 허용 오차 (부동소수점 오차)
        tolerance = 0.0001
        
        date_result = {
            "date": date_str,
            "1h_count": len(candles_1h),
            "valid": True,
            "checks": {},
        }
        
        # Open 검증
        if abs(candle_1d["open"] - expected_open) > tolerance:
            date_result["valid"] = False
            date_result["checks"]["open"] = {
                "expected": expected_open,
                "actual": candle_1d["open"],
                "diff": abs(candle_1d["open"] - expected_open),
            }
        
        # High 검증
        if abs(candle_1d["high"] - expected_high) > tolerance:
            date_result["valid"] = False
            date_result["checks"]["high"] = {
                "expected": expected_high,
                "actual": candle_1d["high"],
                "diff": abs(candle_1d["high"] - expected_high),
            }
        
        # Low 검증
        if abs(candle_1d["low"] - expected_low) > tolerance:
            date_result["valid"] = False
            date_result["checks"]["low"] = {
                "expected": expected_low,
                "actual": candle_1d["low"],
                "diff": abs(candle_1d["low"] - expected_low),
            }
        
        # Close 검증
        if abs(candle_1d["close"] - expected_close) > tolerance:
            date_result["valid"] = False
            date_result["checks"]["close"] = {
                "expected": expected_close,
                "actual": candle_1d["close"],
                "diff": abs(candle_1d["close"] - expected_close),
            }
        
        # Volume 검증
        if abs(candle_1d["volume"] - expected_volume) > tolerance:
            date_result["valid"] = False
            date_result["checks"]["volume"] = {
                "expected": expected_volume,
                "actual": candle_1d["volume"],
                "diff": abs(candle_1d["volume"] - expected_volume),
            }
        
        # 24시간 검증 (1h 캔들이 24개인지 확인)
        if len(candles_1h) < 24:
            date_result["warnings"] = f"1h 캔들이 24개 미만 ({len(candles_1h)}개)"
        
        date_result["checks"]["1h_count"] = len(candles_1h)
        date_result["checks"]["expected_24h"] = len(candles_1h) == 24
        
        result["sample_dates"].append(date_result)
        
        if not date_result["valid"]:
            result["errors"].append(f"{date_str}: 집계 불일치")
    
    # 전체 검증 결과
    if not result["errors"]:
        result["valid"] = True
    
    return result


def print_verification_report(markets: List[str] = None, sample_days: int = 5):
    """1d 집계 검증 리포트 출력"""
    if markets is None:
        from src.storage.data_summary import get_markets_from_monthly_files
        markets = get_markets_from_monthly_files(RAW_DATA_PATH_1H)
    
    if not markets:
        console.print("[red]검증할 마켓이 없습니다.[/red]")
        return
    
    console.print(f"\n[bold cyan]1d(24h) 집계 검증 리포트[/bold cyan]")
    console.print(f"검증 대상: {len(markets)}개 마켓, 샘플 일수: {sample_days}일\n")
    
    valid_count = 0
    invalid_count = 0
    
    for market in markets[:10]:  # 상위 10개만
        result = verify_1d_aggregation(market, sample_days)
        
        if result["valid"]:
            valid_count += 1
            console.print(f"[green]✅ {market}: 검증 통과[/green]")
            if result["sample_dates"]:
                sample = result["sample_dates"][0]
                console.print(f"   샘플: {sample['date']}, 1h 캔들: {sample['1h_count']}개")
        else:
            invalid_count += 1
            console.print(f"[red]❌ {market}: 검증 실패[/red]")
            for error in result["errors"][:3]:
                console.print(f"   {error}")
            if result["sample_dates"]:
                sample = result["sample_dates"][0]
                if not sample["valid"]:
                    console.print(f"   불일치 항목: {list(sample['checks'].keys())}")
    
    console.print(f"\n[bold]검증 결과:[/bold]")
    console.print(f"  ✅ 통과: {valid_count}개")
    console.print(f"  ❌ 실패: {invalid_count}개")

