"""배치 작업 메인 실행 스크립트"""
import sys
from pathlib import Path
from rich.console import Console

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from batch.fetch_data import fetch_recent_90days
from batch.calculate_smi_batch import calculate_smi_for_batch_data
from batch.run_signals_batch import run_signals_for_timeframe

console = Console()


def main():
    """배치 작업 메인 실행"""
    console.print("[bold cyan]배치 작업 시작[/bold cyan]\n")
    
    # 1. 데이터 수집 (최근 120일치, 메모리에만 저장)
    console.print("[bold]1단계: 데이터 수집 (최근 120일치)[/bold]")
    market_data = fetch_recent_90days(markets=None, timeframes=["4h", "1d"])
    
    if not market_data:
        console.print("[red]데이터 수집 실패. 배치 작업을 종료합니다.[/red]")
        return
    
    # 2. SMI 계산 및 캔들 데이터와 결합
    console.print("\n[bold]2단계: SMI 계산 및 캔들 데이터 결합[/bold]")
    market_data_with_smi = calculate_smi_for_batch_data(market_data, timeframes=["4h", "1d"])
    
    # 3. 시그널 실행 (4h, 1d 각각)
    console.print("\n[bold]3단계: 시그널 실행[/bold]")
    
    # 4h 시그널 실행
    console.print("\n[cyan]4h 시그널 실행[/cyan]")
    signals_4h, count_4h = run_signals_for_timeframe(
        market_data_with_smi,
        timeframe="4h",
        timeframes=["4h", "1d"]
    )
    
    # 1d 시그널 실행
    console.print("\n[cyan]1d 시그널 실행[/cyan]")
    signals_1d, count_1d = run_signals_for_timeframe(
        market_data_with_smi,
        timeframe="1d",
        timeframes=["4h", "1d"]
    )
    
    # 결과 요약
    console.print("\n[bold green]배치 작업 완료[/bold green]")
    console.print(f"  4h 시그널: {count_4h}개 전송")
    console.print(f"  1d 시그널: {count_1d}개 전송")


if __name__ == "__main__":
    main()

