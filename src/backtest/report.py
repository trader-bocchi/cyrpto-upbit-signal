"""백테스팅 리포트 생성"""
import pandas as pd
from pathlib import Path
from typing import Dict, List
from rich.console import Console
from rich.table import Table

from src.config import RESULTS_PATH, BACKTEST_INITIAL_CASH

console = Console()


def generate_monthly_report(trades_df: pd.DataFrame, portfolio_history: List[Dict]) -> pd.DataFrame:
    """월별 성과 리포트 생성"""
    if trades_df.empty:
        return pd.DataFrame()
    
    trades_df["entry_time_kst"] = pd.to_datetime(trades_df["entry_time_kst"])
    trades_df["exit_time_kst"] = pd.to_datetime(trades_df["exit_time_kst"])
    
    # 월별 그룹화
    monthly_data = []
    
    for year_month, group in trades_df.groupby(trades_df["entry_time_kst"].dt.to_period("M")):
        # 월초/월말 자산 계산 (간소화: 거래 기반 추정)
        month_start = group["entry_time_kst"].min()
        month_end = group["exit_time_kst"].max() if "exit_time_kst" in group.columns else group["entry_time_kst"].max()
        
        # BUY/SELL 횟수
        buy_count = len(group[group["side"] == "BUY"])
        sell_count = len(group[group["side"] == "SELL"])
        
        # 월별 수익률 (간소화: 거래 수익률 합계)
        pnl_sum = group["pnl_pct"].sum() if "pnl_pct" in group.columns else 0
        
        monthly_data.append({
            "year_month": str(year_month),
            "month_start": month_start,
            "month_end": month_end,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_trades": buy_count + sell_count,
            "pnl_sum_pct": pnl_sum,
        })
    
    return pd.DataFrame(monthly_data)


def save_backtest_results(
    result: Dict,
    timeframe: str,
    period_label: str,
    trades_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
):
    """백테스팅 결과 저장"""
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    
    # 거래 내역 저장
    trades_file = RESULTS_PATH / f"trades_{period_label}_{timeframe}.csv"
    trades_df.to_csv(trades_file, index=False, encoding="utf-8-sig")
    console.print(f"[green]거래 내역 저장: {trades_file}[/green]")
    
    # 월별 리포트 저장
    monthly_file = RESULTS_PATH / f"portfolio_monthly_{period_label}_{timeframe}.csv"
    monthly_df.to_csv(monthly_file, index=False, encoding="utf-8-sig")
    console.print(f"[green]월별 리포트 저장: {monthly_file}[/green]")
    
    # 콘솔 요약 출력
    console.print(f"\n[bold cyan]=== 백테스팅 결과 ({timeframe}, {period_label}) ===[/bold cyan]")
    console.print(f"초기 자본: {result['initial_cash']:,.0f} KRW")
    console.print(f"최종 자산: {result['final_value']:,.0f} KRW")
    console.print(f"총 수익률: {result['total_return_pct']:+.2f}%")
    console.print(f"총 거래 횟수: {len(result['trades'])}회")
    
    if not monthly_df.empty:
        table = Table(title="월별 성과")
        table.add_column("월")
        table.add_column("매수")
        table.add_column("매도")
        table.add_column("총 거래")
        table.add_column("수익률(%)")
        
        for _, row in monthly_df.iterrows():
            table.add_row(
                str(row["year_month"]),
                str(int(row["buy_count"])),
                str(int(row["sell_count"])),
                str(int(row["total_trades"])),
                f"{row['pnl_sum_pct']:+.2f}",
            )
        
        console.print(table)

