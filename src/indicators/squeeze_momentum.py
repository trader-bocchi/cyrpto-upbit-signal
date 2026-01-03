"""Squeeze Momentum Indicator (SMI) - TradingView Pine Script 원본 기반"""
import pandas as pd
import numpy as np

from src.config import (
    SMI_BB_LENGTH,
    SMI_BB_MULT,
    SMI_KC_LENGTH,
    SMI_KC_MULT,
)


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    """
    Average True Range (ATR) 계산
    
    Args:
        high: 고가 시리즈
        low: 저가 시리즈
        close: 종가 시리즈
        window: 기간
    
    Returns:
        ATR 시리즈
    """
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window, min_periods=1).mean()
    
    return atr


def linreg(series: pd.Series, length: int, offset: int = 0) -> pd.Series:
    """
    TradingView linreg 함수 구현
    linreg(series, length, offset) - 선형 회귀의 현재 값 반환
    
    Args:
        series: 입력 시리즈
        length: 회귀 기간
        offset: 오프셋 (0=현재, 1=1 bar 전)
    
    Returns:
        각 시점의 선형 회귀 값
    """
    result = pd.Series(index=series.index, dtype=float)
    
    for i in range(len(series)):
        if i < length - 1 + offset:
            result.iloc[i] = np.nan
        else:
            # 최근 length개의 데이터 (offset만큼 뒤로 이동)
            start_idx = i - length + 1 - offset
            end_idx = i + 1 - offset
            y = series.iloc[start_idx : end_idx].values
            
            if len(y) < length or np.all(np.isnan(y)):
                result.iloc[i] = np.nan
                continue
            
            x = np.arange(length)
            
            # 선형 회귀 (최소 제곱법)
            # y = a + b*x 형태에서 x = length - 1 - offset일 때의 y 값
            n = length
            sum_x = np.sum(x)
            sum_y = np.sum(y[~np.isnan(y)])
            valid_mask = ~np.isnan(y)
            x_valid = x[valid_mask]
            y_valid = y[valid_mask]
            
            if len(y_valid) < 2:
                result.iloc[i] = np.nan
                continue
            
            sum_xy = np.sum(x_valid * y_valid)
            sum_x2 = np.sum(x_valid * x_valid)
            
            denominator = n * sum_x2 - sum_x * sum_x
            if abs(denominator) > 1e-10:
                # 기울기 b
                b = (n * sum_xy - sum_x * sum_y) / denominator
                # 절편 a
                a = (sum_y - b * sum_x) / n
                # 현재 시점의 선형 회귀 값 (x = length - 1 - offset)
                x_val = length - 1 - offset
                result.iloc[i] = a + b * x_val
            else:
                result.iloc[i] = np.nan
    
    return result


def calculate_squeeze_momentum(df: pd.DataFrame, use_true_range: bool = True) -> pd.DataFrame:
    """
    Squeeze Momentum Index를 계산합니다.
    TradingView Pine Script 원본과 동일한 로직
    
    Args:
        df: OHLCV DataFrame
        use_true_range: True Range 사용 여부 (기본 True)
    
    Returns:
        smi_momentum, squeeze_on 컬럼이 추가된 DataFrame
    """
    result_df = df.copy()
    
    # 파라미터 설정 (Pine Script와 동일)
    length = SMI_BB_LENGTH  # 20
    mult = SMI_BB_MULT  # 2.0
    lengthKC = SMI_KC_LENGTH  # 20
    multKC = SMI_KC_MULT  # 1.5
    
    # 최소 데이터 요구사항
    min_required = max(length, lengthKC)
    
    if len(result_df) < min_required:
        result_df["smi_momentum"] = np.nan
        result_df["squeeze_on"] = False
        return result_df
    
    # Pine Script: source = close
    source = result_df['close']
    
    # Calculate BB (Pine Script와 동일)
    # basis = sma(source, length)
    basis = source.rolling(window=length, min_periods=1).mean()
    # dev = mult * stdev(source, length)  # Pine Script 원본: mult 사용 (multKC가 아님)
    dev = mult * source.rolling(window=length, min_periods=1).std()
    upperBB = basis + dev
    lowerBB = basis - dev
    
    # Calculate KC (Pine Script와 동일)
    # ma = sma(source, lengthKC)
    ma = source.rolling(window=lengthKC, min_periods=1).mean()
    # range = useTrueRange ? tr : (high - low)
    if use_true_range:
        tr = calculate_atr(result_df['high'], result_df['low'], result_df['close'], lengthKC)
        range_val = tr
    else:
        range_val = result_df['high'] - result_df['low']
    # rangema = sma(range, lengthKC)
    rangema = range_val.rolling(window=lengthKC, min_periods=1).mean()
    upperKC = ma + rangema * multKC
    lowerKC = ma - rangema * multKC
    
    # Squeeze 상태 계산 (Pine Script와 동일)
    sqzOn = (lowerBB > lowerKC) & (upperBB < upperKC)
    sqzOff = (lowerBB < lowerKC) & (upperBB > upperKC)
    noSqz = ~sqzOn & ~sqzOff
    
    result_df['squeeze_on'] = sqzOn
    result_df['squeeze_off'] = sqzOff
    result_df['no_squeeze'] = noSqz
    
    # Momentum 계산 (Pine Script와 동일)
    # val = linreg(source - avg(avg(highest(high, lengthKC), lowest(low, lengthKC)), sma(close, lengthKC)), lengthKC, 0)
    highest_high = result_df['high'].rolling(window=lengthKC, min_periods=1).max()
    lowest_low = result_df['low'].rolling(window=lengthKC, min_periods=1).min()
    avg_hl = (highest_high + lowest_low) / 2
    sma_close = source.rolling(window=lengthKC, min_periods=1).mean()
    avg_hlc = (avg_hl + sma_close) / 2
    
    # source - avg_hlc
    diff_series = source - avg_hlc
    
    # linreg(diff_series, lengthKC, 0)
    val = linreg(diff_series, lengthKC, offset=0)
    
    result_df['squeeze_momentum'] = val
    
    # 초기 데이터 부족 구간은 NaN으로 설정
    result_df.loc[:min_required-1, "squeeze_momentum"] = np.nan
    
    return result_df


def calculate_smi(df: pd.DataFrame) -> pd.DataFrame:
    """
    SMI 계산 및 squeeze 상태 추가
    TradingView Pine Script 원본과 동일한 로직
    
    Args:
        df: OHLCV DataFrame
    
    Returns:
        smi_momentum, squeeze_on 컬럼이 추가된 DataFrame
    """
    # 필수 컬럼 확인
    required_cols = ['high', 'low', 'close']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        df["smi_momentum"] = np.nan
        df["squeeze_on"] = False
        return df
    
    # Squeeze Momentum 계산 (useTrueRange=True가 기본값)
    result_df = calculate_squeeze_momentum(df.copy(), use_true_range=True)
    
    # smi_momentum 컬럼명 확인 (기존 코드와의 호환성)
    if 'squeeze_momentum' in result_df.columns:
        result_df['smi_momentum'] = result_df['squeeze_momentum']
    elif 'smi_momentum' not in result_df.columns:
        result_df['smi_momentum'] = np.nan
    
    # squeeze_on 컬럼은 이미 calculate_squeeze_momentum에서 계산됨
    
    return result_df
