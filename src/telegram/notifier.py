"""텔레그램 알림 모듈"""
import requests
from typing import Dict, Optional, List
from rich.console import Console

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.telegram.message_format import format_buy_message, format_buy_signals_batch, format_sell_message, format_no_signal_message

console = Console()


class TelegramNotifier:
    """텔레그램 알림 클래스"""
    
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
    
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송"""
        if not self.bot_token or not self.chat_id:
            console.print("[red]텔레그램 봇 토큰 또는 채팅 ID가 설정되지 않았습니다.[/red]")
            return False
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            console.print(f"[red]텔레그램 전송 실패: {e}[/red]")
            return False
    
    def send_buy_signal(self, signal: Dict, ticker_info: Optional[Dict] = None) -> bool:
        """매수 시그널 전송"""
        message = format_buy_message(signal, ticker_info)
        return self.send_message(message)
    
    def send_buy_signals_batch(self, signals: List[tuple], current_time: Optional[str] = None, total_market_volume: Optional[float] = None) -> bool:
        """여러 매수 시그널을 하나의 메시지로 전송"""
        message = format_buy_signals_batch(signals, current_time, total_market_volume)
        if not message:
            return False
        return self.send_message(message)
    
    def send_sell_signal(self, signal: Dict, ticker_info: Optional[Dict] = None) -> bool:
        """매도 시그널 전송"""
        message = format_sell_message(signal, ticker_info)
        return self.send_message(message)
    
    def send_no_signal(
        self,
        date_str: str,
        timeframes: List[str],
    ) -> bool:
        """시그널 없음 메시지 전송"""
        message = format_no_signal_message(date_str, timeframes)
        return self.send_message(message)

