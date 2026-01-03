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
            
            # 모든 연도 캔들 데이터 로드
            base_path = DERIVED_DATA_PATH / f"candles_{timeframe_norm}" / f"market={market}"
            
            if not base_path.exists():
                console.print(f"[yellow]  {market} {timeframe_norm}: 캔들 데이터 디렉토리 없음[/yellow]")
                continue
            
            # 모든 연도 파일 찾기
            all_candle_dfs = []
            if timeframe_norm in ["4h", "1d"]:
                # 4h, 1d는 year 디렉토리 없이 직접 파일
                for file in base_path.glob("*.csv"):
                    df = read_csv_safe(file)
                    if not df.empty:
                        all_candle_dfs.append(df)
            else:
                # 1h는 year 디렉토리 구조
                for year_dir in base_path.glob("year=*"):
                    year_file = year_dir / f"{year_dir.name.replace('year=', '')}.csv"
                    if year_file.exists():
                        df = read_csv_safe(year_file)
                        if not df.empty:
                            all_candle_dfs.append(df)
            
            if not all_candle_dfs:
                console.print(f"[yellow]  {market} {timeframe_norm}: 캔들 데이터 없음[/yellow]")
                continue
            
            # 모든 데이터 병합
            combined_df = pd.concat(all_candle_dfs, ignore_index=True)
            combined_df["candle_time_kst"] = pd.to_datetime(combined_df["candle_time_kst"])
            combined_df = combined_df.drop_duplicates(subset=["candle_time_kst"], keep="last")
            combined_df = combined_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
            
            if combined_df.empty:
                console.print(f"[yellow]  {market} {timeframe_norm}: 캔들 데이터 없음[/yellow]")
                continue
            
            # SMI 계산
            try:
                smi_df = calculate_smi(combined_df.copy())
                
                # 디버깅: SMI 값 통계
                total_count = len(smi_df)
                zero_count = (smi_df["smi_momentum"] == 0).sum()
                squeeze_on_count = smi_df.get("squeeze_on", pd.Series()).sum() if "squeeze_on" in smi_df.columns else 0
                non_zero_count = total_count - zero_count
                
                if zero_count > total_count * 0.8:  # 80% 이상이 0이면 경고
                    console.print(f"[yellow]  {market} {timeframe_norm}: 0값 비율 높음 ({zero_count}/{total_count}, {zero_count/total_count*100:.1f}%)[/yellow]")
                    console.print(f"[dim]    - Squeeze ON: {squeeze_on_count}개 ({squeeze_on_count/total_count*100:.1f}%)[/dim]")
                    console.print(f"[dim]    - Non-zero SMI: {non_zero_count}개[/dim]")
                    if non_zero_count > 0:
                        non_zero_values = smi_df[smi_df["smi_momentum"] != 0]["smi_momentum"]
                        console.print(f"[dim]    - Non-zero 범위: {non_zero_values.min():.6f} ~ {non_zero_values.max():.6f}[/dim]")
                
                # 연도별로 저장
                smi_df["year"] = pd.to_datetime(smi_df["candle_time_kst"]).dt.year
                for year in smi_df["year"].unique():
                    year_smi_df = smi_df[smi_df["year"] == year].drop(columns=["year"])
                    save_smi(year_smi_df, market, timeframe_norm, year=int(year))
                
                console.print(f"[green]  {market} {timeframe_norm}: SMI 계산 완료 ({len(smi_df)}개, {len(smi_df['year'].unique())}개 연도)[/green]")
            except Exception as e:
                console.print(f"[red]  {market} {timeframe_norm}: SMI 계산 실패 - {e}[/red]")
                import traceback
                console.print(f"[red]{traceback.format_exc()}[/red]")
    
    console.print(f"\n[bold green]SMI 계산 완료[/bold green]")

