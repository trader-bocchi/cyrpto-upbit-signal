"""배치 작업 메인 실행 스크립트

알림 발송 조건 (KST 기준):
  - 매 4시간: 업비트+바이낸스 4h BUY+SELL 통합 메시지 (1회)
  - KST 20시 추가: 업비트+바이낸스 1d BUY+SELL 통합 메시지 (1회)

대상 종목: BTC, ETH, SOL, XRP, USDT, USDC
  - 업비트: KRW-BTC, KRW-ETH, KRW-SOL, KRW-XRP, KRW-USDT, KRW-USDC
  - 바이낸스: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from rich.console import Console

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from batch.fetch_data import fetch_recent_90days
from batch.fetch_binance import fetch_binance_data, TARGET_SYMBOLS as BINANCE_TARGETS
from batch.calculate_smi_batch import calculate_smi_for_batch_data
from batch.signal_detector import (
    detect_signals,
    mark_signals_sent,
    detect_sell_signals,
    mark_sell_signals_sent,
)
from src.telegram.notifier import TelegramNotifier
from src.telegram.message_format import format_combined_signals_message

console = Console()

UPBIT_TARGETS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-USDT", "KRW-USDC"]
KST = timezone(timedelta(hours=9))


def send_signals_for_timeframe(
    notifier: TelegramNotifier,
    upbit_smi,
    binance_smi,
    timeframe: str,
    current_time: str,
) -> int:
    """
    특정 시간프레임의 매수+매도 시그널을 하나의 메시지로 감지·전송

    Returns:
        전송 성공 시 1, 실패 시 0
    """
    tf_label = "4h" if timeframe == "4h" else "1d"

    console.print(f"\n[cyan]▶ {tf_label} 매수 시그널 감지...[/cyan]")
    upbit_buy = detect_signals(upbit_smi, timeframe, source_prefix="UPBIT-")
    binance_buy = detect_signals(binance_smi, timeframe, source_prefix="BINANCE-")
    console.print(f"  Upbit {tf_label} 매수: {len(upbit_buy)}개 / Binance {tf_label} 매수: {len(binance_buy)}개")

    console.print(f"[cyan]▶ {tf_label} 매도 시그널 감지...[/cyan]")
    upbit_sell = detect_sell_signals(upbit_smi, timeframe, source_prefix="UPBIT-")
    binance_sell = detect_sell_signals(binance_smi, timeframe, source_prefix="BINANCE-")
    console.print(f"  Upbit {tf_label} 매도: {len(upbit_sell)}개 / Binance {tf_label} 매도: {len(binance_sell)}개")

    msg = format_combined_signals_message(
        upbit_buy=upbit_buy,
        binance_buy=binance_buy,
        upbit_sell=upbit_sell,
        binance_sell=binance_sell,
        timeframe=timeframe,
        current_time=current_time,
    )

    if notifier.send_message(msg):
        mark_signals_sent(upbit_buy, source_prefix="UPBIT-")
        mark_signals_sent(binance_buy, source_prefix="BINANCE-")
        mark_sell_signals_sent(upbit_sell, source_prefix="UPBIT-")
        mark_sell_signals_sent(binance_sell, source_prefix="BINANCE-")
        console.print(f"[green]OK {tf_label} 통합 메시지 전송 완료[/green]")
        return 1
    else:
        console.print(f"[red]FAIL {tf_label} 메시지 전송 실패[/red]")
        return 0


def main():
    """배치 작업 메인 실행"""
    now_kst = datetime.now(KST)
    is_kst_20h = now_kst.hour == 20
    timeframes = ["4h", "1d"] if is_kst_20h else ["4h"]
    current_time = now_kst.strftime("%Y년 %m월 %d일 %H시 %M분")

    console.print("[bold cyan]배치 작업 시작[/bold cyan]\n")
    console.print(f"현재 KST: {now_kst.strftime('%Y-%m-%d %H:%M')}")
    console.print(
        f"KST 20시 실행: {'예 → 4h + 1d 메시지' if is_kst_20h else '아니오 → 4h 메시지만'}\n"
    )

    # 1. 데이터 수집
    console.print("[bold]1단계: 데이터 수집[/bold]")

    console.print("[cyan]▶ Upbit 데이터 수집...[/cyan]")
    upbit_data = fetch_recent_90days(markets=UPBIT_TARGETS, timeframes=timeframes)

    console.print("[cyan]▶ Binance 데이터 수집...[/cyan]")
    binance_data = fetch_binance_data(symbols=BINANCE_TARGETS, timeframes=timeframes)

    if not upbit_data and not binance_data:
        console.print("[red]데이터 수집 실패. 배치 작업을 종료합니다.[/red]")
        return

    # 2. SMI 계산
    console.print("\n[bold]2단계: SMI 계산[/bold]")
    console.print("[cyan]▶ Upbit SMI 계산...[/cyan]")
    upbit_smi = calculate_smi_for_batch_data(upbit_data, timeframes=timeframes)

    console.print("[cyan]▶ Binance SMI 계산...[/cyan]")
    binance_smi = calculate_smi_for_batch_data(binance_data, timeframes=timeframes)

    # 3. 시그널 감지 및 전송
    console.print("\n[bold]3단계: 시그널 감지 및 전송[/bold]")
    notifier = TelegramNotifier()
    total_sent = 0

    # 4h: 항상 전송
    total_sent += send_signals_for_timeframe(notifier, upbit_smi, binance_smi, "4h", current_time)

    # 1d: KST 20시에만 전송
    if is_kst_20h:
        total_sent += send_signals_for_timeframe(notifier, upbit_smi, binance_smi, "1d", current_time)

    console.print(f"\n[bold green]배치 작업 완료 - 총 {total_sent}개 메시지 전송[/bold green]")


if __name__ == "__main__":
    main()
