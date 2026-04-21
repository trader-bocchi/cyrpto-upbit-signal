"""SMI 시그널 감지 로직 (업비트/바이낸스 공통)

매수 시그널 조건 (Squeeze Momentum Index 기반):
  1) 마지막 SMI 값이 음수 (하강 구간에서 회복 중)
  2) 최저 로컬 미니멈(pivot)이 현재 바 기준 정확히 2칸 전(i-2)
  3) m[i-2] < m[i-1] < m[i] 연속 상승 (파동 저점에서 2단계 회복)
  4) SMI_REQUIRE_NEGATIVE_PIVOT=True 이면 pivot 값이 음수여야 함
  5) SIGNAL_ENABLE_SMA50_FILTER=True 이면 close > SMA50 조건 필요

매도 시그널 조건 (매수의 정반대):
  1) 마지막 SMI 값이 양수 (상승 구간에서 하락 시작)
  2) 최고 로컬 맥시멈(pivot)이 현재 바 기준 정확히 2칸 전(i-2)
  3) m[i-2] > m[i-1] > m[i] 연속 하락 (파동 고점에서 2단계 하락)
  4) SMI_REQUIRE_POSITIVE_PIVOT=True 이면 pivot 값이 양수여야 함
  ※ SMA50 필터 없음 — 매도 시그널은 SMI 패턴만으로 판단

공통: 마지막 2개 봉(i, i-1) 체크 — Upbit(KST)/Binance(UTC) 캔들 경계 차이 보정
"""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from rich.console import Console

from src.indicators.extrema import find_pivot_min, find_pivot_max
from src.indicators.moving_averages import calculate_sma
from src.storage.sent_store import is_signal_sent, mark_signal_sent
from src.config import (
    SMI_LOCAL_MIN_WINDOW,
    SMI_REQUIRE_NEGATIVE_PIVOT,
    SMI_REQUIRE_POSITIVE_PIVOT,
    SIGNAL_ENABLE_SMA50_FILTER,
)

console = Console()


def check_smi_signal(df: pd.DataFrame, timeframe: str) -> Optional[Dict]:
    """
    단일 마켓 DataFrame에서 SMI 시그널 감지

    마지막 2개 봉(i, i-1)을 체크한다.
    Upbit(KST 기준)과 Binance(UTC 기준)의 4h 캔들 경계가 최대 4시간 어긋나,
    배치 실행 시점에 따라 신호 봉이 마지막 봉이 아닌 직전 봉에 위치할 수 있다.

    Returns:
        시그널 딕셔너리 또는 None (시그널 없음)
    """
    if df.empty or "smi_momentum" not in df.columns:
        return None

    df_sorted = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)

    smi_values = df_sorted["smi_momentum"].dropna()
    if smi_values.empty:
        return None

    if len(df_sorted) < SMI_LOCAL_MIN_WINDOW + 3:
        return None

    merged_df = calculate_sma(df_sorted, periods=[50, 200])
    pivot_info = find_pivot_min(
        merged_df["smi_momentum"],
        window=SMI_LOCAL_MIN_WINDOW,
        require_negative=SMI_REQUIRE_NEGATIVE_PIVOT,
    )
    merged_df = pd.concat([merged_df, pivot_info], axis=1)

    last_idx = len(merged_df) - 1

    # 마지막 봉(i)과 직전 봉(i-1) 순서로 체크 — 가장 최근 시그널 우선
    for i in [last_idx, last_idx - 1]:
        if i < SMI_LOCAL_MIN_WINDOW + 2:
            continue

        m_i2 = merged_df.iloc[i]["smi_momentum"]
        if pd.isna(m_i2) or m_i2 >= 0:
            continue

        # 피벗(로컬 미니멈)이 정확히 2칸 전이어야 함
        pivot_idx_loc = int(merged_df.iloc[i]["pivot_idx"])
        if pivot_idx_loc < 0 or pivot_idx_loc != i - 2:
            continue

        m_i = merged_df.iloc[pivot_idx_loc]["smi_momentum"]
        m_i1 = merged_df.iloc[pivot_idx_loc + 1]["smi_momentum"]

        if pd.isna(m_i) or pd.isna(m_i1):
            continue
        # 연속 상승 조건: 피벗 → 피벗+1 → 현재 (모두 증가)
        if not (m_i2 > m_i1 > m_i):
            continue
        if SMI_REQUIRE_NEGATIVE_PIVOT and m_i >= 0:
            continue

        signal_row = merged_df.iloc[i]

        if SIGNAL_ENABLE_SMA50_FILTER:
            if pd.isna(signal_row["sma_50"]) or signal_row["close"] <= signal_row["sma_50"]:
                continue

        return {
            "timeframe": timeframe,
            "signal_time_kst": str(signal_row["candle_time_kst"]),
            "side": "BUY",
            "close": float(signal_row["close"]),
            "smi_pivot_min": float(m_i),
            "smi_m_i": float(m_i),
            "smi_m_i1": float(m_i1),
            "smi_m_i2": float(m_i2),
            "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
            "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
            "sma200_above": (
                bool(signal_row["close"] > signal_row["sma_200"])
                if not pd.isna(signal_row["sma_200"]) else None
            ),
        }

    return None


def detect_signals(
    market_data: Dict[str, Dict[str, pd.DataFrame]],
    timeframe: str,
    source_prefix: str = "",
) -> List[Tuple[str, Dict]]:
    """
    마켓 데이터에서 SMI 시그널 감지 및 중복 체크

    Args:
        market_data: {market: {timeframe: DataFrame}} (SMI 계산 완료)
        timeframe: 확인할 시간프레임
        source_prefix: 중복 체크용 prefix (예: "UPBIT-", "BINANCE-")

    Returns:
        [(market, signal_dict), ...] - 시그널 발생 마켓 리스트
    """
    results = []

    for market, tf_data in market_data.items():
        if timeframe not in tf_data or tf_data[timeframe].empty:
            continue

        signal = check_smi_signal(tf_data[timeframe].copy(), timeframe)
        if signal is None:
            continue

        signal["market"] = market

        dedup_key = source_prefix + market
        if is_signal_sent(
            market=dedup_key,
            timeframe=timeframe,
            signal_time_kst=signal["signal_time_kst"],
            side="BUY",
        ):
            console.print(f"[yellow]  {dedup_key} {timeframe}: 중복 시그널 스킵[/yellow]")
            continue

        console.print(f"[green]  ✅ 시그널 발생: {market} [{timeframe}][/green]")
        results.append((market, signal))

    return results


def mark_signals_sent(signals: List[Tuple[str, Dict]], source_prefix: str = "") -> None:
    """전송 완료된 매수 시그널 마킹 (중복 방지용)"""
    for market, signal in signals:
        mark_signal_sent(
            market=source_prefix + market,
            timeframe=signal["timeframe"],
            signal_time_kst=signal["signal_time_kst"],
            side="BUY",
        )


def check_smi_sell_signal(df: pd.DataFrame, timeframe: str) -> Optional[Dict]:
    """
    단일 마켓 DataFrame에서 SMI 매도 시그널 감지 (매수의 정반대 로직)

    마지막 2개 봉(i, i-1)을 체크한다.
    SMA50 필터 없음 — 매도 시그널은 SMI 패턴만으로 판단.

    Returns:
        시그널 딕셔너리 또는 None (시그널 없음)
    """
    if df.empty or "smi_momentum" not in df.columns:
        return None

    df_sorted = df.sort_values("candle_time_kst", ascending=True).reset_index(drop=True)

    smi_values = df_sorted["smi_momentum"].dropna()
    if smi_values.empty:
        return None

    if len(df_sorted) < SMI_LOCAL_MIN_WINDOW + 3:
        return None

    merged_df = calculate_sma(df_sorted, periods=[50, 200])
    pivot_info = find_pivot_max(
        merged_df["smi_momentum"],
        window=SMI_LOCAL_MIN_WINDOW,
        require_positive=SMI_REQUIRE_POSITIVE_PIVOT,
    )
    merged_df = pd.concat([merged_df, pivot_info], axis=1)

    last_idx = len(merged_df) - 1

    # 마지막 봉(i)과 직전 봉(i-1) 순서로 체크 — 가장 최근 시그널 우선
    for i in [last_idx, last_idx - 1]:
        if i < SMI_LOCAL_MIN_WINDOW + 2:
            continue

        m_i2 = merged_df.iloc[i]["smi_momentum"]
        # 마지막 SMI 값이 양수여야 함 (매수의 음수와 반대)
        if pd.isna(m_i2) or m_i2 <= 0:
            continue

        # 피벗(로컬 맥시멈)이 정확히 2칸 전이어야 함
        pivot_idx_loc = int(merged_df.iloc[i]["pivot_max_idx"])
        if pivot_idx_loc < 0 or pivot_idx_loc != i - 2:
            continue

        m_i = merged_df.iloc[pivot_idx_loc]["smi_momentum"]
        m_i1 = merged_df.iloc[pivot_idx_loc + 1]["smi_momentum"]

        if pd.isna(m_i) or pd.isna(m_i1):
            continue
        # 연속 하락 조건: 피벗 → 피벗+1 → 현재 (모두 감소) — 매수의 연속 상승과 반대
        if not (m_i2 < m_i1 < m_i):
            continue
        if SMI_REQUIRE_POSITIVE_PIVOT and m_i <= 0:
            continue

        signal_row = merged_df.iloc[i]

        return {
            "timeframe": timeframe,
            "signal_time_kst": str(signal_row["candle_time_kst"]),
            "side": "SELL",
            "close": float(signal_row["close"]),
            "smi_pivot_max": float(m_i),
            "smi_m_i": float(m_i),
            "smi_m_i1": float(m_i1),
            "smi_m_i2": float(m_i2),
            "sma50": float(signal_row["sma_50"]) if not pd.isna(signal_row["sma_50"]) else None,
            "sma200": float(signal_row["sma_200"]) if not pd.isna(signal_row["sma_200"]) else None,
            "sma200_above": (
                bool(signal_row["close"] > signal_row["sma_200"])
                if not pd.isna(signal_row["sma_200"]) else None
            ),
        }

    return None


def detect_sell_signals(
    market_data: Dict[str, Dict[str, pd.DataFrame]],
    timeframe: str,
    source_prefix: str = "",
) -> List[Tuple[str, Dict]]:
    """
    마켓 데이터에서 SMI 매도 시그널 감지 및 중복 체크

    Args:
        market_data: {market: {timeframe: DataFrame}} (SMI 계산 완료)
        timeframe: 확인할 시간프레임
        source_prefix: 중복 체크용 prefix (예: "UPBIT-", "BINANCE-")

    Returns:
        [(market, signal_dict), ...] - 시그널 발생 마켓 리스트
    """
    results = []

    for market, tf_data in market_data.items():
        if timeframe not in tf_data or tf_data[timeframe].empty:
            continue

        signal = check_smi_sell_signal(tf_data[timeframe].copy(), timeframe)
        if signal is None:
            continue

        signal["market"] = market

        dedup_key = source_prefix + market
        if is_signal_sent(
            market=dedup_key,
            timeframe=timeframe,
            signal_time_kst=signal["signal_time_kst"],
            side="SELL",
        ):
            console.print(f"[yellow]  {dedup_key} {timeframe}: 중복 매도 시그널 스킵[/yellow]")
            continue

        console.print(f"[red]  🔴 매도 시그널 발생: {market} [{timeframe}][/red]")
        results.append((market, signal))

    return results


def mark_sell_signals_sent(signals: List[Tuple[str, Dict]], source_prefix: str = "") -> None:
    """전송 완료된 매도 시그널 마킹 (중복 방지용)"""
    for market, signal in signals:
        mark_signal_sent(
            market=source_prefix + market,
            timeframe=signal["timeframe"],
            signal_time_kst=signal["signal_time_kst"],
            side="SELL",
        )
