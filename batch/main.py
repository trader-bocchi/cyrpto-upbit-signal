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
KST = timezone(timedelta(hours=9))


def send_unified_signals(
    notifier: TelegramNotifier,
    upbit_smi,
    current_time: str,
) -> int:
    """
    4H(주 시그널) + 1D(참고지표)를 하나의 메시지로 감지·전송

    Returns:
        전송 성공 시 1, 실패 시 0
    """
    # 4H: 주 시그널
    console.print("\n[cyan]>> 4H 매수 시그널 감지...[/cyan]")
    buy_4h = detect_signals(upbit_smi, "4h", source_prefix="UPBIT-")
    console.print(f"  Upbit 4H 매수: {len(buy_4h)}개")

    console.print("[cyan]>> 4H 매도 시그널 감지...[/cyan]")
    sell_4h = detect_sell_signals(upbit_smi, "4h", source_prefix="UPBIT-")
    console.print(f"  Upbit 4H 매도: {len(sell_4h)}개")

    # 1D: 참고지표
    console.print("[cyan]>> 1D 참고 시그널 감지...[/cyan]")
    buy_1d = detect_signals(upbit_smi, "1d", source_prefix="UPBIT-")
    console.print(f"  Upbit 1D 매수: {len(buy_1d)}개")

    sell_1d = detect_sell_signals(upbit_smi, "1d", source_prefix="UPBIT-")
    console.print(f"  Upbit 1D 매도: {len(sell_1d)}개")

    msg = format_unified_message(
        buy_signals_4h=buy_4h,
        sell_signals_4h=sell_4h,
        buy_signals_1d=buy_1d,
        sell_signals_1d=sell_1d,
        current_time=current_time,
    )

    sent_ok = notifier.send_message(msg)

    # 발송 감사 로그: 전송 메시지 + 근거(시그널) 데이터를 기록
    from src.storage.dispatch_log import log_dispatch
    log_dispatch(
        msg,
        {"buy_4h": buy_4h, "sell_4h": sell_4h, "buy_1d": buy_1d, "sell_1d": sell_1d},
        sent_ok,
    )

    if sent_ok:
        mark_signals_sent(buy_4h, source_prefix="UPBIT-")
        mark_sell_signals_sent(sell_4h, source_prefix="UPBIT-")
        mark_signals_sent(buy_1d, source_prefix="UPBIT-")
        mark_sell_signals_sent(sell_1d, source_prefix="UPBIT-")
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

    # 1. 데이터 수집
    console.print("[bold]1단계: 데이터 수집[/bold]")
    console.print("[cyan]>> Upbit 데이터 수집...[/cyan]")
    upbit_data = fetch_recent_90days(markets=UPBIT_TARGETS, timeframes=timeframes)

    if not upbit_data:
        console.print("[red]데이터 수집 실패. 배치 작업을 종료합니다.[/red]")
        return

    # 2. SMI 계산
    console.print("\n[bold]2단계: SMI 계산[/bold]")
    console.print("[cyan]>> Upbit SMI 계산...[/cyan]")
    upbit_smi = calculate_smi_for_batch_data(upbit_data, timeframes=timeframes)

    # 3. 시그널 감지 및 전송 (4H + 1D 통합)
    console.print("\n[bold]3단계: 시그널 감지 및 전송[/bold]")
    notifier = TelegramNotifier()

    # 데이터 건강성 체크: 사용 가능한 4h 데이터가 하나도 없으면
    # 잘못된 '없음' 발송 대신 오류 알림 (조용한 실패 방지)
    usable_4h = sum(1 for m in upbit_smi.values() if not m.get("4h", pd.DataFrame()).empty)
    if usable_4h == 0:
        console.print("[red]사용 가능한 4h 데이터 없음 — 데이터 수집 실패로 판단[/red]")
        notifier.send_message("⚠️ <b>데이터 수집 실패</b> — 이번 회차 시그널 판정 불가 (다음 회차 재시도)")
        console.print("\n[bold yellow]배치 종료 - 데이터 수집 실패[/bold yellow]")
        return

    total_sent = send_unified_signals(notifier, upbit_smi, current_time)

    console.print(f"\n[bold green]배치 작업 완료 - {total_sent}개 메시지 전송[/bold green]")


if __name__ == "__main__":
    main()
