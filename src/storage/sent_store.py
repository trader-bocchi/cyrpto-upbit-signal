"""전송된 시그널 저장소 모듈 (중복 알림 방지)"""
import json
from pathlib import Path
from typing import Set, Tuple, Optional

from src.config import META_DATA_PATH


SENT_SIGNALS_FILE = META_DATA_PATH / "sent_signals.jsonl"


def get_signal_key(
    market: str,
    timeframe: str,
    signal_time_kst: str,
    side: str,
    reason: Optional[str] = None,
) -> str:
    """시그널 키 생성"""
    if reason:
        return f"{market}_{timeframe}_{signal_time_kst}_{side}_{reason}"
    return f"{market}_{timeframe}_{signal_time_kst}_{side}"


def load_sent_signals() -> Set[str]:
    """전송된 시그널 키 세트 로드"""
    if not SENT_SIGNALS_FILE.exists():
        return set()
    
    keys = set()
    try:
        with open(SENT_SIGNALS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    keys.add(data.get("key", ""))
    except Exception:
        pass
    
    return keys


def mark_signal_sent(
    market: str,
    timeframe: str,
    signal_time_kst: str,
    side: str,
    reason: Optional[str] = None,
):
    """시그널 전송 표시"""
    key = get_signal_key(market, timeframe, signal_time_kst, side, reason)
    
    SENT_SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(SENT_SIGNALS_FILE, "a", encoding="utf-8") as f:
        json.dump({"key": key, "timestamp": signal_time_kst}, f, ensure_ascii=False)
        f.write("\n")


def is_signal_sent(
    market: str,
    timeframe: str,
    signal_time_kst: str,
    side: str,
    reason: Optional[str] = None,
) -> bool:
    """시그널 전송 여부 확인"""
    key = get_signal_key(market, timeframe, signal_time_kst, side, reason)
    sent_keys = load_sent_signals()
    return key in sent_keys

