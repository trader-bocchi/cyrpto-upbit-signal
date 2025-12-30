"""체크포인트 관리 모듈"""
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

from src.config import META_DATA_PATH


CHECKPOINT_FILE = META_DATA_PATH / "checkpoints.json"


def load_checkpoints() -> Dict[str, str]:
    """체크포인트 로드"""
    if not CHECKPOINT_FILE.exists():
        return {}
    
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_checkpoint(market: str, last_timestamp_kst: str):
    """체크포인트 저장"""
    checkpoints = load_checkpoints()
    checkpoints[market] = last_timestamp_kst
    
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoints, f, indent=2, ensure_ascii=False)


def get_checkpoint(market: str) -> Optional[str]:
    """마켓별 체크포인트 조회"""
    checkpoints = load_checkpoints()
    return checkpoints.get(market)

