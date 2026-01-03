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
        df = pd.read_csv(filepath, **kwargs)
        # SMI 파일인 경우 float64로 명시적 변환 (정밀도 보장)
        if "smi_momentum" in df.columns:
            df["smi_momentum"] = pd.to_numeric(df["smi_momentum"], errors='coerce').astype("float64")
        if "squeeze_on" in df.columns:
            df["squeeze_on"] = df["squeeze_on"].astype("bool")
        return df
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
                # datetime 변환 및 timezone 처리
                try:
                    # 먼저 datetime으로 변환 시도
                    if not pd.api.types.is_datetime64_any_dtype(df[col]):
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                    
                    # datetime 변환이 성공했는지 확인
                    if pd.api.types.is_datetime64_any_dtype(df[col]):
                        # timezone 처리
                        if df[col].dtype.tz is None:
                            # timezone이 없으면 Asia/Seoul로 설정
                            df[col] = df[col].dt.tz_localize("Asia/Seoul", ambiguous='infer', nonexistent='shift_forward')
                        elif str(df[col].dtype.tz) != "Asia/Seoul":
                            # 다른 timezone이면 Asia/Seoul로 변환
                            df[col] = df[col].dt.tz_convert("Asia/Seoul")
                    else:
                        # datetime 변환 실패 시 다시 시도 (utc=True 옵션 사용)
                        df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)
                        if pd.api.types.is_datetime64_any_dtype(df[col]):
                            df[col] = df[col].dt.tz_convert("Asia/Seoul")
                except Exception as e:
                    # 모든 변환 실패 시 경고만 출력하지 않음 (warning 숨김)
                    pass
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
    elif timeframe in ["1d", "24h", "d"]:
        # 1d = 24h = 일봉
        if year:
            return base_path / "candles_1d" / f"market={market}" / f"{year}.csv"
        else:
            return base_path / "candles_1d" / f"market={market}" / f"{market}_1d.csv"
    else:
        raise ValueError(f"Unknown timeframe: {timeframe}")

