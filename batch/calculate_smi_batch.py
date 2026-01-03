"""배치용 SMI 계산 및 캔들 데이터와 결합
참조: python -m src.cli calculate-smi --timeframes 4h,1d
"""
import pandas as pd
from typing import Dict
from rich.console import Console
from rich.progress import track

# 참조 명령어의 로직을 최대한 준수하기 위해 동일한 함수 사용
from src.indicators.squeeze_momentum import calculate_smi

console = Console()


def calculate_smi_for_batch_data(
    market_data: Dict[str, Dict[str, pd.DataFrame]],
    timeframes: list = ["4h", "1d"],
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    배치 데이터에 SMI 계산 및 결합
    참조: src/pipeline/calculate_smi.py의 calculate_smi_for_markets 함수
    
    Args:
        market_data: {market: {timeframe: DataFrame}} 형태의 딕셔너리
        timeframes: 시간프레임 리스트
    
    Returns:
        SMI가 계산된 {market: {timeframe: DataFrame}} 형태의 딕셔너리
    """
    console.print(f"[green]SMI 계산 및 캔들 데이터 결합 시작... ({len(market_data)}개 마켓)[/green]")
    
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    
    # 참조 함수와 동일하게 track 사용
    for market in track(market_data.keys(), description="SMI 계산 중..."):
        result[market] = {}
        
        for timeframe in timeframes:
            if timeframe not in market_data[market]:
                continue
            
            df = market_data[market][timeframe].copy()
            
            if df.empty:
                result[market][timeframe] = df
                continue
            
            # 참조 함수와 동일하게 SMI 계산
            try:
                # 참조: src/pipeline/calculate_smi.py의 calculate_smi 호출
                smi_df = calculate_smi(df)
                result[market][timeframe] = smi_df
            except Exception as e:
                console.print(f"[red]  {market} {timeframe}: SMI 계산 실패 - {e}[/red]")
                result[market][timeframe] = df
    
    console.print(f"\n[bold green]SMI 계산 완료[/bold green]")
    return result

