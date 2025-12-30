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

