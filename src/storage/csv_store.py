"""CSV 저장소 모듈 (원자적 쓰기, dtype 관리)"""
import pandas as pd
import tempfile
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from src.config import KST_OFFSET_HOURS


def ensure_dir(path: Path):
    """디렉토리 생성"""
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_csv(df: pd.DataFrame, filepath: Path, **kwargs):
    """
    원자적 CSV 쓰기 (임시 파일 → rename)
    
    Args:
        df: 저장할 DataFrame
        filepath: 저장 경로
        **kwargs: pandas.to_csv() 추가 인자
    """
    ensure_dir(filepath.parent)
    
    # 임시 파일 생성
    temp_file = filepath.with_suffix(".tmp")
    
    try:
        df.to_csv(temp_file, index=False, **kwargs)
        # 원자적 이동
        temp_file.replace(filepath)
    except Exception as e:
        if temp_file.exists():
            temp_file.unlink()
        raise e


def read_csv_safe(filepath: Path, **kwargs) -> pd.DataFrame:
    """CSV 안전 읽기 (파일 없으면 빈 DataFrame 반환)"""
    if not filepath.exists():
        return pd.DataFrame()
    
    try:
        return pd.read_csv(filepath, **kwargs)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def parse_candle_time_kst(time_str: str) -> pd.Timestamp:
    """KST 시간 문자열을 Timestamp로 파싱"""
    return pd.to_datetime(time_str).tz_localize("Asia/Seoul")


def ensure_candle_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """캔들 DataFrame의 dtype 보장"""
    if df.empty:
        return df
    
    # 필수 컬럼
    required_cols = {
        "market": "string",
        "candle_time_kst": "datetime64[ns, Asia/Seoul]",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "float64",
    }
    
    # 컬럼 타입 변환
    for col, dtype in required_cols.items():
        if col in df.columns:
            if col == "candle_time_kst":
                if df[col].dtype != dtype:
                    df[col] = pd.to_datetime(df[col]).dt.tz_localize("Asia/Seoul")
            else:
                df[col] = df[col].astype(dtype)
    
    return df


def get_candle_filepath(
    base_path: Path,
    market: str,
    timeframe: str = "1h",
    year: Optional[int] = None,
) -> Path:
    """
    캔들 파일 경로 생성
    
    Args:
        base_path: 기본 경로 (raw 또는 derived)
        market: 마켓 코드
        timeframe: 시간프레임 (1h, 4h, 1d)
        year: 연도 (None이면 단일 파일)
    """
    if timeframe == "1h":
        if year:
            return base_path / "candles_1h" / f"market={market}" / f"year={year}" / f"{year}.csv"
        else:
            return base_path / "candles_1h" / f"market={market}" / f"{market}_1h.csv"
    elif timeframe == "4h":
        if year:
            return base_path / "candles_4h" / f"market={market}" / f"{year}.csv"
        else:
            return base_path / "candles_4h" / f"market={market}" / f"{market}_4h.csv"
    elif timeframe == "1d":
        if year:
            return base_path / "candles_1d" / f"market={market}" / f"{year}.csv"
        else:
            return base_path / "candles_1d" / f"market={market}" / f"{market}_1d.csv"
    else:
        raise ValueError(f"Unknown timeframe: {timeframe}")

