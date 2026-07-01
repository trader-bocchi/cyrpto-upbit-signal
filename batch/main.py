"""배치 작업 메인 실행 스크립트

알림 발송 조건:
  - 매 4시간: 업비트 4h + 1d 통합 메시지 (1d는 참고지표)

대상 종목: BTC, ETH
  - 업비트: KRW-BTC, KRW-ETH
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
from rich.console import Console

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from batch.fetch_data import fetch_recent_90days
from batch.fetch_binance import fetch_binance_data
from batch.calculate_smi_batch import calculate_smi_for_batch_data
from batch.signal_detector import (
    detect_signals,
    mark_signals_sent,
    detect_sell_signals,
    mark_sell_signals_sent,
)
from src.telegram.notifier import TelegramNotifier
from src.telegram.message_format import format_unified_message

console = Console()

UPBIT_TARGETS = ["KRW-BTC", "KRW-ETH"]
BINANCE_TARGETS = ["BTCUSDT", "ETHUSDT"]
KST = timezone(timedelta(hours=9))


def _detect_all(smi, source_prefix: str):
    """한 거래소 데이터에서 4H/1D 매수·매도 4개 그룹 감지 → 튜플."""
    return (
        detect_signals(smi, "4h", source_prefix=source_prefix),
        detect_sell_signals(smi, "4h", source_prefix=source_prefix),
        detect_signals(smi, "1d", source_prefix=source_prefix),
        detect_sell_signals(smi, "1d", source_prefix=source_prefix),
    )


def _mark_all(groups, source_prefix: str):
    """감지된 4개 그룹을 전송완료 마킹."""
    buy_4h, sell_4h, buy_1d, sell_1d = groups
    mark_signals_sent(buy_4h, source_prefix=source_prefix)
    mark_sell_signals_sent(sell_4h, source_prefix=source_prefix)
    mark_signals_sent(buy_1d, source_prefix=source_prefix)
    mark_sell_signals_sent(sell_1d, source_prefix=source_prefix)


def send_unified_signals(
    notifier: TelegramNotifier,
    upbit_smi,
    binance_smi,
    current_time: str,
) -> int:
    """
    업비트(KRW) + 바이낸스(USDT) 4H/1D 시그널을 하나의 메시지로 감지·전송

    Returns:
        전송 성공 시 1, 실패 시 0
    """
    console.print("\n[cyan]>> 업비트 시그널 감지...[/cyan]")
    upbit = _detect_all(upbit_smi, "UPBIT-")
    console.print(f"  Upbit 4H 매수 {len(upbit[0])} / 매도 {len(upbit[1])} · 1D 매수 {len(upbit[2])} / 매도 {len(upbit[3])}")

    console.print("[cyan]>> 바이낸스 시그널 감지...[/cyan]")
    binance = _detect_all(binance_smi, "BINANCE-")
    console.print(f"  Binance 4H 매수 {len(binance[0])} / 매도 {len(binance[1])} · 1D 매수 {len(binance[2])} / 매도 {len(binance[3])}")

    msg = format_unified_message(upbit=upbit, binance=binance, current_time=current_time)

    sent_ok = notifier.send_message(msg)

    # 발송 감사 로그: 전송 메시지 + 근거(시그널) 데이터를 기록
    from src.storage.dispatch_log import log_dispatch
    log_dispatch(
        msg,
        {
            "upbit_buy_4h": upbit[0], "upbit_sell_4h": upbit[1],
            "upbit_buy_1d": upbit[2], "upbit_sell_1d": upbit[3],
            "binance_buy_4h": binance[0], "binance_sell_4h": binance[1],
            "binance_buy_1d": binance[2], "binance_sell_1d": binance[3],
        },
        sent_ok,
    )

    if sent_ok:
        _mark_all(upbit, "UPBIT-")
        _mark_all(binance, "BINANCE-")
        console.print("[green]OK 통합 메시지 전송 완료 (감사로그 기록)[/green]")
        return 1
    else:
        console.print("[red]FAIL 메시지 전송 실패 (감사로그 기록)[/red]")
        return 0


def main():
    """배치 작업 메인 실행"""
    now_kst = datetime.now(KST)
    timeframes = ["4h", "1d"]
    current_time = now_kst.strftime("%Y년 %m월 %d일 %H시 %M분")

    console.print("[bold cyan]배치 작업 시작[/bold cyan]\n")
    console.print(f"현재 KST: {now_kst.strftime('%Y-%m-%d %H:%M')}")
    console.print(f"실행 타임프레임: {', '.join(timeframes)}\n")

    # 1. 데이터 수집 (업비트 + 바이낸스)
    console.print("[bold]1단계: 데이터 수집[/bold]")
    console.print("[cyan]>> Upbit 데이터 수집...[/cyan]")
    upbit_data = fetch_recent_90days(markets=UPBIT_TARGETS, timeframes=timeframes)
    console.print("[cyan]>> Binance 데이터 수집...[/cyan]")
    binance_data = fetch_binance_data(symbols=BINANCE_TARGETS, timeframes=timeframes)

    # 2. SMI 계산
    console.print("\n[bold]2단계: SMI 계산[/bold]")
    console.print("[cyan]>> Upbit SMI 계산...[/cyan]")
    upbit_smi = calculate_smi_for_batch_data(upbit_data or {}, timeframes=timeframes)
    console.print("[cyan]>> Binance SMI 계산...[/cyan]")
    binance_smi = calculate_smi_for_batch_data(binance_data or {}, timeframes=timeframes)

    # 3. 시그널 감지 및 전송 (업비트 + 바이낸스 통합)
    console.print("\n[bold]3단계: 시그널 감지 및 전송[/bold]")
    notifier = TelegramNotifier()

    # 데이터 건강성 체크: 양 거래소 모두 4h 데이터가 하나도 없으면
    # 잘못된 '없음' 발송 대신 오류 알림 (조용한 실패 방지)
    usable_4h = sum(
        1 for smi in (upbit_smi, binance_smi)
        for m in smi.values() if not m.get("4h", pd.DataFrame()).empty
    )
    if usable_4h == 0:
        console.print("[red]사용 가능한 4h 데이터 없음 — 데이터 수집 실패로 판단[/red]")
        notifier.send_message("⚠️ <b>데이터 수집 실패</b> — 이번 회차 시그널 판정 불가 (다음 회차 재시도)")
        console.print("\n[bold yellow]배치 종료 - 데이터 수집 실패[/bold yellow]")
        return

    total_sent = send_unified_signals(notifier, upbit_smi, binance_smi, current_time)

    console.print(f"\n[bold green]배치 작업 완료 - {total_sent}개 메시지 전송[/bold green]")


if __name__ == "__main__":
    main()
