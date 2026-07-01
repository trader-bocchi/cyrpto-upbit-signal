"""텔레그램 메시지 포맷팅"""
from typing import Dict, List, Optional, Tuple

from src.config import BACKTEST_STOP_LOSS_PCT


def escape_html(text: str) -> str:
    """HTML 이스케이프"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_buy_signals_batch(signals: List[tuple], current_time: Optional[str] = None, total_market_volume: Optional[float] = None) -> str:
    """
    여러 매수 시그널을 하나의 메시지로 포맷팅
    
    Args:
        signals: (signal, market, timeframe, ticker_info, regime_blocked, has_1d_signal) 튜플 리스트
        current_time: 현재 시점 (예: "2025-01-15 14:30")
        total_market_volume: 업비트 전체 거래대금 (원 단위)
    
    Returns:
        포맷팅된 메시지
    """
    if not signals:
        return ""
    
    msg = ""
    
    # 제일 상단: 현재 시점 및 전체 거래대금 요약
    if current_time:
        msg += f"⏰ <b>시그널 시점:</b> {escape_html(current_time)}\n"
    
    if total_market_volume is not None and total_market_volume > 0:
        # 거래대금을 조 단위로 변환
        total_trillion = total_market_volume / 1000000000000  # 조 단위
        if total_trillion >= 1:
            msg += f"💰 <b>업비트 전체 거래대금:</b> {total_trillion:.1f}조원\n"
        else:
            # 1조 미만이면 억 단위로 표시
            total_billion = total_market_volume / 100000000
            msg += f"💰 <b>업비트 전체 거래대금:</b> {total_billion:.1f}억원\n"
    
    # 시간프레임 정보 추출 (시그널에서)
    timeframes_in_signals = set()
    for signal_tuple in signals:
        if len(signal_tuple) >= 3:
            _, _, timeframe, _, _, _, _ = signal_tuple[:7] if len(signal_tuple) >= 7 else (*signal_tuple[:3], None, None, False, False)
            if timeframe:
                timeframes_in_signals.add(timeframe.lower())
    
    # 시간프레임 표시 (4h -> 4시간, 1d -> 1일)
    timeframe_display = []
    if "4h" in timeframes_in_signals:
        timeframe_display.append("4시간")
    if "1d" in timeframes_in_signals:
        timeframe_display.append("1일")
    
    if timeframe_display:
        msg += f"📊 <b>캔들 기준:</b> {' / '.join(timeframe_display)} 캔들\n"
    
    if current_time or (total_market_volume is not None and total_market_volume > 0) or timeframe_display:
        msg += "\n"
    
    msg += f"✅ <b>BUY SIGNAL</b>\n\n"
    
    for signal_tuple in signals:
        # 튜플 길이에 따라 호환성 처리 (기존 코드와의 호환)
        if len(signal_tuple) == 7:
            signal, market, timeframe, ticker_info, regime_blocked, has_1d_signal, has_4h_signal = signal_tuple
        elif len(signal_tuple) == 6:
            signal, market, timeframe, ticker_info, regime_blocked, has_1d_signal = signal_tuple
            has_4h_signal = False
        else:
            # 기존 형식 (5개 요소) 지원
            signal, market, timeframe, ticker_info, regime_blocked = signal_tuple[:5]
            has_1d_signal = False
            has_4h_signal = False
        
        market_escaped = escape_html(market)
        timeframe_escaped = escape_html(timeframe.upper())
        
        # 거래대금 (억 단위로 변환)
        trade_price_24h = ticker_info.get("acc_trade_price_24h", 0) if ticker_info else 0
        trade_price_billion = trade_price_24h / 100000000  # 억 단위
        
        # 순위 (전체 종목 중 실제 순위 - ticker_info에서 가져옴)
        # ticker_info는 이미 전체 종목 중의 순위가 설정되어 있음
        rank = ticker_info.get("rank", 0) if ticker_info else 0
        total = ticker_info.get("total_markets", 0) if ticker_info else 0
        
        # 거래대금 포맷팅 (조 단위로 표시)
        if trade_price_billion >= 10000:  # 1조 이상
            trade_price_str = f"{trade_price_billion / 10000:.1f}조"
        else:
            trade_price_str = f"{trade_price_billion:.1f}억"
        
        # 시그널 정보 ([4H/1D] 제거)
        signal_line = f"• <b>{market_escaped}</b>"
        if trade_price_24h > 0:
            signal_line += f" (거래대금: {trade_price_str}"
            if rank > 0 and total > 0:
                # 시그널이 잡힌 종목의 실제 거래순위 표시 (예: 15/232)
                signal_line += f", 거래순위: {rank}/{total}위"
            
            # 4h 시그널인 경우 1d 시그널 여부 표시
            if timeframe == "4h":
                if has_1d_signal:
                    signal_line += ", 1일캔들: 시그널 있음"
                else:
                    signal_line += ", 1일캔들: 시그널 없음"
            
            # 1d 시그널인 경우 4h 시그널 여부 표시
            if timeframe == "1d":
                if has_4h_signal:
                    signal_line += ", 4시간캔들: 시그널 있음"
                else:
                    signal_line += ", 4시간캔들: 시그널 없음"
            
            signal_line += ")"
        
        msg += signal_line + "\n"
    
    return msg


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


def _strength_tag(strength: str, score: float) -> str:
    """시그널 강도 태그 생성 (STRONG/MODERATE/NORMAL)"""
    if strength == "STRONG":
        return f"  🔥<b>강한확신</b>({score:.1f}x)"
    elif strength == "MODERATE":
        return f"  ⚡확신({score:.1f}x)"
    return ""


def format_combined_signals_message(
    buy_signals: List[Tuple[str, Dict]],
    sell_signals: List[Tuple[str, Dict]],
    timeframe: str,
    current_time: Optional[str] = None,
) -> str:
    """
    매수/매도 시그널을 하나의 메시지로 포맷팅 (단일 타임프레임)

    Args:
        buy_signals: [(market, signal_dict), ...] 매수 시그널
        sell_signals: [(market, signal_dict), ...] 매도 시그널
        timeframe: 시간프레임 ("4h" 또는 "1d")
        current_time: 현재 시점 문자열

    Returns:
        포맷팅된 HTML 메시지
    """
    tf_upper = timeframe.upper()
    tf_display = "4시간" if timeframe == "4h" else "1일"

    msg = ""
    if current_time:
        msg += f"⏰ <b>시그널 시점:</b> {escape_html(current_time)}\n"
    msg += f"📊 <b>캔들 기준:</b> {tf_display} 캔들\n"

    # ── BUY SIGNAL 섹션 ──────────────────────────────────────────
    msg += f"\n✅ <b>BUY SIGNAL [{tf_upper}]</b>\n\n"

    if buy_signals:
        for market, signal in buy_signals:
            close = signal.get("close", 0)
            strength = signal.get("strength", "NORMAL")
            score = signal.get("strength_score", 0.0)
            strength_tag = _strength_tag(strength, score)
            msg += f"• <b>{escape_html(market)}</b>  {close:,.0f} KRW{strength_tag}\n"
    else:
        msg += "• 시그널 없음\n"

    # ── SELL SIGNAL 섹션 ─────────────────────────────────────────
    msg += f"\n🔴 <b>SELL SIGNAL [{tf_upper}]</b>\n\n"

    if sell_signals:
        for market, signal in sell_signals:
            close = signal.get("close", 0)
            msg += f"• <b>{escape_html(market)}</b>  {close:,.0f} KRW\n"
    else:
        msg += "• 시그널 없음\n"

    return msg


def _format_signal_list(signals: List[Tuple[str, Dict]], is_buy: bool = True) -> str:
    """시그널 리스트를 상세와 함께 포맷팅.

    각 시그널에 SMI 모멘텀 흐름(시그널이 잡힌 근거), 매수는 손절 참고가,
    업비트 링크를 함께 표시한다.
    """
    if not signals:
        return "  없음\n"
    lines = ""
    for market, signal in signals:
        close = signal.get("close", 0)
        head = f"  • <b>{escape_html(market)}</b>  {close:,.0f} KRW"
        if is_buy:
            head += _strength_tag(signal.get("strength", "NORMAL"), signal.get("strength_score", 0.0))
        lines += head + "\n"

        # SMI 모멘텀 상태 (2봉 회복/하락 근거) — 원값은 코인마다 스케일이 달라
        # 부호(0선 돌파 여부)로 표시한다.
        m2 = signal.get("smi_m_i2")
        detail = []
        if is_buy:
            state = "0선 상향돌파" if (m2 is not None and m2 > 0) else "아직 음수권"
            detail.append(f"저점회복 2봉 · {state}")
            if close:
                stop_price = close * (1 - BACKTEST_STOP_LOSS_PCT)
                detail.append(f"손절참고 {stop_price:,.0f}(-{BACKTEST_STOP_LOSS_PCT * 100:.0f}%)")
        else:
            state = "0선 하향돌파" if (m2 is not None and m2 < 0) else "아직 양수권"
            detail.append(f"고점반전 2봉 · {state}")
        if detail:
            lines += "      " + " · ".join(detail) + "\n"
    return lines


def format_unified_message(
    buy_signals_4h: List[Tuple[str, Dict]],
    sell_signals_4h: List[Tuple[str, Dict]],
    buy_signals_1d: List[Tuple[str, Dict]],
    sell_signals_1d: List[Tuple[str, Dict]],
    current_time: Optional[str] = None,
) -> str:
    """
    4H(주 시그널) + 1D(참고지표)를 하나의 메시지로 포맷팅

    Args:
        buy_signals_4h: 4H 매수 시그널
        sell_signals_4h: 4H 매도 시그널
        buy_signals_1d: 1D 매수 시그널 (참고)
        sell_signals_1d: 1D 매도 시그널 (참고)
        current_time: 현재 시점 문자열

    Returns:
        포맷팅된 HTML 메시지
    """
    msg = ""
    if current_time:
        msg += f"⏰ <b>시그널 시점:</b> {escape_html(current_time)}\n"
    msg += "\n"

    # ── 4H 주 시그널 ─────────────────────────────────────────────
    msg += "━━━ <b>4H 시그널</b> ━━━\n\n"

    msg += "✅ <b>BUY</b>\n"
    msg += _format_signal_list(buy_signals_4h, is_buy=True)

    msg += "\n🔴 <b>SELL</b>\n"
    msg += _format_signal_list(sell_signals_4h, is_buy=False)

    # ── 1D 참고지표 ──────────────────────────────────────────────
    msg += "\n━━━ <b>1D 참고지표</b> ━━━\n\n"

    msg += "✅ BUY\n"
    msg += _format_signal_list(buy_signals_1d, is_buy=True)

    msg += "\n🔴 SELL\n"
    msg += _format_signal_list(sell_signals_1d, is_buy=False)

    # ── 안내 ─────────────────────────────────────────────────────
    has_any = any([buy_signals_4h, sell_signals_4h, buy_signals_1d, sell_signals_1d])
    if has_any:
        msg += "\n──────────\n"
        msg += "※ 매수 진입가는 <b>다음 봉 시가</b> 기준. 매도 신호는 SMI 반전 시 발송.\n"

    return msg


def format_no_signal_message(
    date_str: str,
    timeframes: List[str],
) -> str:
    """
    시그널 없음 메시지 포맷팅 (간단 버전)
    
    Args:
        date_str: 날짜 및 시간 문자열 (YYYY-MM-DD HH:MM:SS)
        timeframes: 시간프레임 리스트 (예: ["4h", "1d"])
    """
    date_time = escape_html(date_str)
    
    # 시간프레임 표시 (4h -> 4시간, 1d -> 1일)
    timeframe_display = []
    for tf in timeframes:
        if tf.lower() == "4h":
            timeframe_display.append("4시간")
        elif tf.lower() in ["1d", "24h", "d"]:
            timeframe_display.append("1일")
    
    msg = f"📭 <b>시그널 없음</b>\n\n"
    if timeframe_display:
        msg += f"📊 <b>캔들 기준:</b> {' / '.join(timeframe_display)} 캔들\n"
    msg += f"📅 <b>시그널 시점:</b> {date_time}\n"
    
    return msg