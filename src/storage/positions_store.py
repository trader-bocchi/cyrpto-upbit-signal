"""포지션 저장소 모듈"""
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from src.config import META_DATA_PATH


POSITIONS_FILE = META_DATA_PATH / "positions_live.json"


def load_positions() -> Dict[str, Dict]:
    """포지션 로드"""
    if not POSITIONS_FILE.exists():
        return {}
    
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_positions(positions: Dict[str, Dict]):
    """포지션 저장"""
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)


def get_position_key(market: str, timeframe: str) -> str:
    """포지션 키 생성"""
    return f"{market}_{timeframe}"


def add_position(
    market: str,
    timeframe: str,
    entry_time_kst: str,
    entry_price: float,
    qty: float,
    invested_krw: float,
):
    """포지션 추가"""
    positions = load_positions()
    key = get_position_key(market, timeframe)
    
    positions[key] = {
        "market": market,
        "timeframe": timeframe,
        "entry_time_kst": entry_time_kst,
        "entry_price": entry_price,
        "qty": qty,
        "invested_krw": invested_krw,
    }
    
    save_positions(positions)


def remove_position(market: str, timeframe: str):
    """포지션 제거"""
    positions = load_positions()
    key = get_position_key(market, timeframe)
    
    if key in positions:
        del positions[key]
        save_positions(positions)


def get_position(market: str, timeframe: str) -> Optional[Dict]:
    """포지션 조회"""
    positions = load_positions()
    key = get_position_key(market, timeframe)
    return positions.get(key)


def get_all_positions() -> List[Dict]:
    """모든 포지션 조회"""
    positions = load_positions()
    return list(positions.values())

