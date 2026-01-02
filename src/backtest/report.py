"""백테스팅 리포트 생성"""
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console
from rich.table import Table

from src.config import RESULTS_PATH, BACKTEST_INITIAL_CASH

console = Console()


def generate_monthly_report(
    trades_df: pd.DataFrame, 
    portfolio_history: List[Dict],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    stats: Optional[Dict] = None,
) -> pd.DataFrame:
    """월별 성과 리포트 생성"""
    if trades_df.empty:
        # 거래가 없어도 기간이 있으면 모든 월 포함
        if start_date and end_date:
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            all_months = pd.period_range(start=start_dt, end=end_dt, freq='M')
            monthly_data = []
            for year_month in all_months:
                monthly_data.append({
                    "year_month": str(year_month),
                    "month_start": None,
                    "month_end": None,
                    "buy_count": 0,
                    "sell_count": 0,
                    "total_trades": 0,
                    "pnl_sum_pct": 0.0,
                    "time_stop_count": 0,
                    "time_stop_avg_pnl_pct": 0.0,
                })
            return pd.DataFrame(monthly_data)
        return pd.DataFrame()
    
    # 필수 컬럼 확인 및 변환
    if "entry_time_kst" not in trades_df.columns:
        console.print("[red]경고: entry_time_kst 컬럼이 없습니다.[/red]")
        return pd.DataFrame()
    
    trades_df["entry_time_kst"] = pd.to_datetime(trades_df["entry_time_kst"])
    
    # exit_time_kst는 SELL 거래에만 있음 (BUY 거래에는 없을 수 있음)
    if "exit_time_kst" in trades_df.columns:
        trades_df["exit_time_kst"] = pd.to_datetime(trades_df["exit_time_kst"])
    else:
        # exit_time_kst가 없으면 entry_time_kst를 사용 (진행 중인 포지션)
        trades_df["exit_time_kst"] = trades_df["entry_time_kst"]
    
    # 월별 그룹화
    monthly_data = []
    
    # 모든 거래의 날짜 범위 확인
    all_entry_dates = trades_df["entry_time_kst"].dt.to_period("M")
    all_exit_dates = None
    if "exit_time_kst" in trades_df.columns:
        exit_times = pd.to_datetime(trades_df["exit_time_kst"])
        all_exit_dates = exit_times.dt.to_period("M")
    
    # 모든 월 수집 (entry와 exit 모두 고려)
    all_months = set(all_entry_dates.unique())
    if all_exit_dates is not None:
        all_months.update(all_exit_dates.dropna().unique())
    
    # 백테스팅 기간의 모든 월 포함 (거래가 없는 월도 포함)
    if start_date and end_date:
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        # Period 범위 생성 (월 단위)
        period_range = pd.period_range(start=start_dt.to_period('M'), end=end_dt.to_period('M'), freq='M')
        # Period 객체를 set에 추가
        for period in period_range:
            all_months.add(period)
    
    # 월별로 그룹화
    for year_month in sorted(all_months):
        # 해당 월에 진입한 거래
        entry_trades = trades_df[all_entry_dates == year_month]
        # 해당 월에 종료한 거래 (exit_time_kst 기준)
        if all_exit_dates is not None:
            exit_trades = trades_df[all_exit_dates == year_month]
        else:
            exit_trades = pd.DataFrame()
        
        # 월초/월말 자산 계산
        month_start = None
        month_end = None
        
        if not entry_trades.empty:
            month_start = entry_trades["entry_time_kst"].min()
        if not exit_trades.empty and "exit_time_kst" in exit_trades.columns:
            exit_dates = pd.to_datetime(exit_trades["exit_time_kst"])
            month_end = exit_dates.max()
        elif not entry_trades.empty:
            month_end = entry_trades["entry_time_kst"].max()
        
        # BUY/SELL 횟수
        buy_count = len(entry_trades[entry_trades["side"] == "BUY"])
        sell_count = len(exit_trades[exit_trades["side"] == "SELL"]) if not exit_trades.empty else 0
        
        # 월별 수익률 계산: 해당 월에 종료된 거래(SELL)의 수익률만 합계
        if not exit_trades.empty and "pnl_pct" in exit_trades.columns:
            sell_trades = exit_trades[exit_trades["side"] == "SELL"]
            if not sell_trades.empty:
                pnl_sum = sell_trades["pnl_pct"].sum()
            else:
                pnl_sum = 0.0
        else:
            pnl_sum = 0.0
        
        # TIME_STOP 통계
        time_stop_count = 0
        time_stop_avg_pnl = 0.0
        if not exit_trades.empty and "reason" in exit_trades.columns:
            time_stop_trades = exit_trades[exit_trades["reason"] == "TIME_STOP"]
            if not time_stop_trades.empty:
                time_stop_count = len(time_stop_trades)
                if "pnl_pct" in time_stop_trades.columns:
                    time_stop_avg_pnl = time_stop_trades["pnl_pct"].mean()
        
        monthly_data.append({
            "year_month": str(year_month),
            "month_start": month_start,
            "month_end": month_end,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_trades": buy_count + sell_count,
            "pnl_sum_pct": pnl_sum,
            "time_stop_count": time_stop_count,
            "time_stop_avg_pnl_pct": time_stop_avg_pnl,
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
    
    # 통계 출력
    if "stats" in result:
        stats = result["stats"]
        console.print(f"\n[bold yellow]=== 통계 ===[/bold yellow]")
        console.print(f"레짐 OFF 스킵: {stats.get('skipped_regime_off', 0)}회")
        console.print(f"최대 포지션 스킵: {stats.get('skipped_max_positions', 0)}회")
        console.print(f"최대 비중 스킵: {stats.get('skipped_max_weight', 0)}회")
        console.print(f"축소 진입: {stats.get('reduced_size_entries', 0)}회")
        console.print(f"타임스탑 청산: {stats.get('time_stop_count', 0)}회")
    
    if not monthly_df.empty:
        table = Table(title="월별 성과")
        table.add_column("월")
        table.add_column("매수")
        table.add_column("매도")
        table.add_column("총 거래")
        table.add_column("수익률(%)")
        table.add_column("타임스탑")
        table.add_column("타임스탑 평균 수익률(%)")
        
        for _, row in monthly_df.iterrows():
            table.add_row(
                str(row["year_month"]),
                str(int(row["buy_count"])),
                str(int(row["sell_count"])),
                str(int(row["total_trades"])),
                f"{row['pnl_sum_pct']:+.2f}",
                str(int(row.get("time_stop_count", 0))),
                f"{row.get('time_stop_avg_pnl_pct', 0.0):+.2f}",
            )
        
        console.print(table)

