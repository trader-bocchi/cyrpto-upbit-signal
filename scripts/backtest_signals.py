"""종목별 시그널 백테스트 (신선 데이터 수집 → 진입=실전규칙, 청산=손절/익절/타임스탑).

실전과 동일한 진입 규칙(src.signals.smi_rule)과 동일한 청산 파라미터(config)를 사용한다.
타임스탑은 config의 BACKTEST_TIME_STOP_MODE(adaptive=동적A / fixed=고정봉수)를 따른다.

사용:
    python scripts/backtest_signals.py                 # 기본 BTC/ETH, 2026-01-01~현재
    python scripts/backtest_signals.py KRW-BTC KRW-SOL
    START=2025-01-01 python scripts/backtest_signals.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline.fetch_4h_1d_direct import fetch_timeframe_direct
from src.indicators.squeeze_momentum import calculate_smi
from src.signals.signal_engine import detect_buy_signals
from src.signals.exit_rules import adaptive_bounds, momentum_time_stop_hit
from src.config import (
    BACKTEST_STOP_LOSS_PCT,
    BACKTEST_TAKE_PROFIT_PCT,
    BACKTEST_FEE_RATE,
    BACKTEST_TIME_STOP_MODE,
    BACKTEST_TIME_STOP_BARS_4H,
)

TF = "4h"
COUNT_FROM = pd.Timestamp(os.getenv("START", "2026-01-01"))
WARMUP_START = COUNT_FROM.to_pydatetime().replace(hour=0) - pd.Timedelta(days=90)


def prepare(market):
    df = fetch_timeframe_direct(market, TF, start_kst=WARMUP_START, end_kst=datetime.now())
    df = df.drop_duplicates(subset=["candle_time_kst"]).sort_values("candle_time_kst").reset_index(drop=True)
    if len(df) > 1:  # 마지막 미완성 캔들 제거
        df = df.iloc[:-1].reset_index(drop=True)
    df = calculate_smi(df).sort_values("candle_time_kst").reset_index(drop=True)
    signals = detect_buy_signals(df.copy(), market, TF, use_saved_smi=False)
    return {
        "times": pd.to_datetime(df["candle_time_kst"]).tolist(),
        "open": df["open"].tolist(),
        "close": df["close"].tolist(),
        "smi": df["smi_momentum"].tolist(),
        "sig": {pd.Timestamp(s["signal_time_kst"]) for s in signals},
        "n": len(df),
    }


def find_exit(data, entry_bar):
    close, smi, n = data["close"], data["smi"], data["n"]
    ep = data["open"][entry_bar]
    min_bars, max_bars = adaptive_bounds(TF)
    j = entry_bar
    while j < n:
        c = close[j]
        hold = j - entry_bar
        if c <= ep * (1 - BACKTEST_STOP_LOSS_PCT):
            return j, c, "STOP"
        if c >= ep * (1 + BACKTEST_TAKE_PROFIT_PCT):
            return j, c, "TAKE"
        if BACKTEST_TIME_STOP_MODE == "adaptive":
            if momentum_time_stop_hit(smi, j, hold, min_bars, max_bars):
                return j, c, "TIME_STOP"
        else:  # fixed
            if hold >= BACKTEST_TIME_STOP_BARS_4H:
                return j, c, "TIME_STOP"
        j += 1
    return n - 1, close[n - 1], "OPEN_END"


def backtest(market):
    data = prepare(market)
    times, sig, n = data["times"], data["sig"], data["n"]
    equity = 1.0
    trades = []
    i = 0
    while i < n - 1:
        if times[i] in sig:
            eb = i + 1
            j, ex, reason = find_exit(data, eb)
            net = (ex - data["open"][eb]) / data["open"][eb] - 2 * BACKTEST_FEE_RATE
            if pd.Timestamp(times[i]) >= COUNT_FROM:
                trades.append({"reason": reason, "pnl_pct": net * 100,
                               "hold_days": (j - eb) * 4 / 24})
                equity *= (1 + net)
            i = j + 1
        else:
            i += 1

    df_p = pd.DataFrame({"t": times, "close": data["close"]})
    df_p = df_p[df_p["t"] >= COUNT_FROM]
    bh = (df_p["close"].iloc[-1] / df_p["close"].iloc[0] - 1) * 100 if len(df_p) > 1 else 0.0
    return (equity - 1) * 100, trades, bh


def main():
    markets = sys.argv[1:] or ["KRW-BTC", "KRW-ETH"]
    print(f"\n타임스탑 모드: {BACKTEST_TIME_STOP_MODE}  |  집계 시작: {COUNT_FROM.date()}")
    print("=" * 78)
    for m in markets:
        total, trades, bh = backtest(m)
        n = len(trades)
        wins = [t for t in trades if t["pnl_pct"] > 0]
        wr = len(wins) / n * 100 if n else 0
        avg_hold = sum(t["hold_days"] for t in trades) / n if n else 0
        reasons = {}
        for t in trades:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print(f"\n■ {m}")
        print(f"  트레이드 {n} | 승률 {wr:.1f}% | 평균보유 {avg_hold:.1f}일 | "
              f"누적수익 {total:+.2f}% (매수후보유 {bh:+.2f}%)")
        print(f"  청산: {reasons}")
    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
