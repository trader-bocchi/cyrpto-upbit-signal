# Upbit Crypto Signal System

SMI(Squeeze Momentum Index) 기반 암호화폐 매수/매도 시그널 봇.
**이 PC의 로컬 cron**으로 4시간마다(캔들 마감 직후) 실행되며, 텔레그램으로 알림을 전송합니다.

---

## 모니터링 대상 종목

| 거래소 | 종목 | 단위 |
|--------|------|------|
| 업비트 | KRW-BTC, KRW-ETH | KRW |
| 바이낸스 | BTCUSDT, ETHUSDT | USDT |

> 대상 종목은 `batch/main.py`의 `UPBIT_TARGETS`, `BINANCE_TARGETS`에서 관리합니다.
> 두 거래소 모두 동일한 SMI 로직으로 감지하며, 한 메시지에 통합 전송됩니다.
> 캔들 경계가 동일(둘 다 UTC 정렬)해 같은 cron으로 동작합니다.

---

## 시그널 로직

시그널은 **SMI 모멘텀** 하나만 봅니다. 추세(SMA)·거래량·강도 필터는 발신에 관여하지 않습니다.
판정은 **완성된 4시간 봉**에서만 이루어집니다(수집 시 진행 중인 미완성 캔들은 제거).

### 매수 시그널 (BUY)
SMI 모멘텀이 바닥을 찍고 회복하는 패턴:

1. 최근 100개 캔들 내 가장 최근 **로컬 미니멈(pivot)** 이 현재 봉 기준 정확히 2칸 전
2. pivot 값이 **음수** (`SMI_REQUIRE_NEGATIVE_PIVOT=true`)
3. `m[i-2] < m[i-1] < m[i]` — 2봉 연속 상승 (저점 회복 확인)

### 매도 시그널 (SELL)
매수의 정반대 — SMI 모멘텀이 고점을 찍고 반전하는 패턴:

1. 최근 100개 캔들 내 가장 최근 **로컬 맥시멈(pivot)** 이 현재 봉 기준 정확히 2칸 전
2. pivot 값이 **양수** (`SMI_REQUIRE_POSITIVE_PIVOT=true`)
3. `m[i-2] > m[i-1] > m[i]` — 2봉 연속 하락 (고점 반전 확인)

> **pivot 탐색:** 단순 최솟값/최댓값이 아니라, 양쪽 이웃보다 엄격히 작은(큰) 진짜 극값 중 가장 최근 것을 선택합니다.
>
> 실전 발신과 백테스트는 **동일한 진입 규칙**(`src/signals/smi_rule.py`)을 공유합니다.

---

## 알림 주기 (로컬 cron)

업비트 4h 캔들 마감 시각은 **KST 01·05·09·13·17·21시**(UTC 00·04·08·12·16·20)입니다.
캔들 마감 **5분 뒤**에 실행하여, 갓 닫힌 완성 캔들 기준으로 발송합니다.

| 시각 (KST) | 전송 내용 |
|-----------|----------|
| 01:05·05:05·09:05·13:05·17:05·21:05 | 업비트(KRW) + 바이낸스(USDT) **4h**(주 시그널) + **1d**(참고지표) 매수/매도 통합 메시지 |

- 매 실행마다 4H/1D 매수·매도를 하나의 메시지로 통합 전송(시그널 없으면 "없음"으로 표기).

---

## 설치 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. `.env` 파일 생성 (필수)

`.env.example`을 복사해 텔레그램 정보를 채웁니다. 이 파일이 없으면 실행 시 에러가 납니다.

```env
# @BotFather에서 봇 생성 후 토큰 발급
TELEGRAM_BOT_TOKEN=your_bot_token_here
# 봇에 /start 후 https://api.telegram.org/bot<TOKEN>/getUpdates 에서 확인
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 3. 로컬 cron 등록

실행 스크립트(`scripts/run_local.sh`)를 KST 캔들 마감 5분 뒤에 돌립니다.

```bash
crontab -e
```

아래 줄을 추가(TZ는 시스템 로컬 시각 = KST 기준):

```cron
5 1,5,9,13,17,21 * * * /Users/jongwon/proj/application/crypto-upbit-signal/scripts/run_local.sh
```

> **macOS 주의:** cron이 파일/네트워크에 접근하려면 `시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한`에서 `/usr/sbin/cron`을 허용해야 할 수 있습니다.
> 실행 로그는 `logs/cron_YYYYMMDD.log`에 남습니다.

---

## 실행 방법

```bash
# 데이터 수집 → SMI 계산 → 시그널 감지 → 텔레그램 전송 (한 번에)
python batch/main.py

# 로컬 러너(로그 파일로 기록) — cron이 호출하는 것과 동일
scripts/run_local.sh
```

---

## 백테스트

실전과 **동일한 진입 규칙**으로 종목별 수익률을 측정합니다.

```bash
python scripts/backtest_signals.py                 # 기본 BTC/ETH, 2026-01-01~현재
python scripts/backtest_signals.py KRW-BTC          # 종목 지정
START=2025-01-01 python scripts/backtest_signals.py # 집계 시작일 지정
BACKTEST_TIME_STOP_MODE=fixed python scripts/backtest_signals.py  # 고정 타임스탑과 비교
```

**청산 기준:**
- 손절(STOP): 진입가 대비 **-2%** 이하
- 익절(TAKE): 진입가 대비 **+5%** 이상
- 타임스탑(TIME_STOP):
  - `adaptive`(기본, 동적A): SMI 모멘텀이 **2봉 연속 하락**하면 청산. 단 **최소 1일(6봉) 보유, 최대 7일(42봉) 캡**.
  - `fixed`: 고정 봉 수(`BACKTEST_TIME_STOP_BARS_4H`) 경과 시 청산.

> 진입가는 시그널 다음 봉 시가, 수수료 왕복 0.1% 반영. 종목당 1포지션.

---

## 주요 설정값 (환경변수)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SMI_LOCAL_MIN_WINDOW` | 100 | pivot 탐색 범위 (캔들 수) |
| `SMI_REQUIRE_NEGATIVE_PIVOT` | true | 매수 pivot이 음수여야 함 |
| `SMI_REQUIRE_POSITIVE_PIVOT` | true | 매도 pivot이 양수여야 함 |
| `BACKTEST_STOP_LOSS_PCT` | 0.02 | 손절 -2% |
| `BACKTEST_TAKE_PROFIT_PCT` | 0.05 | 익절 +5% |
| `BACKTEST_TIME_STOP_MODE` | adaptive | 타임스탑 모드 (adaptive=동적A / fixed) |
| `BACKTEST_TIME_STOP_MIN_BARS_4H` | 6 | 동적A 최소 보유 (1일) |
| `BACKTEST_TIME_STOP_MAX_BARS_4H` | 42 | 동적A 최대 보유 (7일) |
| `BACKTEST_TIME_STOP_BARS_4H` | 18 | fixed 모드 4h 타임스탑 봉 수 |

---

## 아키텍처 메모

- **실전 발신**: `batch/main.py` → `batch/signal_detector.py`(상태 없는 알림; 포지션 관리 없음).
- **진입 규칙(공용)**: `src/signals/smi_rule.py` — 실전·백테스트가 공유.
- **청산 규칙(백테스트/CLI)**: `src/signals/exit_rules.py`(동적A), `src/signals/sell_engine.py`.
  포지션 상태가 필요한 청산 모델이라 상태 없는 실전 알림에는 적용되지 않습니다.
- **수집**: `batch/fetch_data.py` — 진행 중인 미완성 캔들을 제거해 리페인팅을 방지합니다.

업로드 일자: 2026-04-07 (갱신: 로컬 cron 전환 / 동적A 타임스탑 / BTC·ETH)
