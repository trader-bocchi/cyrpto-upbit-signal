"""캔들 정합성 헬스체크 — '최신 완성 캔들'이 정확히 잡히는지(밀림 없음) 검증.

확인 항목:
  1) 모든 4h 캔들 시각이 업비트 4h 경계(KST 01·05·09·13·17·21)에 정렬돼 있는가
  2) 수집 원본의 마지막 봉 = 현재 진행 중(미완성) 봉인가
  3) 미완성 봉 제거 후 마지막 봉 = '가장 최근 완성 캔들'인가
     (조건: last+4h <= now < last+8h → 완성됐고, 그다음 봉은 아직 미완성)
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline.fetch_4h_1d_direct import fetch_timeframe_direct
from batch.fetch_data import drop_incomplete_candle

KST = timezone(timedelta(hours=9))
MARKETS = ["KRW-BTC", "KRW-ETH"]


def check(market):
    now = datetime.now()  # 이 PC는 KST
    start = now - timedelta(days=10)
    raw = fetch_timeframe_direct(market, "4h", start_kst=start, end_kst=now)
    raw = raw.drop_duplicates(subset=["candle_time_kst"]).sort_values("candle_time_kst").reset_index(drop=True)
    prod = drop_incomplete_candle(raw)

    raw_last = pd.to_datetime(raw["candle_time_kst"].iloc[-1])
    prod_last = pd.to_datetime(prod["candle_time_kst"].iloc[-1])
    now_ts = pd.Timestamp(now)

    problems = []

    # 1) 4h 경계 정렬 (KST 01,05,09,13,17,21 → hour % 4 == 1)
    hours = pd.to_datetime(prod["candle_time_kst"]).dt.hour
    misaligned = prod[(hours % 4 != 1)]
    if len(misaligned) > 0:
        problems.append(f"4h 경계 어긋난 봉 {len(misaligned)}개")

    # 2) 원본 마지막 = 진행 중(미완성) 봉이어야 함: raw_last + 4h > now
    raw_is_forming = (raw_last + timedelta(hours=4)) > now_ts
    if not raw_is_forming:
        problems.append(f"원본 마지막 봉이 미완성이 아님(raw_last={raw_last})")

    # 3) 제거 후 마지막 = 가장 최근 완성 캔들: last+4h <= now < last+8h
    completed = (prod_last + timedelta(hours=4)) <= now_ts
    is_latest = now_ts < (prod_last + timedelta(hours=8))
    if not completed:
        problems.append(f"마지막 봉이 아직 미완성(prod_last={prod_last})")
    if not is_latest:
        problems.append(f"마지막 봉이 최신이 아님 — 캔들 밀림 의심(prod_last={prod_last})")

    print(f"\n■ {market}")
    print(f"  현재(KST)            : {now_ts:%Y-%m-%d %H:%M}")
    print(f"  원본 마지막(진행중)   : {raw_last:%Y-%m-%d %H:%M}  → 제거 대상")
    print(f"  제거 후 마지막(완성)  : {prod_last:%Y-%m-%d %H:%M}  (마감 {prod_last+timedelta(hours=4):%H:%M})")
    print(f"  경과: 마감 후 {(now_ts - (prod_last+timedelta(hours=4))).total_seconds()/3600:.1f}시간 (0~4h면 정상)")
    print(f"  판정: {'✅ 최신 완성 캔들, 밀림 없음' if not problems else '❌ ' + ' / '.join(problems)}")
    return not problems


def main():
    print("=" * 64)
    print("캔들 정합성 헬스체크 (밀림/미완성 오류 검사)")
    print("=" * 64)
    ok = all(check(m) for m in MARKETS)
    print("\n" + "=" * 64)
    print("결과:", "✅ 전체 정상" if ok else "❌ 문제 발견")
    print("=" * 64)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
