"""SMI 데이터 검증 스크립트 - 마지막 값이 음수이면서 시그널 조건에 맞는지 확인"""
import pandas as pd
from pathlib import Path
from rich.console import Console
from rich.table import Table

from src.config import DERIVED_DATA_PATH, SMI_LOCAL_MIN_WINDOW, SMI_REQUIRE_NEGATIVE_PIVOT, SIGNAL_ENABLE_SMA50_FILTER
from src.storage.smi_store import load_smi
from src.storage.csv_store import get_candle_filepath, read_csv_safe
from src.indicators.extrema import find_pivot_min
from src.indicators.moving_averages import calculate_sma

console = Console()


def verify_signal_condition(market: str, timeframe: str) -> dict:
    """
    특정 마켓의 마지막 SMI 값이 음수이면서 시그널 조건에 맞는지 확인
    
    Returns:
        검증 결과 딕셔너리
    """
    # SMI 데이터 로드
    smi_df = load_smi(market, timeframe, year=None)
    
    if smi_df.empty:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": False,
            "last_smi": None,
            "signal_eligible": False,
            "reason": "SMI 데이터 없음"
        }
    
    # 마지막 SMI 값 확인
    smi_df = smi_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    last_smi = smi_df.iloc[-1]["smi_momentum"]
    
    if pd.isna(last_smi):
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": None,
            "signal_eligible": False,
            "reason": "마지막 SMI 값이 NaN"
        }
    
    # 마지막 값이 양수이면 시그널 조건 불만족
    if last_smi >= 0:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": f"마지막 SMI 값이 양수 또는 0: {last_smi:.4f}"
        }
    
    # 캔들 데이터 로드 (SMA 계산 및 시그널 조건 확인용)
    filepath = get_candle_filepath(DERIVED_DATA_PATH, market, timeframe, year=2025)
    candles_df = read_csv_safe(filepath)
    
    if candles_df.empty:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": "캔들 데이터 없음"
        }
    
    # 캔들 데이터와 SMI 병합
    candles_df["candle_time_kst"] = pd.to_datetime(candles_df["candle_time_kst"])
    candles_df = candles_df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)
    
    # SMI 병합
    smi_df["candle_time_kst"] = pd.to_datetime(smi_df["candle_time_kst"])
    merged_df = candles_df.merge(
        smi_df[["candle_time_kst", "smi_momentum"]],
        on="candle_time_kst",
        how="left"
    )
    
    if merged_df.empty or len(merged_df) < SMI_LOCAL_MIN_WINDOW + 3:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": f"데이터 부족 (필요: {SMI_LOCAL_MIN_WINDOW + 3}, 실제: {len(merged_df)})"
        }
    
    # 이동평균 계산
    merged_df = calculate_sma(merged_df, periods=[50, 200])
    
    # 피벗 찾기
    pivot_info = find_pivot_min(
        merged_df["smi_momentum"],
        window=SMI_LOCAL_MIN_WINDOW,
        require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
    )
    merged_df = pd.concat([merged_df, pivot_info], axis=1)
    
    # 마지막 행 인덱스
    i = len(merged_df) - 1
    
    # 마지막 행의 SMI 값 (m[i+2])
    m_i2 = merged_df.iloc[i]["smi_momentum"]
    
    if pd.isna(m_i2):
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": "마지막 행 SMI 값이 NaN"
        }
    
    # 피벗 정보 확인
    pivot_info_row = merged_df.iloc[i]
    pivot_idx_loc = int(pivot_info_row["pivot_idx"])
    
    if pivot_idx_loc < 0:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": "피벗이 없음"
        }
    
    # 피벗이 정확히 2칸 이전에 있어야 함
    if pivot_idx_loc != i - 2:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": f"피벗 위치 불일치 (피벗: {pivot_idx_loc}, 필요: {i - 2})"
        }
    
    # 피벗 시점의 SMI 값 (m[i])
    m_i = merged_df.iloc[pivot_idx_loc]["smi_momentum"]
    if pd.isna(m_i):
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": "피벗 SMI 값이 NaN"
        }
    
    # m[i+1] 확인
    m_i1 = merged_df.iloc[pivot_idx_loc + 1]["smi_momentum"]
    if pd.isna(m_i1):
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": "m[i+1] SMI 값이 NaN"
        }
    
    # 2단계 회복 조건: m[i+2] > m[i+1] > m[i]
    if not (m_i2 > m_i1 > m_i):
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": f"2단계 회복 조건 불만족 (m[i]={m_i:.4f}, m[i+1]={m_i1:.4f}, m[i+2]={m_i2:.4f})"
        }
    
    # SMI_REQUIRE_NEGATIVE_PIVOT 체크
    if SMI_REQUIRE_NEGATIVE_PIVOT and m_i >= 0:
        return {
            "market": market,
            "timeframe": timeframe,
            "has_data": True,
            "last_smi": float(last_smi),
            "signal_eligible": False,
            "reason": f"피벗이 음수가 아님: {m_i:.4f}"
        }
    
    # SMA50 필터 확인
    signal_row = merged_df.iloc[i]
    if SIGNAL_ENABLE_SMA50_FILTER:
        if pd.isna(signal_row["sma_50"]) or signal_row["close"] <= signal_row["sma_50"]:
            return {
                "market": market,
                "timeframe": timeframe,
                "has_data": True,
                "last_smi": float(last_smi),
                "signal_eligible": False,
                "reason": f"SMA50 필터 실패 (close={signal_row['close']:.0f}, sma50={signal_row['sma_50']:.0f if not pd.isna(signal_row['sma_50']) else 'NaN'})"
            }
    
    # 모든 조건 통과
    return {
        "market": market,
        "timeframe": timeframe,
        "has_data": True,
        "last_smi": float(last_smi),
        "signal_eligible": True,
        "reason": "시그널 조건 만족",
        "m_i": float(m_i),
        "m_i1": float(m_i1),
        "m_i2": float(m_i2),
        "pivot_idx": pivot_idx_loc,
    }


def main():
    """모든 마켓의 SMI 데이터 검증"""
    console.print("[cyan]SMI 데이터 검증 시작...[/cyan]")
    
    # 모든 마켓 조회
    console.print("[cyan]마켓 목록 수집 중...[/cyan]")
    markets = []
    for timeframe in ["4h", "1d"]:
        tf_path = DERIVED_DATA_PATH / "indicators" / f"smi_{timeframe}"
        if tf_path.exists():
            try:
                for market_dir in tf_path.iterdir():
                    if market_dir.is_dir() and market_dir.name.startswith("market="):
                        market = market_dir.name.replace("market=", "")
                        if market not in markets:
                            markets.append(market)
            except Exception as e:
                console.print(f"[red]마켓 목록 수집 중 오류 ({timeframe}): {e}[/red]")
    
    console.print(f"[green]검증 대상: {len(markets)}개 마켓[/green]")
    
    if not markets:
        console.print("[yellow]검증할 마켓이 없습니다.[/yellow]")
        return
    
    results = []
    negative_last_smi_count = 0
    signal_eligible_count = 0
    
    # 진행 상황 표시를 위한 카운터
    total_tasks = len(markets) * 2  # 4h, 1d 각각
    processed = 0
    
    for market in markets:
        for timeframe in ["4h", "1d"]:
            processed += 1
            console.print(f"[dim][{processed}/{total_tasks}] {market} [{timeframe}] 검증 중...[/dim]")
            
            try:
                result = verify_signal_condition(market, timeframe)
                results.append(result)
                
                if result["last_smi"] is not None and result["last_smi"] < 0:
                    negative_last_smi_count += 1
                    
                    if result["signal_eligible"]:
                        signal_eligible_count += 1
                        console.print(f"[green]✅ {market} [{timeframe}]: 시그널 조건 만족! (마지막 SMI: {result['last_smi']:.4f})[/green]")
                        console.print(f"   m[i]={result.get('m_i', 0):.4f}, m[i+1]={result.get('m_i1', 0):.4f}, m[i+2]={result.get('m_i2', 0):.4f}, pivot_idx={result.get('pivot_idx', -1)}")
            except Exception as e:
                console.print(f"[red]오류 ({market} [{timeframe}]): {e}[/red]")
                results.append({
                    "market": market,
                    "timeframe": timeframe,
                    "has_data": False,
                    "last_smi": None,
                    "signal_eligible": False,
                    "reason": f"오류: {str(e)}"
                })
    
    # 결과 요약
    console.print("\n[bold cyan]검증 결과 요약[/bold cyan]")
    console.print(f"전체 마켓: {len(markets)}개")
    console.print(f"마지막 SMI 값이 음수인 경우: {negative_last_smi_count}개")
    console.print(f"시그널 조건 만족: {signal_eligible_count}개")
    
    # 시그널 조건 만족하는 마켓 목록
    eligible_markets = [r for r in results if r["signal_eligible"]]
    
    if eligible_markets:
        console.print("\n[bold green]시그널 조건 만족 마켓:[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("마켓", style="cyan")
        table.add_column("시간프레임", style="yellow")
        table.add_column("마지막 SMI", style="green")
        table.add_column("m[i]", style="blue")
        table.add_column("m[i+1]", style="blue")
        table.add_column("m[i+2]", style="blue")
        
        for r in eligible_markets:
            table.add_row(
                r["market"],
                r["timeframe"],
                f"{r['last_smi']:.4f}",
                f"{r.get('m_i', 0):.4f}",
                f"{r.get('m_i1', 0):.4f}",
                f"{r.get('m_i2', 0):.4f}"
            )
        
        console.print(table)
    else:
        console.print("\n[bold red]시그널 조건을 만족하는 마켓이 없습니다.[/bold red]")
        
        # 마지막 값이 음수인데 시그널 조건을 만족하지 않는 경우 분석
        negative_but_not_eligible = [r for r in results if r["last_smi"] is not None and r["last_smi"] < 0 and not r["signal_eligible"]]
        
        if negative_but_not_eligible:
            console.print(f"\n[bold yellow]마지막 SMI 값이 음수이지만 시그널 조건을 만족하지 않는 경우: {len(negative_but_not_eligible)}개[/bold yellow]")
            
            # 이유별 그룹화
            reason_counts = {}
            for r in negative_but_not_eligible:
                reason = r["reason"]
                if reason not in reason_counts:
                    reason_counts[reason] = []
                reason_counts[reason].append(f"{r['market']} [{r['timeframe']}]")
            
            for reason, markets_list in reason_counts.items():
                console.print(f"\n[cyan]{reason}:[/cyan] {len(markets_list)}개")
                if len(markets_list) <= 10:
                    for m in markets_list:
                        console.print(f"  - {m}")
                else:
                    for m in markets_list[:10]:
                        console.print(f"  - {m}")
                    console.print(f"  ... 외 {len(markets_list) - 10}개")


if __name__ == "__main__":
    main()