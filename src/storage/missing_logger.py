"""미수집 내역 로깅 모듈"""
import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime


def log_missing_summary(
    meta_dir: Path,
    market: str,
    date_kst: datetime,
    missing_hours: List[str],
    rows_saved: int,
):
    """
    미수집 요약을 JSONL 파일에 기록
    
    Args:
        meta_dir: 메타 디렉토리
        market: 마켓 코드
        date_kst: 날짜
        missing_hours: 누락된 시간 리스트 (예: ["03:00", "14:00"])
        rows_saved: 저장된 행 수
    """
    summary_file = meta_dir / "missing_summary_2025_1h.jsonl"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    
    record = {
        "market": market,
        "date": date_kst.strftime("%Y-%m-%d"),
        "missing_hours": missing_hours,
        "rows_saved": rows_saved,
        "updated_at_kst": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_missing_summary(meta_dir: Path) -> List[Dict]:
    """미수집 요약 로드"""
    summary_file = meta_dir / "missing_summary_2025_1h.jsonl"
    if not summary_file.exists():
        return []
    
    records = []
    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
    except:
        pass
    
    return records


def get_missing_top_n(meta_dir: Path, n: int = 10) -> List[Dict]:
    """누락이 많은 상위 N개 반환"""
    records = load_missing_summary(meta_dir)
    
    # 누락 시간 수 기준 정렬
    records_sorted = sorted(records, key=lambda x: len(x.get("missing_hours", [])), reverse=True)
    
    return records_sorted[:n]

