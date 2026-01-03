"""고속 Upbit API 클라이언트 (레이트리밋 최대 활용, 개선 버전)"""
import time
import random
import requests
from typing import List, Dict, Optional
from datetime import datetime
from rich.console import Console

from src.config import KST_OFFSET_HOURS

console = Console()


class FastUpbitClient:
    """고속 Upbit API 클라이언트 (429 적응형 감속, Rate Limit 예방)"""
    
    def __init__(
        self,
        base_sleep: float = 0.15,  # 기본 sleep (0.1 -> 0.15로 증가하여 자연스러운 속도 제한)
        request_timeout: int = 30,  # 타임아웃 증가 (20 -> 30초, 1d 캔들 수집 시 더 긴 응답 시간 고려)
        max_retries: int = 10,
        backoff_base: float = 2.0,
    ):
        self.base_url = "https://api.upbit.com/v1"
        self.session = requests.Session()
        self.base_sleep = base_sleep
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        
        # 429 적응형 감속 상태
        self.rate_limit_mode = False
        self.rate_limit_until = None
        self.rate_limit_sleep = 0.5  # 속도 제한 모드 시 sleep 증가
        
        # 연속 요청 추적 (429 발생 시에만 사용)
        self.last_request_time = 0
        self.consecutive_requests = 0
        self.consecutive_429_count = 0  # 연속 429 발생 횟수
    
    def _should_slow_down(self) -> bool:
        """속도 제한 모드 여부 확인"""
        if self.rate_limit_mode and self.rate_limit_until:
            if time.time() < self.rate_limit_until:
                return True
            else:
                # 정상 모드로 복귀
                self.rate_limit_mode = False
                self.rate_limit_until = None
                self.consecutive_429_count = 0
        return False
    
    def _prevent_rate_limit(self):
        """Rate Limit 예방 로직 (간소화: 윈도우 제한 제거, base_sleep만 사용)"""
        current_time = time.time()
        
        # 연속 요청 간 최소 간격 보장 (base_sleep)
        if self.last_request_time > 0:
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.base_sleep:
                time.sleep(self.base_sleep - time_since_last)
                current_time = time.time()  # sleep 후 시간 업데이트
        
        self.last_request_time = current_time
        self.consecutive_requests += 1
        
        # 연속 요청이 많으면 짧은 휴식 (100개마다)
        if self.consecutive_requests >= 100:
            self.consecutive_requests = 0
            time.sleep(0.1)  # 짧은 휴식
    
    def _handle_429(self, attempt: int):
        """429 Rate Limit 처리"""
        self.consecutive_429_count += 1
        
        # 즉시 2~5초 랜덤 대기
        immediate_wait = random.uniform(2, 5)
        time.sleep(immediate_wait)
        
        # 지수 백오프
        backoff = self.backoff_base ** attempt + random.uniform(0, 2)
        time.sleep(backoff)
        
        # 연속 429 발생 시 더 강한 제한
        if self.consecutive_429_count >= 2:
            # 속도 제한 모드 활성화 (120초로 증가)
            self.rate_limit_mode = True
            self.rate_limit_until = time.time() + 120
            console.print(f"[yellow]429 연속 발생 ({self.consecutive_429_count}회), 속도 제한 모드 활성화 (120초)[/yellow]")
        else:
            # 첫 429는 짧은 제한만
            self.rate_limit_mode = True
            self.rate_limit_until = time.time() + 60
            console.print(f"[yellow]429 Rate Limit 감지, {backoff:.2f}초 대기 후 속도 제한 모드 활성화 (60초)[/yellow]")
        
        # 요청 카운터 리셋
        self.consecutive_requests = 0
    
    def _request_with_retry(
        self, method: str, endpoint: str, params: Optional[Dict] = None
    ) -> Optional[Dict]:
        """재시도 로직 포함 요청"""
        url = f"{self.base_url}/{endpoint}"
        
        # 디버깅: 요청 정보 로그 (days 엔드포인트인 경우)
        if endpoint == "candles/days":
            # 실제 요청 URL 구성 (params를 query string으로 변환)
            if params:
                from urllib.parse import urlencode
                query_string = urlencode(params)
                full_url = f"{url}?{query_string}"
            else:
                full_url = url
            console.print(f"[dim]    [DEBUG] 요청 URL: {full_url}[/dim]")
            console.print(f"[dim]    [DEBUG] 요청 파라미터: {params}[/dim]")
        
        for attempt in range(self.max_retries):
            try:
                # Rate Limit 예방
                if not self._should_slow_down():
                    self._prevent_rate_limit()
                else:
                    # 속도 제한 모드에서는 더 긴 대기
                    time.sleep(self.rate_limit_sleep)
                
                # 요청 전 시간 기록
                request_start = time.time()
                if endpoint == "candles/days" and attempt == 0:
                    console.print(f"[dim]    [DEBUG] API 요청 시작 (시도 {attempt + 1}/{self.max_retries})...[/dim]")
                
                response = self.session.request(method, url, params=params, timeout=self.request_timeout)
                request_elapsed = time.time() - request_start
                
                # 디버깅: 응답 정보 로그 (days 엔드포인트인 경우)
                if endpoint == "candles/days":
                    console.print(f"[dim]    [DEBUG] 응답 상태: {response.status_code}, 소요 시간: {request_elapsed:.2f}초[/dim]")
                
                # 429 처리
                if response.status_code == 429:
                    self._handle_429(attempt)
                    continue
                
                if response.status_code == 200:
                    # 정상 응답이면 429 카운터 리셋
                    if self.consecutive_429_count > 0:
                        self.consecutive_429_count = 0
                    try:
                        result = response.json()
                        # 디버깅: 응답 데이터 로그 (days 엔드포인트인 경우, 첫 번째 시도만)
                        if endpoint == "candles/days" and attempt == 0:
                            data_count = len(result) if isinstance(result, list) else 0
                            console.print(f"[dim]    [DEBUG] 응답 데이터 개수: {data_count}[/dim]")
                        return result
                    except ValueError as e:
                        # JSON 파싱 실패
                        console.print(f"[red]JSON 파싱 실패: {e}[/red]")
                        console.print(f"[red]응답 내용: {response.text[:200]}[/red]")
                        return None
                else:
                    # 에러 응답 로그
                    if endpoint == "candles/days":
                        console.print(f"[red]    [DEBUG] 에러 응답: {response.status_code}[/red]")
                        console.print(f"[red]    [DEBUG] 응답 내용: {response.text[:200]}[/red]")
                    response.raise_for_status()
                    
            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    backoff = self.backoff_base ** attempt + random.uniform(0, 0.5)
                    time.sleep(backoff)
                else:
                    console.print(f"[red]Timeout 최종 실패[/red]")
                    return None
                    
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    backoff = self.backoff_base ** attempt + random.uniform(0, 0.5)
                    time.sleep(backoff)
                else:
                    console.print(f"[red]요청 실패: {e}[/red]")
                    return None
        
        return None
    
    def get_markets(self, is_details: bool = False) -> List[Dict]:
        """마켓 목록 조회"""
        params = {"isDetails": str(is_details).lower()}
        result = self._request_with_retry("GET", "market/all", params)
        if result:
            return [m for m in result if m.get("market", "").startswith("KRW-")]
        return []
    
    def get_candles_minutes(
        self,
        market: str,
        unit: int,
        to: Optional[str] = None,
        count: int = 200,
    ) -> List[Dict]:
        """
        분 단위 캔들 조회 (unit에 따라 1h, 4h, 1d 등)
        
        Args:
            market: 마켓 코드
            unit: 분 단위 (60=1h, 240=4h, 1440=1d)
            to: 마지막 캔들 시각 (ISO8601)
            count: 조회할 캔들 개수 (최대 200)
        """
        params = {
            "market": market,
            "count": min(count, 200),  # 최대 200
        }
        
        # 1일(1440분) 캔들은 candles/days 엔드포인트 사용
        if unit == 1440:
            endpoint = "candles/days"
            # days 엔드포인트는 to 파라미터를 ISO8601 형식(YYYY-MM-DDTHH:MM:SS)으로 사용
            # 참고: https://api.upbit.com/v1/candles/days?market=KRW-WAXP&count=200&to=2026-01-04T00:00:00
            if to:
                # 이미 ISO8601 형식인 경우 그대로 사용
                # 날짜 형식(YYYY-MM-DD)인 경우 시간을 추가하여 ISO8601 형식으로 변환
                if "T" in to:
                    # 이미 ISO8601 형식
                    params["to"] = to
                elif " " in to:
                    # 공백으로 구분된 형식인 경우 T로 변환
                    params["to"] = to.replace(" ", "T")
                else:
                    # 날짜 형식만 있는 경우 시간 추가 (00:00:00)
                    params["to"] = f"{to}T00:00:00"
            # days 엔드포인트는 unit 파라미터를 사용하지 않음 (이미 endpoint에 포함됨)
        else:
            endpoint = f"candles/minutes/{unit}"
            params["unit"] = unit
            if to:
                params["to"] = to
        
        # 디버깅: days 엔드포인트인 경우 요청 정보 출력
        if unit == 1440:
            console.print(f"[dim]    [DEBUG] Days 엔드포인트 호출: {endpoint}, params: {params}[/dim]")
        
        result = self._request_with_retry("GET", endpoint, params)
        
        # 디버깅: days 엔드포인트인 경우 결과 확인
        if unit == 1440:
            if result:
                console.print(f"[dim]    [DEBUG] Days 엔드포인트 응답: {len(result)}개 캔들[/dim]")
            else:
                console.print(f"[yellow]    [DEBUG] Days 엔드포인트 응답: None 또는 빈 리스트[/yellow]")
        
        return result if result else []
