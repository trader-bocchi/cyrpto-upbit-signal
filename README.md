# Upbit Crypto Signal System


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
# .env 파일 생성 (프로젝트 루트에)
# Windows PowerShell:
New-Item -Path .env -ItemType File

# .env 파일에 다음 내용 추가:
```

**.env 파일 내용:**
```env
# Telegram Bot 설정
# 1. 텔레그램에서 @BotFather 검색 후 /newbot 명령으로 봇 생성
# 2. 봇 이름과 사용자명을 입력하면 봇 토큰을 받습니다
# 3. 받은 토큰을 아래에 입력하세요
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Telegram Chat ID 설정
# 1. 생성한 봇에게 아무 메시지나 보냅니다 (예: /start)
# 2. 브라우저에서 다음 URL 접속 (YOUR_BOT_TOKEN을 실제 토큰으로 변경):
#    https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
# 3. 응답에서 "chat":{"id":123456789} 형식의 숫자를 찾아 아래에 입력하세요
TELEGRAM_CHAT_ID=your_chat_id_here
```
## 사용 방법


### 1. 2025년 전체 마켓 1시간 캔들 수집 (단일 스크립트, 권장)

**무설정 실행 스크립트**: `python scripts/collect_upbit_1h_2025.py` 실행만으로 현재 상장 전체 마켓의 2025년 1시간 캔들 CSV를 수집하며, 중간 실패 시 `data/meta/collect_2025_1h_status.json`을 기반으로 다음 실행에서 자동 재개한다.

```bash
python scripts/collect_upbit_1h_2025.py
```

### 2. 일일 증분 수집
```bash
# 1h 증분 수집
python -m src.cli fetch-daily

# 4시간/ 1일 캔들 집계
python -m src.cli aggregate --timeframes 4h,1d

```

### 3. SMI 지표 계산 및 저장
```bash
# 4h, 1d에 대해 SMI 계산 및 저장
python -m src.cli calculate-smi --timeframes 4h,1d
```

### 4. 매수/매도 시그널 실행 및 알림
```bash
python -m src.cli run-signals --timeframes 4h,1d

```

### 5. 백테스팅
```bash
# 2025년 전체 백테스팅
python -m src.cli backtest --year 2025 --timeframes 4h,1d

# 1h, 4h, 1d 모두 백테스팅
python -m src.cli backtest --year 2025 --timeframes 1h,4h,1d

# 혼합 전략 (4h + 1d 시그널 동시 활용)
python -m src.cli backtest --last-days 30 --timeframes 4h,1d --mixed

# 2. 혼합 전략: 4h + 1d 시그널 동시 활용
python -m src.cli backtest --year 2025 --mixed


## 최근 시그널 활용 백테스팅 (4시간, 1일)
python -m src.cli calculate-smi --timeframes 4h,1d
python -m src.cli backtest --last-days 30 --timeframes 4h,1d
```

## 백테스팅 개선 규칙
백테스팅 엔진에는 다음 3가지 개선 규칙이 적용됩니다:

### 1. 시장 레짐 필터 (BTC 1D SMA200)

- **전략 ON 조건**: BTC 1D close > BTC 1D SMA200인 경우에만 신규 진입(BUY) 허용
- **전략 OFF 조건**: BTC 1D close <= BTC 1D SMA200인 경우 신규 진입 차단 또는 비중 축소
- **모드**:
  - `BLOCK_ENTRY` (기본): 레짐 OFF 시 신규 진입 완전 차단
  - `REDUCE_SIZE`: 레짐 OFF 시 진입 비중을 20%로 축소 (기본값, `BACKTEST_REGIME_REDUCE_SIZE_FACTOR`로 조정 가능)
- **설정**: `.env` 파일에서 `BACKTEST_REGIME_ENABLED`, `BACKTEST_REGIME_MODE`, `BACKTEST_REGIME_REDUCE_SIZE_FACTOR`로 제어

### 2. 타임스탑 (Time Stop)

- **정의**: 진입 후 N bars 이내에 익절(+5%)이 발생하지 않으면 N번째 bar의 close로 강제 청산
- **파라미터**:
  - 4h 전략: 12 bars (약 2일)
  - 1d 전략: 7 bars (약 1주)
  - 1h 전략: 24 bars (약 1일)
  - 혼합 전략: 4h와 1d 중 더 짧은 값 사용 (7 bars)
- **우선순위**: 손절/익절이 먼저 발생하면 타임스탑은 적용되지 않음
- **설정**: `.env` 파일에서 `BACKTEST_TIME_STOP_ENABLED`, `BACKTEST_TIME_STOP_BARS_4H`, `BACKTEST_TIME_STOP_BARS_1D`, `BACKTEST_TIME_STOP_BARS_1H`로 제어

### 3. 노출 상한 (리스크 캡)

- **A) 동시 보유 종목 수 상한**: 최대 10개 종목 동시 보유 제한
  - 신규 BUY 시 현재 보유 종목 수가 10개 이상이면 진입 스킵
  - 설정: `.env` 파일에서 `BACKTEST_MAX_POSITIONS`로 제어 (기본값: 10)
- **B) 한 종목 최대 비중 상한**: 한 종목의 비중이 equity의 15%를 초과하지 못하도록 제한
  - 신규 매수 시 허용 가능한 최대 가치를 계산하여 invest_amount를 자동 조정
  - 설정: `.env` 파일에서 `BACKTEST_MAX_SINGLE_POSITION_WEIGHT`로 제어 (기본값: 0.15)
- **처리 순서**: 
  1. 레짐 필터 적용
  2. 최대 포지션 수 체크
  3. 최대 비중 체크 및 invest_amount 조정
  4. 거래대금 기준 정렬 후 순차 처리

### 백테스팅 리포트 통계

백테스팅 결과에는 다음 통계가 포함됩니다:

- **월별 리포트**:
  - TIME_STOP 청산 횟수
  - TIME_STOP 평균 수익률
- **전체 통계**:
  - 레짐 OFF 기간의 스킵된 진입 횟수 (BLOCK_ENTRY 모드)
  - 축소 진입 횟수 (REDUCE_SIZE 모드)
  - 최대 포지션 수 초과로 인한 스킵 횟수
  - 최대 비중 초과로 인한 스킵/컷 횟수
  - 타임스탑 청산 횟수

### 실시간 시그널에도 동일 규칙 적용

`python -m src.cli run-signals` 명령 실행 시에도 다음 규칙이 적용됩니다:

- **시장 레짐 필터**: BTC 1D SMA200 기준으로 신규 진입 차단/축소
- **동시 보유 종목 수 상한**: 최대 10개 종목 제한
- **거래대금 기준 정렬**: 모든 시그널을 거래대금 기준으로 정렬 후 순차 처리