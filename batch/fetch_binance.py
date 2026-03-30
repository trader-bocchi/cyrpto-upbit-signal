"""배치용 바이낸스 캔들 수집 (Public API, API Key 불필요)"""
import requests
import pandas as pd
from datetime import timezone, timedelta
from typing import Dict, List, Optional
from rich.console import Console

console = Console()

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# 알림 대상 심볼
TARGET_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# 업비트 마켓코드 → 바이낸스 심볼 변환 맵
UPBIT_TO_BINANCE = {
    "KRW-BTC": "BTCUSDT",
    "KRW-ETH": "ETHUSDT",
    "KRW-SOL": "SOLUSDT",
    "KRW-XRP": "XRPUSDT",
}

_KST = timezone(timedelta(hours=9))


def fetch_binance_candles(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """
    바이낸스 캔들 데이터 수집

    Args:
        symbol: 심볼 (예: BTCUSDT)
        interval: 시간프레임 (예: 4h, 1d)
        limit: 수집할 캔들 수 (최대 1000)

    Returns:
        candle_time_kst, open, high, low, close, volume 컬럼을 가진 DataFrame
    """
    try:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        # 바이낸스 klines 응답: [open_time, open, high, low, close, volume, ...]
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])

        # open_time: UTC ms → KST datetime
        df["candle_time_kst"] = pd.to_datetime(
            df["open_time"].astype("int64"), unit="ms", utc=True
        ).dt.tz_convert(_KST)

        # 마지막 미완성 캔들 제거
        df = df.iloc[:-1].reset_index(drop=True)

        return df[["candle_time_kst", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        console.print(f"[red]바이낸스 {symbol} {interval} 수집 실패: {e}[/red]")
        return pd.DataFrame()


def fetch_binance_data(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    바이낸스 캔들 데이터 수집

    Args:
        symbols: 심볼 리스트 (None이면 TARGET_SYMBOLS)
        timeframes: 시간프레임 리스트 (None이면 ["4h", "1d"])

    Returns:
        {symbol: {timeframe: DataFrame}} 형태의 딕셔너리
    """
    if symbols is None:
        symbols = TARGET_SYMBOLS
    if timeframes is None:
        timeframes = ["4h", "1d"]

    # 120일치 데이터 확보 (SMI 계산에 충분한 양)
    # 4h: 120일 * 6캔들/일 = 720 캔들, 1d: 120 캔들
    limit_map = {"4h": 800, "1d": 150}

    result: Dict[str, Dict[str, pd.DataFrame]] = {}

    for symbol in symbols:
        console.print(f"\n[cyan]{symbol} (Binance)[/cyan]")
        result[symbol] = {}

        for timeframe in timeframes:
            limit = min(limit_map.get(timeframe, 500), 1000)
            df = fetch_binance_candles(symbol, timeframe, limit=limit)

            if df.empty:
                console.print(f"  [{timeframe}] 데이터 없음")
                result[symbol][timeframe] = pd.DataFrame()
            else:
                result[symbol][timeframe] = df
                console.print(f"  [{timeframe}] {len(df)}개 수집 완료")

    total = sum(1 for m in result.values() for tf, df in m.items() if not df.empty)
    console.print(f"\n[green]바이낸스 데이터 수집 완료 (총 {total}개 마켓×타임프레임)[/green]")

    return result
