"""Upbit API 클라이언트"""
import time
import requests
from typing import List, Dict, Optional
from datetime import datetime
from rich.console import Console
from rich.progress import track

from src.config import (
    UPBIT_API_BASE,
    UPBIT_RATE_LIMIT_RETRIES,
    UPBIT_RATE_LIMIT_BACKOFF_BASE,
    KST_OFFSET_HOURS,
)

console = Console()


class UpbitClient:
    """Upbit API 클라이언트 (Public API만 사용)"""

    def __init__(self):
        self.base_url = UPBIT_API_BASE
        self.session = requests.Session()

    def _request_with_retry(
        self, method: str, endpoint: str, params: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Rate limit을 고려한 재시도 로직"""
        url = f"{self.base_url}/{endpoint}"
        
        for attempt in range(UPBIT_RATE_LIMIT_RETRIES):
            try:
                response = self.session.request(method, url, params=params, timeout=10)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    # Rate limit 초과
                    backoff = UPBIT_RATE_LIMIT_BACKOFF_BASE ** attempt
                    console.print(
                        f"[yellow]Rate limit 초과. {backoff:.1f}초 대기 후 재시도...[/yellow]"
                    )
                    time.sleep(backoff)
                else:
                    response.raise_for_status()
            except requests.exceptions.RequestException as e:
                if attempt == UPBIT_RATE_LIMIT_RETRIES - 1:
                    console.print(f"[red]API 요청 실패: {e}[/red]")
                    return None
                backoff = UPBIT_RATE_LIMIT_BACKOFF_BASE ** attempt
                time.sleep(backoff)
        
        return None

    def get_markets(self, is_details: bool = False) -> List[Dict]:
        """마켓 목록 조회"""
        params = {"isDetails": str(is_details).lower()}
        result = self._request_with_retry("GET", "market/all", params)
        if result:
            # KRW 마켓만 필터링
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
        1시간 캔들 조회 (unit=60)
        
        Args:
            market: 마켓 코드 (예: KRW-BTC)
            unit: 분 단위 (60 = 1시간)
            to: 마지막 캔들 시각 (ISO8601, 미지정 시 현재)
            count: 조회할 캔들 개수 (최대 200)
        """
        params = {
            "market": market,
            "unit": unit,
            "count": min(count, 200),
        }
        if to:
            params["to"] = to
        
        result = self._request_with_retry("GET", "candles/minutes/60", params)
        return result if result else []

    def get_ticker(self, markets: List[str]) -> List[Dict]:
        """현재가 정보 조회 (24시간 거래대금 포함)"""
        if not markets:
            return []
        
        params = {"markets": ",".join(markets)}
        result = self._request_with_retry("GET", "ticker", params)
        return result if result else []

    def get_ticker_all_krw(self) -> List[Dict]:
        """모든 KRW 마켓의 현재가 정보 조회"""
        markets = self.get_markets()
        market_codes = [m["market"] for m in markets]
        
        # Upbit API는 한 번에 최대 100개 마켓 조회 가능
        all_tickers = []
        for i in range(0, len(market_codes), 100):
            batch = market_codes[i : i + 100]
            tickers = self.get_ticker(batch)
            all_tickers.extend(tickers)
            time.sleep(0.1)  # Rate limit 고려
        
        return all_tickers

    @staticmethod
    def timestamp_to_kst_str(timestamp_ms: int) -> str:
        """타임스탬프(ms)를 KST 문자열로 변환"""
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        # UTC에서 KST로 변환
        kst_dt = dt.replace(hour=(dt.hour + KST_OFFSET_HOURS) % 24)
        return kst_dt.strftime("%Y-%m-%d %H:%M:%S")

