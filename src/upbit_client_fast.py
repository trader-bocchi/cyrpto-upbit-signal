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
        request_timeout: int = 20,
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
        
        for attempt in range(self.max_retries):
            try:
                # Rate Limit 예방
                if not self._should_slow_down():
                    self._prevent_rate_limit()
                else:
                    # 속도 제한 모드에서는 더 긴 대기
                    time.sleep(self.rate_limit_sleep)
                
                response = self.session.request(method, url, params=params, timeout=self.request_timeout)
                
                # 429 처리
                if response.status_code == 429:
                    self._handle_429(attempt)
                    continue
                
                if response.status_code == 200:
                    # 정상 응답이면 429 카운터 리셋
                    if self.consecutive_429_count > 0:
                        self.consecutive_429_count = 0
                    return response.json()
                else:
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
            "unit": unit,
            "count": min(count, 200),  # 최대 200
        }
        if to:
            params["to"] = to
        
        # unit에 따라 엔드포인트 동적 생성
        endpoint = f"candles/minutes/{unit}"
        result = self._request_with_retry("GET", endpoint, params)
        return result if result else []
