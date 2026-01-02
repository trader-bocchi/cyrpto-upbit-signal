"""수집된 데이터 요약 및 상태 확인 유틸리티"""
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from rich.console import Console
from rich.table import Table

from src.config import RAW_DATA_PATH_1H, META_DATA_PATH
from src.storage.monthly_store import load_monthly_csv

console = Console()


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


def get_market_summary(market: str, data_root: Path) -> Dict:
    """마켓별 데이터 요약"""
    summary = {
        "market": market,
        "files_count": 0,
        "total_rows": 0,
        "date_range": None,
        "months": [],
    }
    
    # 월 단위 파일 스캔
    current_date = datetime(2025, 1, 1)
    end_date = datetime(2025, 12, 31)
    
    all_times = []
    
    while current_date <= end_date:
        df = load_monthly_csv(data_root, market, current_date)
        if not df.empty:
            summary["files_count"] += 1
            summary["total_rows"] += len(df)
            month_str = current_date.strftime("%Y-%m")
            summary["months"].append(month_str)
            
            if "candle_time_kst" in df.columns:
                df["candle_time_kst"] = pd.to_datetime(df["candle_time_kst"])
                all_times.extend(df["candle_time_kst"].tolist())
        
        # 다음 달로 이동
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)
    
    if all_times:
        summary["date_range"] = {
            "start": min(all_times),
            "end": max(all_times),
        }
    
    return summary


def print_data_summary(data_root: Optional[Path] = None):
    """수집된 데이터 요약 출력"""
    if data_root is None:
        data_root = RAW_DATA_PATH_1H
    
    console.print(f"\n[bold cyan]데이터 요약[/bold cyan]")
    console.print(f"경로: {data_root}")
    
    if not data_root.exists():
        console.print(f"[red]경로가 존재하지 않습니다: {data_root}[/red]")
        return
    
    # 마켓 목록 조회
    markets = get_markets_from_monthly_files(data_root)
    
    if not markets:
        console.print(f"[yellow]수집된 데이터가 없습니다.[/yellow]")
        console.print(f"[yellow]힌트: python scripts/collect_upbit_1h_2025.py를 실행하세요.[/yellow]")
        return
    
    console.print(f"\n[green]총 {len(markets)}개 마켓 발견[/green]\n")
    
    # 테이블 생성
    table = Table(title="마켓별 데이터 요약")
    table.add_column("마켓", style="cyan")
    table.add_column("파일 수", justify="right", style="magenta")
    table.add_column("총 행 수", justify="right", style="green")
    table.add_column("기간", style="yellow")
    table.add_column("월 수", justify="right", style="blue")
    
    for market in markets[:20]:  # 상위 20개만 표시
        summary = get_market_summary(market, data_root)
        
        date_range_str = "-"
        if summary["date_range"]:
            start = summary["date_range"]["start"]
            end = summary["date_range"]["end"]
            date_range_str = f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
        
        table.add_row(
            summary["market"],
            str(summary["files_count"]),
            f"{summary['total_rows']:,}",
            date_range_str,
            str(len(summary["months"])),
        )
    
    console.print(table)
    
    if len(markets) > 20:
        console.print(f"\n[yellow]... 외 {len(markets) - 20}개 마켓 더 있음[/yellow]")
    
    # 전체 통계
    total_files = sum(get_market_summary(m, data_root)["files_count"] for m in markets)
    total_rows = sum(get_market_summary(m, data_root)["total_rows"] for m in markets)
    
    console.print(f"\n[bold]전체 통계:[/bold]")
    console.print(f"  마켓 수: {len(markets)}개")
    console.print(f"  파일 수: {total_files}개")
    console.print(f"  총 행 수: {total_rows:,}개")


def check_aggregate_readiness(data_root: Optional[Path] = None) -> bool:
    """aggregate 명령 실행 준비 상태 확인"""
    if data_root is None:
        data_root = RAW_DATA_PATH_1H
    
    console.print(f"\n[bold cyan]Aggregate 준비 상태 확인[/bold cyan]")
    console.print(f"경로: {data_root}\n")
    
    if not data_root.exists():
        console.print(f"[red]❌ 경로가 존재하지 않습니다: {data_root}[/red]")
        return False
    
    markets = get_markets_from_monthly_files(data_root)
    
    if not markets:
        console.print(f"[red]❌ 수집된 데이터가 없습니다.[/red]")
        console.print(f"[yellow]힌트: python scripts/collect_upbit_1h_2025.py를 실행하세요.[/yellow]")
        return False
    
    console.print(f"[green]✅ {len(markets)}개 마켓의 데이터 발견[/green]")
    
    # 샘플 마켓 확인
    sample_market = markets[0]
    summary = get_market_summary(sample_market, data_root)
    
    if summary["total_rows"] == 0:
        console.print(f"[red]❌ 샘플 마켓({sample_market})에 데이터가 없습니다.[/red]")
        return False
    
    console.print(f"[green]✅ 샘플 마켓({sample_market}): {summary['total_rows']}개 행 확인[/green]")
    
    if summary["date_range"]:
        console.print(f"[green]✅ 날짜 범위: {summary['date_range']['start']} ~ {summary['date_range']['end']}[/green]")
    
    console.print(f"\n[bold green]✅ aggregate 명령 실행 준비 완료![/bold green]")
    console.print(f"[cyan]다음 명령 실행: python -m src.cli aggregate --timeframes 4h,1d[/cyan]")
    
    return True

