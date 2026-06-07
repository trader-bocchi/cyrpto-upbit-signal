"""SMI 시그널 감지 로직 (업비트)

매수 시그널 조건 (Squeeze Momentum Index 기반):
  1) 최근 로컬 미니멈(pivot)이 현재 바 기준 정확히 2칸 전(i-2)
  2) m[i-2] < m[i-1] < m[i] 연속 상승 (로컬미니멈 이후 2캔들 회복 확인)
  3) SMI_REQUIRE_NEGATIVE_PIVOT=True 이면 pivot 값이 음수여야 함

매도 시그널 조건 (매수의 정반대):
  1) 최근 로컬 맥시멈(pivot)이 현재 바 기준 정확히 2칸 전(i-2)
  2) m[i-2] > m[i-1] > m[i] 연속 하락 (로컬맥시멈 이후 2캔들 하락 확인)
  3) SMI_REQUIRE_POSITIVE_PIVOT=True 이면 pivot 값이 양수여야 함

공통: 마지막 3개 봉(i, i-1, i-2) 체크 — 미완성 캔들 및 배치 타이밍 오차 보정

시그널 강도(매수 확신도):
  스퀴즈 릴리즈 봉(squeeze_on True→False) 대비 현재까지 모먼텀 변화량을
  스퀴즈 온 기간의 평균 봉 간 변화량과 비교
  - STRONG: 3배 이상 — 강한 브레이크아웃
  - MODERATE: 1.5배 이상 — 보통 수준
  - NORMAL: 기준 이하
"""
import numpy as np
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
)

console = Console()

_STRENGTH_THRESHOLDS = {"STRONG": 3.0, "MODERATE": 1.5}


def calculate_signal_strength(df: pd.DataFrame, signal_idx: int) -> Dict:
    """
    매수 시그널 강도(확신도) 계산

    직전 스퀴즈 릴리즈 봉(squeeze_on True→False 전환점)부터 현재 시그널 봉까지의
    모먼텀 변화량을, 스퀴즈 온 기간 동안의 봉 간 평균 변화량으로 나눠 강도 산정.
    """
    if "smi_momentum" not in df.columns:
        return {"strength": "NORMAL", "strength_score": 0.0}

    smi = df["smi_momentum"].values
    has_squeeze = "squeeze_on" in df.columns
    squeeze_on = df["squeeze_on"].values if has_squeeze else np.zeros(len(df), dtype=bool)

    current_smi = float(smi[signal_idx])
    if np.isnan(current_smi):
        return {"strength": "NORMAL", "strength_score": 0.0}

    squeeze_release_idx = None
    search_start = signal_idx - 1
    for j in range(search_start, max(0, signal_idx - 120), -1):
        if squeeze_on[j]:
            squeeze_release_idx = min(j + 1, signal_idx)
            break

    if squeeze_release_idx is None or squeeze_release_idx >= signal_idx:
        return {"strength": "NORMAL", "strength_score": 0.0}

    release_smi = float(smi[squeeze_release_idx])
    if np.isnan(release_smi):
        return {"strength": "NORMAL", "strength_score": 0.0}

    current_delta = abs(current_smi - release_smi)

    pre_deltas: List[float] = []
    for j in range(squeeze_release_idx - 1, max(0, squeeze_release_idx - 60), -1):
        if not squeeze_on[j]:
            break
        if j > 0 and not np.isnan(smi[j]) and not np.isnan(smi[j - 1]):
            pre_deltas.append(abs(float(smi[j]) - float(smi[j - 1])))

    if not pre_deltas:
        for j in range(squeeze_release_idx - 1, max(0, squeeze_release_idx - 20), -1):
            if j > 0 and not np.isnan(smi[j]) and not np.isnan(smi[j - 1]):
                pre_deltas.append(abs(float(smi[j]) - float(smi[j - 1])))

    if not pre_deltas:
        return {"strength": "NORMAL", "strength_score": 0.0}

    avg_pre_delta = float(np.mean(pre_deltas))
    if avg_pre_delta < 1e-8:
        return {"strength": "NORMAL", "strength_score": 0.0}

    strength_score = current_delta / avg_pre_delta

    if strength_score >= _STRENGTH_THRESHOLDS["STRONG"]:
        strength = "STRONG"
    elif strength_score >= _STRENGTH_THRESHOLDS["MODERATE"]:
        strength = "MODERATE"
    else:
        strength = "NORMAL"

    return {
        "strength": strength,
        "strength_score": round(strength_score, 2),
        "squeeze_release_smi": round(release_smi, 4),
        "current_delta": round(current_delta, 4),
        "avg_pre_delta": round(avg_pre_delta, 4),
    }


def check_smi_signal(df: pd.DataFrame, timeframe: str) -> Optional[Dict]:
    """
    단일 마켓 DataFrame에서 SMI 매수 시그널 감지

    로컬미니멈 이후 2캔들 연속 상승 패턴을 감지한다.
    마지막 3개 봉을 체크하여 미완성 캔들 및 배치 타이밍 오차를 보정.

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

    # 마지막 3개 봉 체크 (미완성 캔들 + 배치 타이밍 오차 대응)
    for i in [last_idx, last_idx - 1, last_idx - 2]:
        if i < SMI_LOCAL_MIN_WINDOW + 2:
            continue

        m_i2 = merged_df.iloc[i]["smi_momentum"]
        if pd.isna(m_i2):
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

        strength_info = calculate_signal_strength(merged_df, i)
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
            **strength_info,
        }

    return None


def detect_signals(
    market_data: Dict[str, Dict[str, pd.DataFrame]],
    timeframe: str,
    source_prefix: str = "",
) -> List[Tuple[str, Dict]]:
    """
    마켓 데이터에서 SMI 매수 시그널 감지 및 중복 체크

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

        console.print(f"[green]  BUY 시그널: {market} [{timeframe}][/green]")
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
    단일 마켓 DataFrame에서 SMI 매도 시그널 감지

    로컬맥시멈 이후 2캔들 연속 하락 패턴을 감지한다 (매수의 정반대).
    마지막 3개 봉을 체크하여 미완성 캔들 및 배치 타이밍 오차를 보정.

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

    # 마지막 3개 봉 체크
    for i in [last_idx, last_idx - 1, last_idx - 2]:
        if i < SMI_LOCAL_MIN_WINDOW + 2:
            continue

        m_i2 = merged_df.iloc[i]["smi_momentum"]
        if pd.isna(m_i2):
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

        console.print(f"[red]  SELL 시그널: {market} [{timeframe}][/red]")
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
