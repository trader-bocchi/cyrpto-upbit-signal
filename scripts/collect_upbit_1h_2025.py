"""
Upbit 2025년 1시간 캔들 수집기 (단순 버전)

실행: python scripts/collect_upbit_1h_2025.py
설정: 스크립트 상단 상수만 수정

저장 구조: data/raw/candles_1h/upbit_{market}_{YYYYMM}.csv (월 단위)
Meta 구조: data/meta/candles_1h/upbit_{market}_{YYYYMM}.meta.json (CSV와 분리)
"""
import json
import time
import requests
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd

# 상대 경로 import를 위한 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.monthly_store import (
    load_monthly_csv,
    merge_monthly_data,
)
from src.storage.missing_logger import log_missing_summary, get_missing_top_n

# ==================== 설정 상수 ====================
DATA_ROOT = Path("data/raw/candles_1h")
META_ROOT = Path("data/meta/candles_1h")
YEAR = 2025
MARKET_PREFIX_FILTER = None  # None이면 전체, 예: ["KRW-"]면 KRW만

# API 설정
UPBIT_API_BASE = "https://api.upbit.com/v1"
REQUEST_DELAY = 0.12  # 요청 간 딜레이 (초당 약 8회, 안전 마진)
REQUEST_TIMEOUT = 20
MAX_RETRIES = 5
RETRY_DELAY_BASE = 2.0  # 재시도 지수 백오프 베이스

# 수집 범위
START_KST = datetime(2025, 1, 1, 0, 0, 0)
END_KST = datetime(2025, 12, 31, 23, 0, 0)

# 로그 설정
LOG_EVERY_N_REQUESTS = 100
# ====================================================

# 경로 설정
META_DIR = Path("data/meta")
STATUS_FILE = META_DIR / "collect_2025_1h_status.json"
META_DIR.mkdir(parents=True, exist_ok=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)
META_ROOT.mkdir(parents=True, exist_ok=True)


def get_kst_now() -> str:
    """현재 KST 시각 문자열"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_all_markets() -> List[str]:
    """현재 상장된 모든 KRW 마켓 조회"""
    url = f"{UPBIT_API_BASE}/market/all"
    params = {"isDetails": "false"}
    
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        markets = [m["market"] for m in data if m.get("market", "").startswith("KRW-")]
        
        # 필터 적용
        if MARKET_PREFIX_FILTER:
            markets = [m for m in markets if any(m.startswith(p) for p in MARKET_PREFIX_FILTER)]
        
        return markets
    except Exception as e:
        print(f"[오류] 마켓 목록 조회 실패: {e}")
        return []


def fetch_candles(
    market: str,
    to_time: Optional[datetime] = None,
    count: int = 200,
) -> Optional[List[Dict]]:
    """
    1시간 캔들 조회 (단순 버전)
    
    Args:
        market: 마켓 코드
        to_time: 조회 종료 시각 (KST datetime)
        count: 조회할 캔들 개수 (최대 200)
    
    Returns:
        캔들 리스트 또는 None
    """
    url = f"{UPBIT_API_BASE}/candles/minutes/60"
    params = {
        "market": market,
        "count": min(count, 200),
    }
    
    if to_time:
        # KST datetime을 ISO 문자열로 변환
        to_str = to_time.strftime("%Y-%m-%dT%H:%M:%S")
        params["to"] = to_str
    
    for attempt in range(MAX_RETRIES):
        try:
            # 요청 간 딜레이
            if attempt > 0:
                delay = RETRY_DELAY_BASE ** attempt
                time.sleep(delay)
            else:
                time.sleep(REQUEST_DELAY)
            
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            
            # 429 Rate Limit 처리
            if response.status_code == 429:
                # Remaining-Req 헤더 확인 (있으면)
                remaining_req = response.headers.get("Remaining-Req", "")
                print(f"  [429] Rate Limit 초과, {remaining_req if remaining_req else '대기 중...'}")
                
                # 5초 대기 후 재시도
                time.sleep(5)
                continue
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                print(f"  [Timeout] 재시도 {attempt + 1}/{MAX_RETRIES}")
                continue
            else:
                print(f"  [실패] Timeout")
                return None
                
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [오류] {e}, 재시도 {attempt + 1}/{MAX_RETRIES}")
                continue
            else:
                print(f"  [실패] {e}")
                return None
    
    return None


def parse_candle_time(candle: Dict) -> Optional[datetime]:
    """캔들에서 KST 시간 파싱"""
    if "candle_date_time_kst" in candle:
        time_str = candle["candle_date_time_kst"]
        # "2025-07-18T12:00:00" 형식
        time_str = time_str.replace("T", " ").strip()
        if "." in time_str:
            time_str = time_str.split(".")[0]
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            except:
                return None
    return None


def process_candles(candles: List[Dict], market: str) -> pd.DataFrame:
    """캔들 데이터를 DataFrame으로 변환"""
    rows = []
    ingest_time = get_kst_now()
    
    for candle in candles:
        candle_time = parse_candle_time(candle)
        if not candle_time:
            continue
        
        # 범위 체크
        if candle_time < START_KST or candle_time > END_KST:
            continue
        
        row = {
            "market": market,
            "candle_time_kst": candle_time,
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


def collect_market(
    market: str,
    status: Dict,
) -> Tuple[bool, Dict]:
    """
    단일 마켓 수집
    
    Returns:
        (성공 여부, 통계 정보)
    """
    print(f"\n[마켓] {market}")
    
    # 재개 체크
    market_status = status.get(market, {})
    if market_status.get("status") == "DONE":
        print(f"  [스킵] 이미 완료됨")
        return True, {}
    
    # 시작 시각 결정
    if market_status.get("last_to_kst"):
        try:
            start_to = datetime.strptime(market_status["last_to_kst"], "%Y-%m-%dT%H:%M:%S")
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
    total_bars = 0
    start_time = time.time()
    
    # 월별 데이터 저장
    monthly_chunks: Dict[str, List[pd.DataFrame]] = {}
    
    # 무한 루프 방지: 같은 시간대가 연속으로 나오는지 추적
    last_min_time = None
    same_time_count = 0
    MAX_SAME_TIME_COUNT = 3  # 같은 시간대가 3번 연속 나오면 중단
    
    while current_to >= START_KST:
        request_count += 1
        
        # 진행 로그
        if request_count % LOG_EVERY_N_REQUESTS == 0:
            elapsed = time.time() - start_time
            req_per_sec = request_count / elapsed if elapsed > 0 else 0
            bars_per_sec = total_bars / elapsed if elapsed > 0 else 0
            print(f"  [진행] to={current_to.strftime('%Y-%m-%d %H:%M')}, "
                  f"수집={total_bars}개, 요청={request_count}회, "
                  f"{req_per_sec:.1f} req/s, {bars_per_sec:.1f} bars/s")
        
        # API 호출
        candles = fetch_candles(market, to_time=current_to, count=200)
        
        if not candles or len(candles) == 0:
            print(f"  [완료] 데이터 없음 (신규 상장 또는 데이터 부재 가능)")
            break
        
        # DataFrame 변환
        df = process_candles(candles, market)
        
        if df.empty:
            print(f"  [완료] 변환된 데이터 없음 (범위 밖 또는 신규 상장)")
            break
        
        # 최소 시간 확인
        min_time = df["candle_time_kst"].min()
        
        # 무한 루프 방지: 같은 시간대가 연속으로 나오는지 체크
        if last_min_time is not None:
            if min_time >= last_min_time:
                # 같은 시간대 또는 더 최근 시간대가 나옴 = 더 이상 과거 데이터 없음
                same_time_count += 1
                if same_time_count >= MAX_SAME_TIME_COUNT:
                    print(f"  [완료] 더 이상 과거 데이터 없음 (신규 상장일: {min_time.strftime('%Y-%m-%d %H:%M')})")
                    break
            else:
                # 정상적으로 과거로 진행 중
                same_time_count = 0
        
        last_min_time = min_time
        total_bars += len(df)
        
        # 월별로 그룹화
        df["month_str"] = df["candle_time_kst"].dt.strftime("%Y-%m")
        for month_str, group_df in df.groupby("month_str"):
            if month_str not in monthly_chunks:
                monthly_chunks[month_str] = []
            monthly_chunks[month_str].append(group_df.drop(columns=["month_str"]))
        
        # 다음 배치를 위한 시각 업데이트
        # 최소 시간보다 1초 전으로 이동
        current_to = min_time - timedelta(seconds=1)
        
        # 범위 체크
        if min_time < START_KST:
            print(f"  [완료] 시작 시각 도달")
            break
        
        # 추가 안전장치: current_to가 더 이상 앞으로 가지 않으면 중단
        if current_to >= min_time:
            print(f"  [완료] 더 이상 과거로 진행 불가 (최소 시간: {min_time.strftime('%Y-%m-%d %H:%M')})")
            break
    
    # 월별로 저장
    saved_months = 0
    total_missing = 0
    
    for month_str, chunks in monthly_chunks.items():
        date_kst = datetime.strptime(month_str + "-01", "%Y-%m-%d")
        
        # 기존 데이터 로드
        existing_df = load_monthly_csv(DATA_ROOT, market, date_kst)
        
        # 병합 및 저장
        new_df = pd.concat(chunks, ignore_index=True)
        combined_df, meta = merge_monthly_data(
            existing_df, new_df, DATA_ROOT, META_ROOT, market, date_kst
        )
        
        # 미수집 로깅
        missing_hours_list = meta.get("missing_hours", [])
        if missing_hours_list:
            for missing_info in missing_hours_list:
                date_str = missing_info.get("date", "")
                hours = missing_info.get("hours", [])
                if hours:
                    total_missing += len(hours)
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
        "bars_collected": total_bars,
        "saved_months": saved_months,
        "total_missing_hours": total_missing,
        "elapsed_sec": elapsed,
    }
    
    if current_to < START_KST:
        status[market] = {
            "status": "DONE",
            "last_to_kst": START_KST.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at_kst": get_kst_now(),
            "stats": stats,
        }
        print(f"  [완료] {saved_months}개월 저장, 누락 {total_missing}시간")
        return True, stats
    else:
        status[market] = {
            "status": "IN_PROGRESS",
            "last_to_kst": current_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at_kst": get_kst_now(),
            "stats": stats,
        }
        print(f"  [진행 중] {saved_months}개월 저장, 누락 {total_missing}시간")
        return False, stats


def main():
    """메인 실행"""
    print("=" * 60)
    print("Upbit 2025년 1시간 캔들 수집기 (단순 버전)")
    print("=" * 60)
    print(f"시작 시각: {get_kst_now()}")
    print(f"수집 범위: {START_KST} ~ {END_KST} (KST)")
    print(f"요청 딜레이: {REQUEST_DELAY}초 (초당 약 {1/REQUEST_DELAY:.1f}회)")
    print()
    
    # 상태 로드
    status = load_status()
    
    # 마켓 목록 조회
    print("[1단계] 마켓 목록 조회 중...")
    all_markets = get_all_markets()
    if not all_markets:
        print("[오류] 마켓 목록을 가져올 수 없습니다.")
        return
    
    print(f"  총 {len(all_markets)}개 마켓 발견")
    
    # 재개 모드: DONE 마켓 제외
    remaining_markets = [
        m for m in all_markets
        if status.get(m, {}).get("status") != "DONE"
    ]
    print(f"  처리 대상: {len(remaining_markets)}개 마켓 (완료: {len(all_markets) - len(remaining_markets)}개)")
    
    if not remaining_markets:
        print("\n[완료] 모든 마켓 수집 완료!")
        return
    
    # 수집 실행
    all_stats = []
    failed_markets = []
    
    for i, market in enumerate(remaining_markets, 1):
        try:
            success, stats = collect_market(market, status)
            all_stats.append({"market": market, **stats})
            
            if not success:
                failed_markets.append(market)
            
            # 상태 저장
            save_status(status)
            
        except Exception as e:
            print(f"  [예외] {e}")
            failed_markets.append(market)
            status[market] = {
                "status": "FAILED",
                "error": str(e),
                "updated_at_kst": get_kst_now(),
            }
            save_status(status)
    
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
        total_elapsed = sum(s.get("elapsed_sec", 0) for s in all_stats)
        
        print(f"\n[성능 통계]")
        print(f"  총 요청: {total_requests}회")
        print(f"  총 수집: {total_bars}개 바")
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

