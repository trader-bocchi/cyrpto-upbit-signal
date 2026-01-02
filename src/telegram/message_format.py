"""텔레그램 메시지 포맷팅"""
from typing import Dict, Optional


def escape_html(text: str) -> str:
    """HTML 이스케이프"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_buy_message(signal: Dict, ticker_info: Optional[Dict] = None) -> str:
    """
    매수 시그널 메시지 포맷팅
    
    필드:
    - 제목: ✅ BUY SIGNAL [4H] KRW-XXX
    - 시간(KST): YYYY-MM-DD HH:MM
    - 가격: close / (참고로 entry는 다음 캔들 시가라고 명시)
    - SMI: pivot_min, m[i], m[i+1], m[i+2]
    - Trend: close vs SMA50(통과), SMA200 above/below
    - 24h: trade_price_24h, trade_volume_24h, rank, top20/top50
    - 링크: Upbit 거래 페이지
    """
    market = escape_html(signal["market"])
    timeframe = escape_html(signal["timeframe"].upper())
    time_kst = escape_html(signal["signal_time_kst"])
    close = signal["close"]
    
    # 제목
    msg = f"✅ <b>BUY SIGNAL [{timeframe}] {market}</b>\n\n"
    
    # 시간
    msg += f"⏰ <b>시간 (KST):</b> {time_kst}\n"
    
    # 가격
    msg += f"💰 <b>가격:</b> {close:,.0f} KRW\n"
    msg += f"   (진입가는 다음 캔들 시가 기준)\n\n"
    
    # SMI
    msg += f"📊 <b>SMI 지표:</b>\n"
    msg += f"   Pivot Min: {signal.get('smi_pivot_min', 0):.4f}\n"
    msg += f"   m[i]: {signal.get('smi_m_i', 0):.4f}\n"
    msg += f"   m[i+1]: {signal.get('smi_m_i1', 0):.4f}\n"
    msg += f"   m[i+2]: {signal.get('smi_m_i2', 0):.4f}\n\n"
    
    # Trend
    msg += f"📈 <b>추세:</b>\n"
    sma50 = signal.get("sma50")
    if sma50:
        msg += f"   Close vs SMA50: {close:,.0f} {'>' if close > sma50 else '<='} {sma50:,.0f} ✅\n"
    
    sma200_above = signal.get("sma200_above")
    sma200 = signal.get("sma200")
    if sma200 is not None:
        status = "Above" if sma200_above else "Below"
        msg += f"   SMA200: {status} ({sma200:,.0f})\n"
    msg += "\n"
    
    # 24h 거래 정보
    if ticker_info:
        trade_price_24h = ticker_info.get("acc_trade_price_24h", 0)
        trade_volume_24h = ticker_info.get("acc_trade_volume_24h", 0)
        rank = ticker_info.get("rank", 0)
        total = ticker_info.get("total_markets", 0)
        top20 = ticker_info.get("top_20", False)
        top50 = ticker_info.get("top_50", False)
        
        msg += f"💹 <b>24시간 거래 정보:</b>\n"
        msg += f"   거래대금: {trade_price_24h:,.0f} KRW\n"
        msg += f"   거래량: {trade_volume_24h:,.4f}\n"
        if rank > 0 and total > 0:
            msg += f"   순위: {rank}/{total}\n"
            if top20:
                msg += f"   🏆 Top 20\n"
            elif top50:
                msg += f"   🥈 Top 50\n"
        msg += "\n"
    
    # 링크
    market_code = market.replace("KRW-", "").lower()
    msg += f"🔗 <a href='https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{market_code}'>Upbit 거래 페이지</a>"
    
    return msg


def format_sell_message(signal: Dict, ticker_info: Optional[Dict] = None) -> str:
    """
    매도 시그널 메시지 포맷팅
    
    필드:
    - 제목: 🟥 SELL SIGNAL (STOP or TAKE) [4H] KRW-XXX
    - 시간(KST): YYYY-MM-DD HH:MM
    - entry_price / exit_close / PnL%
    - 사유: STOP(-2%) or TAKE(+5%)
    - 보조지표(동일): rank/top20/top50, SMA200 above/below
    """
    market = escape_html(signal["market"])
    timeframe = escape_html(signal["timeframe"].upper())
    time_kst = escape_html(signal["signal_time_kst"])
    reason = signal.get("reason", "UNKNOWN")
    entry_price = signal.get("entry_price", 0)
    exit_price = signal.get("exit_price", 0)
    pnl_pct = signal.get("pnl_pct", 0)
    
    # 제목
    emoji = "🟥" if reason == "STOP" else "🟩"
    reason_text = "손절 (-2%)" if reason == "STOP" else "익절 (+5%)"
    msg = f"{emoji} <b>SELL SIGNAL ({reason_text}) [{timeframe}] {market}</b>\n\n"
    
    # 시간
    msg += f"⏰ <b>시간 (KST):</b> {time_kst}\n\n"
    
    # 가격 정보
    msg += f"💰 <b>가격 정보:</b>\n"
    msg += f"   진입가: {entry_price:,.0f} KRW\n"
    msg += f"   청산가: {exit_price:,.0f} KRW\n"
    msg += f"   수익률: {pnl_pct:+.2f}%\n\n"
    
    # 사유
    msg += f"📋 <b>사유:</b> {reason_text}\n\n"
    
    # 보조지표
    sma200_above = signal.get("sma200_above")
    sma200 = signal.get("sma200")
    if sma200 is not None:
        status = "Above" if sma200_above else "Below"
        msg += f"📈 SMA200: {status} ({sma200:,.0f})\n"
    
    if ticker_info:
        rank = ticker_info.get("rank", 0)
        total = ticker_info.get("total_markets", 0)
        top20 = ticker_info.get("top_20", False)
        top50 = ticker_info.get("top_50", False)
        
        if rank > 0 and total > 0:
            msg += f"💹 순위: {rank}/{total}\n"
            if top20:
                msg += f"   🏆 Top 20\n"
            elif top50:
                msg += f"   🥈 Top 50\n"
    
    return msg


def format_no_signal_message(
    date_str: str,
    reason: str,
    smi_signal_count: int = 0,
    top_signal: Optional[Dict] = None,
    filter_stats: Optional[Dict] = None,
) -> str:
    """
    시그널 없음 메시지 포맷팅
    
    Args:
        date_str: 날짜 문자열 (YYYY-MM-DD)
        reason: 시그널이 없는 사유
        smi_signal_count: SMI로 잡힌 시그널 개수
        top_signal: 거래대금 기준 TOP 1 시그널 정보
        filter_stats: 필터별 통계
    """
    date = escape_html(date_str)
    reason_text = escape_html(reason)
    
    msg = f"📭 <b>시그널 없음</b>\n\n"
    msg += f"📅 <b>날짜:</b> {date}\n"
    msg += f"📋 <b>사유:</b> {reason_text}\n\n"
    
    # 1. SMI 시그널로 잡힌 시그널 개수
    msg += f"<b>1. SMI 시그널로 잡힌 시그널:</b> {smi_signal_count}건\n"
    
    if top_signal and smi_signal_count > 0:
        market = escape_html(top_signal["market"])
        timeframe = escape_html(top_signal["timeframe"].upper())
        ticker_info = top_signal.get("ticker_info", {})
        trade_price = ticker_info.get("acc_trade_price_24h", 0)
        msg += f"   ㄴ TOP 1 종목: <b>{market} [{timeframe}]</b> (거래대금: {trade_price:,.0f} KRW)\n"
    
    msg += "\n"
    
    # 2. 필터링 상세 조건
    if filter_stats:
        msg += f"<b>2. 필터링 상세 조건:</b>\n"
        for filter_name, count in filter_stats.items():
            if count > 0:
                filter_name_escaped = escape_html(filter_name)
                msg += f"   - {filter_name_escaped}에 걸린 시그널: {count}건\n"
    
    return msg