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

# 4h, 1d 캔들 수집
python -m src.cli fetch-4h-1d-direct

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

python -m src.cli calculate-smi --timeframes 4h --markets KRW-MON

python -m src.cli backtest --last-days 30 --timeframes 4h,1d
```

``` 
백테스팅 적용 로직

- SMI 기반 매수 시그널
ㄴ 규칙 1: 시장 레짐 필터 (BTC 1D SMA200)
ㄴ 규칙 2: 동시 보유 종목 수 상한
ㄴ 규칙 3: 단일 종목 최대 비중 상한


- 매수조건
진입 금액: 현금 보유의 10%
수수료: 0.05%

- 매도실행
1순위: 손절 (STOP)
보유 2% 이하시
2순위: 익절 (TAKE)
보유 5% 이상시

3순위: 타임스탑 (TIME_STOP)
4h: 최대 2일 보유
1d: 최대 7일 보유


```

### 6. 시각화

``` bash
# 기본 사용 (화면에 표시)
python candle_smi_chart.py --market KRW-BTC --timeframes 4h,1d

# 최근 30일 데이터 (기본값)
python candle_smi_chart.py --market KRW-BTC --timeframes 4h,1d --days 30

# 파일로 저장
python candle_smi_chart.py --market KRW-BTC --timeframes 4h,1d --output charts/

# 여러 시간프레임 동시 생성
python candle_smi_chart.py --market KRW-MON --timeframes 4h,1d --days 30
```


### 7. run all

``` bash
python -m src.cli fetch-daily
# python -m src.cli fetch-4h-1d-direct
python -m src.cli aggregate --timeframes 4h,1d
python -m src.cli calculate-smi --timeframes 4h,1d
python -m src.cli run-signals --timeframes 4h
python -m src.cli run-signals --timeframes 1d
#1

### 진짜 시그널 없는지 리체크하는 코드
python verify_smi_signals.py

### 최근 90일치 데이터를 가져와서 신호 생성하는 코드 (github action용)
python batch/main.py


```
