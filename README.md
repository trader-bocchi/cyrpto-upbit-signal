# Upbit Crypto Signal System

Upbit에서 1시간 캔들을 수집하여 4시간/24시간 캔들로 집계하고, Squeeze Momentum Indicator(SMI) 기반 매수/매도 시그널을 텔레그램으로 전송하는 시스템입니다.

## 환경 요구사항

- Windows 11
- Python 3.11 이상
- 인터넷 연결 (Upbit API 접근)

## 설치 방법

1. Python 가상환경 생성 및 활성화:
```bash
python -m venv venv
venv\Scripts\activate
```

2. 패키지 설치:
```bash
pip install -r requirements.txt
```

3. 환경 변수 설정:
```bash
copy .env.example .env
# .env 파일을 열어 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정하세요
```

## 프로젝트 구조

```
.
├── src/
│   ├── config.py              # 설정 관리
│   ├── upbit_client.py        # Upbit API 클라이언트
│   ├── storage/               # CSV 저장소 및 중복 제거
│   ├── pipeline/              # 데이터 수집/집계 파이프라인
│   ├── indicators/            # 기술적 지표 (SMI, MA)
│   ├── signals/               # 시그널 엔진 (매수/매도)
│   ├── telegram/              # 텔레그램 알림
│   ├── backtest/              # 백테스팅 엔진
│   └── cli.py                 # CLI 인터페이스
├── data/                      # 데이터 저장소
│   ├── raw/                   # 원시 1시간 캔들
│   ├── derived/               # 집계된 4시간/24시간 캔들
│   └── meta/                  # 메타데이터 (체크포인트, 로그)
├── results/                   # 백테스팅 결과
├── tests/                     # 단위 테스트
├── requirements.txt
├── .env.example
└── README.md
```

## 사용 방법

### 1. 2025년 전체 1시간 캔들 수집
```bash
python -m src.cli fetch-2025 --markets KRW-BTC,KRW-ETH
```

### 2. 일일 증분 수집
```bash
python -m src.cli fetch-daily
```

### 3. 4시간/24시간 캔들 집계
```bash
python -m src.cli aggregate --timeframes 4h,1d
```

### 4. 매수 시그널 실행 및 알림
```bash
python -m src.cli run-signals --timeframes 4h,1d
```

### 5. 매도 시그널 체크 및 알림
```bash
python -m src.cli run-sell-check --timeframes 4h,1d
```

### 6. 백테스팅 (2025년 전체)
```bash
python -m src.cli backtest --year 2025 --timeframes 4h,1d
```

### 7. 백테스팅 (최근 30일)
```bash
python -m src.cli backtest --last-days 30 --timeframes 4h,1d
```

## 주요 특징

- **KST 시간 기준**: 모든 시간은 한국 표준시(KST)로 처리
- **중복 제거**: 모든 파이프라인은 idempotent하며 중복 데이터를 자동 제거
- **CSV 기반 저장**: 데이터베이스 없이 파일 기반으로 동작
- **SMI 기반 시그널**: Squeeze Momentum Indicator를 활용한 매수 시그널
- **손절/익절 자동화**: -2% 손절, +5% 익절 규칙 적용
- **포트폴리오 백테스팅**: 다중 종목 동시 보유 시뮬레이션

## 주의사항

- 실제 주문/매수는 수행하지 않습니다 (시그널 알림만 전송)
- 텔레그램 봇 토큰과 채팅 ID는 반드시 설정해야 합니다
- Rate limit을 고려하여 API 호출 시 자동 재시도 및 백오프 적용

