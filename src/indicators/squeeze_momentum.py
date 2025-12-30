"""Squeeze Momentum Indicator (SMI)"""
import pandas as pd
import numpy as np

from src.config import (
    SMI_BB_LENGTH,
    SMI_BB_MULT,
    SMI_KC_LENGTH,
    SMI_KC_MULT,
)


def bollinger_bands(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    """볼린저 밴드"""
    basis = close.rolling(window=length, min_periods=1).mean()
    dev = close.rolling(window=length, min_periods=1).std()
    
    upper = basis + (dev * mult)
    lower = basis - (dev * mult)
    
    return pd.DataFrame({
        "bb_basis": basis,
        "bb_upper": upper,
        "bb_lower": lower,
    })


def keltner_channels(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 20,
    mult: float = 1.5,
) -> pd.DataFrame:
    """켈트너 채널"""
    basis = close.rolling(window=length, min_periods=1).mean()
    tr = pd.DataFrame({
        "hl": high - low,
        "hc": (high - close.shift()).abs(),
        "lc": (low - close.shift()).abs(),
    }).max(axis=1)
    
    atr = tr.rolling(window=length, min_periods=1).mean()
    
    upper = basis + (atr * mult)
    lower = basis - (atr * mult)
    
    return pd.DataFrame({
        "kc_basis": basis,
        "kc_upper": upper,
        "kc_lower": lower,
    })


def squeeze_momentum(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    bb_length: int = SMI_BB_LENGTH,
    bb_mult: float = SMI_BB_MULT,
    kc_length: int = SMI_KC_LENGTH,
    kc_mult: float = SMI_KC_MULT,
) -> pd.Series:
    """
    Squeeze Momentum Histogram 계산
    
    TradingView의 Squeeze Momentum Indicator 컨셉 기반
    """
    # 볼린저 밴드
    bb = bollinger_bands(close, bb_length, bb_mult)
    
    # 켈트너 채널
    kc = keltner_channels(high, low, close, kc_length, kc_mult)
    
    # Squeeze 판단: BB가 KC 안에 있으면 squeeze on
    squeeze_on = (
        (bb["bb_lower"] > kc["kc_lower"]) & (bb["bb_upper"] < kc["kc_upper"])
    )
    
    # Momentum 계산 (회귀 기반 평활)
    # TradingView 구현 컨셉: 회귀 기반 momentum
    length_reg = 12
    hlc3 = (high + low + close) / 3
    
    # 선형 회귀 기울기
    momentum = pd.Series(index=close.index, dtype=float)
    
    for i in range(len(close)):
        if i < length_reg:
            momentum.iloc[i] = 0.0
        else:
            y = hlc3.iloc[i - length_reg + 1 : i + 1].values
            x = np.arange(length_reg)
            
            # 선형 회귀
            coeffs = np.polyfit(x, y, 1)
            momentum.iloc[i] = coeffs[0]  # 기울기
    
    # 평활화
    momentum_smooth = momentum.rolling(window=5, min_periods=1).mean()
    
    # Squeeze off일 때만 momentum 표시
    momentum_smooth = momentum_smooth * (~squeeze_on).astype(int)
    
    return momentum_smooth


def calculate_smi(df: pd.DataFrame) -> pd.DataFrame:
    """
    SMI 계산 및 squeeze 상태 추가
    
    Args:
        df: OHLCV DataFrame
    
    Returns:
        smi_momentum, squeeze_on 컬럼이 추가된 DataFrame
    """
    result_df = df.copy()
    
    smi = squeeze_momentum(
        result_df["high"],
        result_df["low"],
        result_df["close"],
        bb_length=SMI_BB_LENGTH,
        bb_mult=SMI_BB_MULT,
        kc_length=SMI_KC_LENGTH,
        kc_mult=SMI_KC_MULT,
    )
    
    result_df["smi_momentum"] = smi
    
    # Squeeze 상태 계산
    bb = bollinger_bands(result_df["close"], SMI_BB_LENGTH, SMI_BB_MULT)
    kc = keltner_channels(
        result_df["high"],
        result_df["low"],
        result_df["close"],
        SMI_KC_LENGTH,
        SMI_KC_MULT,
    )
    
    result_df["squeeze_on"] = (
        (bb["bb_lower"] > kc["kc_lower"]) & (bb["bb_upper"] < kc["kc_upper"])
    )
    
    return result_df

