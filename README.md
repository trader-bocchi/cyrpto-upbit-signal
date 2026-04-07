# Upbit Crypto Signal System

SMI(Squeeze Momentum Index) 기반 암호화폐 매수/매도 시그널 봇.  
GitHub Actions로 4시간마다 자동 실행되며, 텔레그램으로 알림을 전송합니다.

---

## 모니터링 대상 종목

| 거래소 | 종목 |
|--------|------|
| 업비트 | KRW-BTC, KRW-ETH, KRW-SOL, KRW-XRP, KRW-USDT, KRW-USDC |
| 바이낸스 | BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT |

---

## 시그널 로직

### 매수 시그널 (BUY)
SMI momentum이 바닥을 찍고 2단계 회복하는 패턴을 감지합니다.

1. 마지막 SMI 값이 음수 (하락 구간)
2. 최근 100개 캔들 내 가장 최근 **로컬 미니멈(pivot)** 이 현재 바 기준 정확히 2칸 전 위치
3. `m[i-2] < m[i-1] < m[i]` — 연속 2단계 상승 (저점에서 회복)
4. pivot 값이 음수여야 함 (`SMI_REQUIRE_NEGATIVE_PIVOT=true`)
5. `close > SMA50` 필터 (`SIGNAL_ENABLE_SMA50_FILTER=true`)

### 매도 시그널 (SELL)
매수 시그널의 정반대 — SMI momentum이 고점을 찍고 2단계 하락하는 패턴을 감지합니다.

1. 마지막 SMI 값이 양수 (상승 구간)
2. 최근 100개 캔들 내 가장 최근 **로컬 맥시멈(pivot)** 이 현재 바 기준 정확히 2칸 전 위치
3. `m[i-2] > m[i-1] > m[i]` — 연속 2단계 하락 (고점에서 반전)
4. pivot 값이 양수여야 함 (`SMI_REQUIRE_POSITIVE_PIVOT=true`)
5. `close < SMA50` 필터 (`SIGNAL_ENABLE_SMA50_FILTER=true`)

> **pivot 탐색 방식:** 단순 N개 캔들 내 최솟값/최댓값이 아니라, 양쪽 이웃보다 엄격히 작은(큰) 진짜 극값 중 가장 최근 것을 선택합니다.

---

## 알림 주기

| 시간 (KST) | 전송 내용 |
|-----------|----------|
| 매 4시간 (0, 4, 8, 12, 16, 20시) | 업비트 + 바이낸스 **4h** 매수/매도 시그널 |
| 20시 추가 | 업비트 + 바이낸스 **1d** 매수/매도 시그널 |

- 매수: 시그널 유무와 관계없이 항상 전송
- 매도: 시그널이 있을 때만 전송

---

## 설치 방법

### 1. 환경 설정

```bash
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Mac/Linux

pip install -r requirements.txt
```

### 2. `.env` 파일 생성

```env
# Telegram Bot 설정
# @BotFather에서 봇 생성 후 토큰 발급
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Telegram Chat ID
# 봇에 /start 메시지 후 https://api.telegram.org/bot<TOKEN>/getUpdates 에서 확인
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 3. GitHub Secrets 설정 (자동화용)

`Settings > Secrets and variables > Actions`에서 아래 두 항목 등록:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

---

## 실행 방법

### 배치 파이프라인 (GitHub Actions / 수동 실행)

```bash
# 데이터 수집 → SMI 계산 → 시그널 감지 → 텔레그램 전송 (한 번에)
python batch/main.py
```

### CLI 개별 실행

```bash
# 4h/1d 캔들 직접 수집
python -m src.cli fetch-4h-1d-direct

# SMI 지표 계산
python -m src.cli calculate-smi --timeframes 4h,1d

# 매수/매도 시그널 실행 및 알림
python -m src.cli run-signals --timeframes 4h,1d
```

### 백테스팅

```bash
# 2025년 전체 백테스팅
python -m src.cli backtest --year 2025 --timeframes 4h,1d

# 최근 30일
python -m src.cli backtest --last-days 30 --timeframes 4h,1d

# 혼합 전략 (4h + 1d 동시)
python -m src.cli backtest --year 2025 --mixed
```

백테스팅 매도 기준:
- 1순위 손절 (STOP): 진입가 대비 -2% 이하
- 2순위 익절 (TAKE): 진입가 대비 +5% 이상
- 3순위 타임스탑: 4h는 최대 12봉(2일), 1d는 최대 7봉 보유

### 시각화

```bash
python candle_smi_chart.py --market KRW-BTC --timeframes 4h,1d --days 30
```

---

## 주요 설정값 (환경변수)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SMI_LOCAL_MIN_WINDOW` | 100 | pivot 탐색 범위 (캔들 수) |
| `SMI_REQUIRE_NEGATIVE_PIVOT` | true | 매수 pivot이 음수여야 함 |
| `SMI_REQUIRE_POSITIVE_PIVOT` | true | 매도 pivot이 양수여야 함 |
| `SIGNAL_ENABLE_SMA50_FILTER` | true | SMA50 방향 필터 사용 여부 |

---

업로드 일자: 2026-04-07
