"""캔들 차트와 SMI 지표를 함께 그리는 스크립트"""
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from rich.console import Console

from src.config import DERIVED_DATA_PATH
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.storage.smi_store import load_smi, merge_smi_with_candles

console = Console()


def load_market_data(market: str, timeframe: str, days: int = 30) -> pd.DataFrame:
    """
    마켓의 캔들 데이터와 SMI 데이터를 로드하고 병합
    
    Args:
        market: 마켓 코드 (예: KRW-BTC)
        timeframe: 시간프레임 (4h, 1d)
        days: 최근 N일 데이터 로드
    
    Returns:
        병합된 DataFrame
    """
    # 시간프레임 정규화
    timeframe_norm = timeframe.strip().lower()
    if timeframe_norm == "1" or timeframe_norm == "24h" or timeframe_norm == "d":
        timeframe_norm = "1d"
    
    # 캔들 데이터 로드 (모든 연도)
    base_path = DERIVED_DATA_PATH / f"candles_{timeframe_norm}" / f"market={market}"
    
    if not base_path.exists():
        console.print(f"[red]캔들 데이터가 없습니다: {market} {timeframe_norm}[/red]")
        return pd.DataFrame()
    
    # 모든 연도 파일 찾기
    all_candle_dfs = []
    for file in base_path.glob("*.csv"):
        df = read_csv_safe(file)
        if not df.empty:
            all_candle_dfs.append(df)
    
    if not all_candle_dfs:
        console.print(f"[red]캔들 데이터가 없습니다: {market} {timeframe_norm}[/red]")
        return pd.DataFrame()
    
    # 모든 데이터 병합
    combined_df = pd.concat(all_candle_dfs, ignore_index=True)
    combined_df["candle_time_kst"] = pd.to_datetime(combined_df["candle_time_kst"])
    combined_df = combined_df.drop_duplicates(subset=["candle_time_kst"], keep="last")
    combined_df = combined_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # 최근 N일 데이터만 필터링
    if days > 0:
        cutoff_date = combined_df["candle_time_kst"].max() - timedelta(days=days)
        combined_df = combined_df[combined_df["candle_time_kst"] >= cutoff_date]
    
    # SMI 데이터 로드 및 병합
    smi_df = load_smi(market, timeframe_norm, year=None)
    if not smi_df.empty:
        combined_df = merge_smi_with_candles(combined_df, smi_df)
    
    return combined_df


def plot_candle_smi_chart(df: pd.DataFrame, market: str, timeframe: str, output_path: Optional[Path] = None):
    """
    캔들 차트와 SMI 지표를 함께 그리기
    
    Args:
        df: OHLCV + SMI DataFrame
        market: 마켓 코드
        timeframe: 시간프레임
        output_path: 저장 경로 (None이면 화면에 표시)
    """
    if df.empty:
        console.print(f"[red]데이터가 없습니다: {market} {timeframe}[/red]")
        return
    
    # 시간을 인덱스로 설정
    df = df.copy()
    df.set_index("candle_time_kst", inplace=True)
    
    # 차트 생성 (2개 서브플롯: 가격 라인 차트, SMI)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), height_ratios=[3, 1])
    fig.suptitle(f"{market} {timeframe.upper()} - 가격 & SMI", fontsize=16, fontweight='bold')
    
    # 1. 가격 라인 차트 (위) - line chart
    ax1.plot(df.index, df['close'], color='#2196F3', linewidth=1.5, label='Close Price')
    ax1.set_ylabel("가격 (KRW)", fontsize=12)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    # 2. SMI 지표 (아래) - TradingView 색상의 bar chart
    if "smi_momentum" in df.columns:
        smi_values = df["smi_momentum"]
        
        # TradingView 색상: val > 0이면 lime/green, val < 0이면 red/maroon
        # 이전 값과 비교하여 색상 결정
        smi_prev = smi_values.shift(1)
        
        # 색상 결정 (TradingView와 동일)
        colors = []
        for i in range(len(smi_values)):
            val = smi_values.iloc[i]
            prev_val = smi_prev.iloc[i] if not pd.isna(smi_prev.iloc[i]) else 0
            
            if pd.isna(val):
                colors.append('#808080')  # gray for NaN
            elif val > 0:
                if val > prev_val:
                    colors.append('#00FF00')  # lime (밝은 초록)
                else:
                    colors.append('#008000')  # green (어두운 초록)
            else:  # val < 0
                if val < prev_val:
                    colors.append('#FF0000')  # red (밝은 빨강)
                else:
                    colors.append('#800000')  # maroon (어두운 빨강)
        
        # Bar chart로 그리기
        dates = mdates.date2num(df.index)
        width = (dates[1] - dates[0]) * 0.8 if len(dates) > 1 else 0.8
        
        ax2.bar(dates, smi_values, width=width, color=colors, edgecolor='none', alpha=0.8)
        
        # 0 라인 (TradingView 색상: noSqz=blue, sqzOn=black, sqzOff=gray)
        if "squeeze_on" in df.columns:
            squeeze_on = df["squeeze_on"]
            squeeze_off = df.get("squeeze_off", pd.Series(False, index=df.index))
            no_squeeze = ~squeeze_on & ~squeeze_off
            
            # 각 시점별로 0 라인 색상 결정
            for i, idx in enumerate(df.index):
                if squeeze_on.iloc[i]:
                    line_color = '#000000'  # black
                elif squeeze_off.iloc[i] if i < len(squeeze_off) else False:
                    line_color = '#808080'  # gray
                else:
                    line_color = '#0000FF'  # blue
                
                ax2.axvline(x=mdates.date2num(idx), ymin=0, ymax=0.02, 
                           color=line_color, linewidth=2, alpha=0.7)
        else:
            # squeeze_on 정보가 없으면 기본적으로 blue
            ax2.axhline(y=0, color='#0000FF', linestyle='-', linewidth=2, alpha=0.7)
        
        ax2.set_ylabel("SMI Momentum", fontsize=12)
        ax2.grid(True, alpha=0.3)
    else:
        ax2.text(0.5, 0.5, 'SMI 데이터 없음', 
                horizontalalignment='center', verticalalignment='center',
                transform=ax2.transAxes, fontsize=14)
    
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    plt.tight_layout()
    
    # 저장 또는 표시
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        console.print(f"[green]차트 저장 완료: {output_path}[/green]")
    else:
        plt.show()
    
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="캔들 차트와 SMI 지표를 함께 그리기")
    parser.add_argument("--market", type=str, required=True, help="마켓 코드 (예: KRW-BTC)")
    parser.add_argument("--timeframes", type=str, default="4h,1d", help="시간프레임 (쉼표 구분, 예: 4h,1d)")
    parser.add_argument("--days", type=int, default=30, help="최근 N일 데이터 (기본값: 30)")
    parser.add_argument("--output", type=str, help="저장 경로 (지정하지 않으면 화면에 표시)")
    
    args = parser.parse_args()
    
    # 시간프레임 파싱
    timeframes = []
    for part in args.timeframes.split(","):
        part = part.strip()
        if not part:
            continue
        # 정규화: "1", "24h", "d" -> "1d"
        if part == "1" or part == "24h" or part == "d":
            part = "1d"
        if part not in timeframes:
            timeframes.append(part)
    
    if not timeframes:
        timeframes = ["4h", "1d"]
    
    # 각 시간프레임별로 차트 생성
    for timeframe in timeframes:
        console.print(f"[cyan]{args.market} {timeframe} 데이터 로드 중...[/cyan]")
        df = load_market_data(args.market, timeframe, days=args.days)
        
        if df.empty:
            console.print(f"[yellow]  {timeframe}: 데이터 없음[/yellow]")
            continue
        
        console.print(f"[green]  {timeframe}: {len(df)}개 캔들 로드 완료[/green]")
        
        # 출력 경로 설정
        output_path = None
        if args.output:
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{args.market}_{timeframe}_{args.days}days.png"
        
        # 차트 그리기
        plot_candle_smi_chart(df, args.market, timeframe, output_path)
    
    console.print(f"\n[bold green]차트 생성 완료[/bold green]")


if __name__ == "__main__":
    main()

