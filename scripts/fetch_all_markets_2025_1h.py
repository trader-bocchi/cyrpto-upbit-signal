"""
Upbit 2025년 전체 마켓 1시간 캔들 수집 스크립트 (고속 버전, 월 단위 저장)

실행: python scripts/fetch_all_markets_2025_1h.py
설정: 스크립트 상단 상수만 수정

저장 구조: data/raw/candles_1h/upbit_{market}_{YYYYMM}.csv (월 단위)
Meta 구조: data/meta/candles_1h/upbit_{market}_{YYYYMM}.meta.json (CSV와 분리)
"""
import json
import time
import random
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd

# 상대 경로 import를 위한 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.monthly_store import (
    get_monthly_csv_path,
    load_monthly_csv,
    merge_monthly_data,
    dedup_monthly_dataframe,
)
from src.storage.missing_logger import log_missing_summary, get_missing_top_n
from src.upbit_client_fast import FastUpbitClient

# ==================== 설정 상수 ====================
DATA_ROOT = Path("data/raw/candles_1h")
META_ROOT = Path("data/meta/candles_1h")  # Meta 파일 저장 경로 (CSV와 분리)
YEAR = 2025
MARKET_PREFIX_FILTER = None  # None이면 전체, 예: ["KRW-"]면 KRW만
REQUEST_TIMEOUT_SEC = 20
BASE_SLEEP_SEC = 0.15  # 기본 sleep (Rate Limit 예방, 윈도우 제한 제거)
MAX_HTTP_RETRIES = 10
HTTP_BACKOFF_BASE_SEC = 2.0
MAX_MARKET_RETRY_ROUNDS = 3
RESUME_ENABLED = True  # 재개 기능 on/off
LOG_EVERY_N_REQUESTS = 50  # 로그 출력 주기
SLEEP_BETWEEN_ROUNDS_SEC = 10  # 라운드 간 대기 시간
# ====================================================

# 경로 설정
META_DIR = Path("data/meta")
STATUS_FILE = META_DIR / "fetch_2025_1h_status.json"
META_DIR.mkdir(parents=True, exist_ok=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)
META_ROOT.mkdir(parents=True, exist_ok=True)

# 2025년 범위 (KST)
START_KST = datetime(2025, 1, 1, 0, 0, 0)
END_KST = datetime(2025, 12, 31, 23, 0, 0)


def get_kst_now() -> str:
    """현재 KST 시각 문자열"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def kst_to_iso_string(dt: datetime) -> str:
    """KST datetime을 ISO 문자열로 변환 (Upbit API 형식)"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_kst_string(s: str) -> datetime:
    """KST 문자열을 datetime으로 파싱"""
    s = s.replace("T", " ").strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(f"날짜 파싱 실패: {s}")


def load_status() -> Dict:
    """상태 파일 로드"""
    if not STATUS_FILE.exists():
        return {}
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_status(status: Dict):
    """상태 파일 저장"""
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)


def get_all_markets(client: FastUpbitClient) -> List[str]:
    """현재 상장된 모든 마켓 조회"""
    markets_data = client.get_markets()
    markets = [m["market"] for m in markets_data]
    
    # 필터 적용
    if MARKET_PREFIX_FILTER:
        markets = [m for m in markets if any(m.startswith(p) for p in MARKET_PREFIX_FILTER)]
    
    return markets


def process_candle_data(candles: List[Dict], market: str) -> pd.DataFrame:
    """캔들 데이터를 DataFrame으로 변환"""
    rows = []
    ingest_time = get_kst_now()
    
    for candle in candles:
        if "candle_date_time_kst" not in candle:
            continue
        
        candle_time_kst = parse_kst_string(candle["candle_date_time_kst"])
        
        # 범위 체크
        if candle_time_kst < START_KST or candle_time_kst > END_KST:
            continue
        
        row = {
            "market": market,
            "candle_time_kst": candle_time_kst,
            "open": float(candle.get("opening_price", 0)),
            "high": float(candle.get("high_price", 0)),
            "low": float(candle.get("low_price", 0)),
            "close": float(candle.get("trade_price", 0)),
            "volume": float(candle.get("candle_acc_trade_volume", 0)),
            "trade_value": float(candle.get("candle_acc_trade_price", 0)),
            "ingest_time_kst": ingest_time,
        }
        rows.append(row)
    
    return pd.DataFrame(rows)


def fetch_market_candles(
    client: FastUpbitClient,
    market: str,
    status: Dict,
    round_num: int = 1,
) -> Tuple[bool, str, Dict]:
    """
    단일 마켓 캔들 수집 (월 단위 저장)
    
    Returns:
        (성공 여부, 결과 메시지, 통계 정보)
    """
    print(f"\n[라운드 {round_num}] 마켓: {market}")
    
    # 재개 체크
    market_status = status.get(market, {})
    if RESUME_ENABLED and market_status.get("status") == "DONE":
        print(f"  [스킵] 이미 완료됨")
        return True, "DONE", {}
    
    # 시작 시각 결정
    if RESUME_ENABLED and market_status.get("last_to_kst"):
        try:
            start_to = parse_kst_string(market_status["last_to_kst"])
            print(f"  [재개] {start_to}부터 이어서 수집")
        except:
            start_to = END_KST
            print(f"  [시작] {start_to}부터 수집")
    else:
        start_to = END_KST
        print(f"  [시작] {start_to}부터 수집")
    
    # 수집 루프
    current_to = start_to
    request_count = 0
    total_bars_collected = 0
    rate_limit_count = 0
    start_time = time.time()
    
    # 월별 데이터 저장 (메모리 효율)
    monthly_chunks: Dict[str, List[pd.DataFrame]] = {}  # month_str -> [df1, df2, ...]
    
    while current_to >= START_KST:
        request_count += 1
        
        if request_count % LOG_EVERY_N_REQUESTS == 0:
            elapsed = time.time() - start_time
            req_per_sec = request_count / elapsed if elapsed > 0 else 0
            bars_per_sec = total_bars_collected / elapsed if elapsed > 0 else 0
            print(f"  [진행] to={current_to.strftime('%Y-%m-%d %H:%M')}, "
                  f"수집={total_bars_collected}개, 요청={request_count}회, "
                  f"{req_per_sec:.1f} req/s, {bars_per_sec:.1f} bars/s, "
                  f"429={rate_limit_count}회")
        
        # API 호출
        to_str = kst_to_iso_string(current_to)
        candles = client.get_candles_minutes(market, unit=60, to=to_str, count=200)
        
        if candles is None or len(candles) == 0:
            print(f"  [경고] 데이터 없음 또는 실패, 중단")
            break
        
        # 429 카운트 (간접 추정)
        if client.rate_limit_mode:
            rate_limit_count += 1
        
        # DataFrame 변환
        new_df = process_candle_data(candles, market)
        
        if new_df.empty:
            print(f"  [경고] 변환된 데이터 없음, 중단")
            break
        
        total_bars_collected += len(new_df)
        
        # 월별로 그룹화하여 메모리에 저장
        new_df["month_str"] = new_df["candle_time_kst"].dt.strftime("%Y-%m")
        for month_str, group_df in new_df.groupby("month_str"):
            if month_str not in monthly_chunks:
                monthly_chunks[month_str] = []
            monthly_chunks[month_str].append(group_df.drop(columns=["month_str"]))
        
        # 가장 오래된 캔들 시각 찾기
        min_time = new_df["candle_time_kst"].min()
        
        # 무한 루프 방지: 1초 전으로 이동
        current_to = min_time - timedelta(seconds=1)
        
        # 범위 체크
        if min_time < START_KST:
            print(f"  [완료] 시작 시각 도달")
            break
    
    # 월별로 저장
    saved_months = 0
    total_missing_hours = 0
    
    for month_str, chunks in monthly_chunks.items():
        # 월의 첫 날짜로 datetime 생성
        date_kst = datetime.strptime(month_str + "-01", "%Y-%m-%d")
        
        # 기존 데이터 로드
        existing_df = load_monthly_csv(DATA_ROOT, market, date_kst)
        
        # 병합
        new_df = pd.concat(chunks, ignore_index=True)
        combined_df, meta = merge_monthly_data(existing_df, new_df, DATA_ROOT, META_ROOT, market, date_kst)
        
        # 미수집 로깅 (날짜별로)
        missing_hours_list = meta.get("missing_hours", [])
        if missing_hours_list:
            for missing_info in missing_hours_list:
                date_str = missing_info.get("date", "")
                hours = missing_info.get("hours", [])
                if hours:
                    total_missing_hours += len(hours)
                    try:
                        date_kst_for_log = datetime.strptime(date_str, "%Y-%m-%d")
                        log_missing_summary(
                            META_DIR,
                            market,
                            date_kst_for_log,
                            hours,
                            meta.get("rows_saved", 0),
                        )
                    except:
                        pass
        
        saved_months += 1
    
    # 상태 업데이트
    elapsed = time.time() - start_time
    stats = {
        "requests": request_count,
        "bars_collected": total_bars_collected,
        "rate_limit_count": rate_limit_count,
        "saved_months": saved_months,
        "total_missing_hours": total_missing_hours,
        "elapsed_sec": elapsed,
    }
    
    if current_to < START_KST:
        status[market] = {
            "status": "DONE",
            "last_to_kst": kst_to_iso_string(START_KST),
            "updated_at_kst": get_kst_now(),
            "stats": stats,
        }
        print(f"  [완료] {saved_months}개월 저장, 누락 {total_missing_hours}시간")
        return True, "DONE", stats
    else:
        status[market] = {
            "status": "IN_PROGRESS",
            "last_to_kst": kst_to_iso_string(current_to),
            "updated_at_kst": get_kst_now(),
            "stats": stats,
        }
        print(f"  [진행 중] {saved_months}개월 저장, 누락 {total_missing_hours}시간")
        return False, "IN_PROGRESS", stats


def main():
    """메인 실행"""
    print("=" * 60)
    print("Upbit 2025년 전체 마켓 1시간 캔들 수집 (고속 버전, 월 단위 저장)")
    print("=" * 60)
    print(f"시작 시각: {get_kst_now()}")
    print(f"수집 범위: {START_KST} ~ {END_KST} (KST)")
    print("저장 구조: 월 단위 CSV (upbit_{market}_{YYYYMM}.csv)")
    print("Meta 구조: data/meta/candles_1h/ (CSV와 분리)")
    print()
    
    # 클라이언트 생성
    client = FastUpbitClient(
        base_sleep=BASE_SLEEP_SEC,
        request_timeout=REQUEST_TIMEOUT_SEC,
        max_retries=MAX_HTTP_RETRIES,
        backoff_base=HTTP_BACKOFF_BASE_SEC,
    )
    
    # 상태 로드
    status = load_status()
    
    # 마켓 목록 조회
    print("[1단계] 마켓 목록 조회 중...")
    try:
        all_markets = get_all_markets(client)
        print(f"  총 {len(all_markets)}개 마켓 발견")
    except Exception as e:
        print(f"  [오류] 마켓 목록 조회 실패: {e}")
        return
    
    # 재개 모드: DONE 마켓 제외
    if RESUME_ENABLED:
        remaining_markets = [
            m for m in all_markets
            if status.get(m, {}).get("status") != "DONE"
        ]
        print(f"  재개 모드: {len(remaining_markets)}개 마켓 남음 (완료: {len(all_markets) - len(remaining_markets)}개)")
    else:
        remaining_markets = all_markets
    
    if not remaining_markets:
        print("\n[완료] 모든 마켓 수집 완료!")
        return
    
    # 라운드별 재시도
    failed_markets = []
    all_stats = []
    
    for round_num in range(1, MAX_MARKET_RETRY_ROUNDS + 1):
        print(f"\n{'=' * 60}")
        print(f"[라운드 {round_num}/{MAX_MARKET_RETRY_ROUNDS}]")
        print(f"{'=' * 60}")
        
        markets_to_process = failed_markets if round_num > 1 else remaining_markets
        
        if not markets_to_process:
            print("  처리할 마켓 없음")
            break
        
        print(f"  처리 대상: {len(markets_to_process)}개 마켓")
        
        round_failed = []
        
        for i, market in enumerate(markets_to_process, 1):
            try:
                success, result, stats = fetch_market_candles(client, market, status, round_num)
                all_stats.append({"market": market, **stats})
                
                if not success:
                    round_failed.append(market)
                
                # 상태 저장
                save_status(status)
                
            except Exception as e:
                print(f"  [예외] {e}")
                round_failed.append(market)
                status[market] = {
                    "status": "FAILED",
                    "error": str(e),
                    "updated_at_kst": get_kst_now(),
                }
                save_status(status)
        
        failed_markets = round_failed
        
        if not failed_markets:
            print(f"\n[라운드 {round_num} 완료] 모든 마켓 성공!")
            break
        
        if round_num < MAX_MARKET_RETRY_ROUNDS:
            print(f"\n[라운드 {round_num} 완료] 실패 마켓: {len(failed_markets)}개")
            print(f"  {SLEEP_BETWEEN_ROUNDS_SEC}초 후 다음 라운드 시작...")
            time.sleep(SLEEP_BETWEEN_ROUNDS_SEC)
    
    # 최종 요약
    print("\n" + "=" * 60)
    print("[최종 요약]")
    print("=" * 60)
    
    final_status = load_status()
    done_count = sum(1 for m in all_markets if final_status.get(m, {}).get("status") == "DONE")
    failed_count = len(failed_markets)
    in_progress_count = len(all_markets) - done_count - failed_count
    
    print(f"총 마켓 수: {len(all_markets)}")
    print(f"  ✅ 완료: {done_count}개")
    print(f"  ⏳ 진행 중: {in_progress_count}개")
    print(f"  ❌ 실패: {failed_count}개")
    
    # 통계 요약
    if all_stats:
        total_requests = sum(s.get("requests", 0) for s in all_stats)
        total_bars = sum(s.get("bars_collected", 0) for s in all_stats)
        total_429 = sum(s.get("rate_limit_count", 0) for s in all_stats)
        total_elapsed = sum(s.get("elapsed_sec", 0) for s in all_stats)
        
        print(f"\n[성능 통계]")
        print(f"  총 요청: {total_requests}회")
        print(f"  총 수집: {total_bars}개 바")
        print(f"  429 발생: {total_429}회")
        if total_elapsed > 0:
            print(f"  평균 속도: {total_requests/total_elapsed:.1f} req/s, {total_bars/total_elapsed:.1f} bars/s")
    
    # 누락 요약
    missing_top = get_missing_top_n(META_DIR, n=10)
    if missing_top:
        print(f"\n[누락 Top 10]")
        for record in missing_top[:10]:
            missing_count = len(record.get("missing_hours", []))
            if missing_count > 0:
                print(f"  {record['market']} {record['date']}: {missing_count}시간 누락")
    
    if failed_markets:
        print(f"\n실패 마켓 목록:")
        for market in failed_markets:
            error = final_status.get(market, {}).get("error", "알 수 없음")
            print(f"  - {market}: {error}")
    
    print(f"\n종료 시각: {get_kst_now()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
