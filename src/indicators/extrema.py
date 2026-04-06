"""극값(로컬 미니멈/맥시멈) 계산"""
import pandas as pd
import numpy as np


def find_local_minima(series: pd.Series, window: int = 1) -> pd.Series:
    """
    로컬 미니멈 찾기
    
    Args:
        series: 입력 시리즈
        window: 주변 확인 범위 (window=1이면 이전/다음 1개씩 확인)
    
    Returns:
        로컬 미니멈인지 여부를 나타내는 boolean Series
    """
    is_local_min = pd.Series(False, index=series.index)
    
    for i in range(window, len(series) - window):
        if series.iloc[i] < series.iloc[i - window] and series.iloc[i] < series.iloc[i + window]:
            is_local_min.iloc[i] = True
    
    return is_local_min


def find_pivot_min(
    series: pd.Series,
    window: int = 100,
    require_negative: bool = True,
) -> pd.DataFrame:
    """
    최근 window 내에서 최저 로컬 미니멈(피벗) 찾기
    
    Args:
        series: 입력 시리즈 (예: SMI momentum)
        window: 확인할 윈도우 크기
        require_negative: 피벗이 음수여야 하는지 여부
    
    Returns:
        각 인덱스별 피벗 정보 DataFrame
        - pivot_min: 피벗 값
        - pivot_idx: 피벗 인덱스
        - is_pivot: 피벗인지 여부
    """
    result = pd.DataFrame(index=series.index)
    result["pivot_min"] = np.nan
    result["pivot_idx"] = -1
    result["is_pivot"] = False
    
    local_mins = find_local_minima(series, window=1)
    
    for i in range(window, len(series)):
        # 최근 window 내 로컬 미니멈들
        window_start = max(0, i - window)
        window_series = series.iloc[window_start:i + 1]
        window_local_mins = local_mins.iloc[window_start:i + 1]
        
        local_min_indices = window_series[window_local_mins].index
        
        if len(local_min_indices) == 0:
            continue
        
        pivot_candidates = window_series.loc[local_min_indices]
        
        if require_negative:
            pivot_candidates = pivot_candidates[pivot_candidates < 0]
        
        if len(pivot_candidates) == 0:
            continue

        # 가장 최근 로컬 미니멈을 피벗으로 선택 (가장 깊은 값이 아닌, 가장 최근 발생한 것)
        pivot_idx = pivot_candidates.index[-1]
        pivot_value = pivot_candidates.iloc[-1]
        
        result.loc[series.index[i], "pivot_min"] = pivot_value
        result.loc[series.index[i], "pivot_idx"] = series.index.get_loc(pivot_idx)
        result.loc[series.index[i], "is_pivot"] = (series.index[i] == pivot_idx)
    
    return result

