"""SMI 지표 계산 및 저장"""
import pandas as pd
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from rich.progress import track

from src.config import DERIVED_DATA_PATH
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.storage.smi_store import save_smi
from src.indicators.squeeze_momentum import calculate_smi

console = Console()


def calculate_smi_for_markets(
    markets: Optional[List[str]] = None,
    timeframes: List[str] = ["4h", "1d"],
) -> None:
    """
    마켓별로 SMI 지표 계산 및 저장
    
    Args:
        markets: 마켓 리스트 (None이면 모든 마켓)
        timeframes: 시간프레임 리스트
    """
    # 마켓 목록 조회
    if markets is None:
        markets = []
        for timeframe in timeframes:
            tf_path = DERIVED_DATA_PATH / f"candles_{timeframe}"
            if tf_path.exists():
                for market_dir in tf_path.iterdir():
                    if market_dir.is_dir() and market_dir.name.startswith("market="):
                        market = market_dir.name.replace("market=", "")
                        if market not in markets:
                            markets.append(market)
        
        if not markets:
            console.print(f"[red]캔들 데이터가 없습니다. (경로: {DERIVED_DATA_PATH})[/red]")
            return
    
    console.print(f"[green]SMI 계산 대상: {len(markets)}개 마켓, {timeframes} 시간프레임[/green]")
    
    for market in track(markets, description="SMI 계산 중..."):
        for timeframe in timeframes:
            # 시간프레임 정규화
            timeframe_norm = timeframe.strip().lower()
            if timeframe_norm == "1":
                timeframe_norm = "1d"
            if timeframe_norm == "24h":
                timeframe_norm = "1d"
            
            # 캔들 데이터 로드
            filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe_norm, year=2025)
            df = read_csv_safe(filepath)
            
            if df.empty:
                console.print(f"[yellow]  {market} {timeframe_norm}: 캔들 데이터 없음[/yellow]")
                continue
            
            # SMI 계산
            try:
                smi_df = calculate_smi(df.copy())
                save_smi(smi_df, market, timeframe_norm, year=2025)
                console.print(f"[green]  {market} {timeframe_norm}: SMI 계산 완료 ({len(smi_df)}개)[/green]")
            except Exception as e:
                console.print(f"[red]  {market} {timeframe_norm}: SMI 계산 실패 - {e}[/red]")
    
    console.print(f"\n[bold green]SMI 계산 완료[/bold green]")

