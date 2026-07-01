"""발송 감사 로그 — 전송한 메시지와 그 근거(시그널 데이터)를 함께 기록.

매 발송마다 logs/dispatch_YYYYMMDD.jsonl 에 한 줄(JSON)씩 남긴다.
각 레코드: 발송 시각, 성공 여부, 시그널 근거 데이터(종목/가격/SMI/강도 등), 실제 전송 메시지 원문.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

from src.config import PROJECT_ROOT

KST = timezone(timedelta(hours=9))
LOG_DIR = PROJECT_ROOT / "logs"


def _serialize(signal_groups: Dict[str, List[Tuple[str, Dict]]]) -> Dict[str, List[Dict]]:
    """{그룹명: [(market, signal_dict), ...]} → JSON 직렬화 가능한 형태."""
    out: Dict[str, List[Dict]] = {}
    for name, sigs in signal_groups.items():
        out[name] = [{"market": market, **signal} for market, signal in sigs]
    return out


def log_dispatch(
    message: str,
    signal_groups: Dict[str, List[Tuple[str, Dict]]],
    success: bool,
) -> None:
    """발송 1건을 감사 로그에 기록.

    Args:
        message: 실제 전송된(또는 시도된) 메시지 원문(HTML)
        signal_groups: {"buy_4h": [...], "sell_4h": [...], "buy_1d": [...], "sell_1d": [...]}
        success: 텔레그램 전송 성공 여부
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    record = {
        "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "success": success,
        "counts": {name: len(sigs) for name, sigs in signal_groups.items()},
        "signals": _serialize(signal_groups),
        "message": message,
    }
    path = LOG_DIR / f"dispatch_{now.strftime('%Y%m%d')}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
