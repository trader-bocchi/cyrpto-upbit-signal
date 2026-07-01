"""설정 관리 모듈"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent.parent

# Telegram 설정 (환경 변수 필수, 기본값 없음)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 텔레그램 설정 검증
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN 환경 변수가 설정되지 않았습니다. .env 파일 또는 환경 변수를 확인하세요.")
if not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID 환경 변수가 설정되지 않았습니다. .env 파일 또는 환경 변수를 확인하세요.")

# 데이터 경로
DATA_ROOT = Path(os.getenv("DATA_ROOT", "data"))
RAW_DATA_PATH = DATA_ROOT / "raw"
RAW_DATA_PATH_1H = RAW_DATA_PATH / "candles_1h"  # 월 단위 1시간 캔들 저장 경로
DERIVED_DATA_PATH = DATA_ROOT / "derived"
META_DATA_PATH = DATA_ROOT / "meta"
RESULTS_PATH = Path(os.getenv("RESULTS_PATH", "results"))

# 경로 생성
for path in [RAW_DATA_PATH, DERIVED_DATA_PATH, META_DATA_PATH, RESULTS_PATH]:
    path.mkdir(parents=True, exist_ok=True)

# SMI 파라미터
SMI_BB_LENGTH = int(os.getenv("SMI_BB_LENGTH", "20"))
SMI_BB_MULT = float(os.getenv("SMI_BB_MULT", "2.0"))
SMI_KC_LENGTH = int(os.getenv("SMI_KC_LENGTH", "20"))
SMI_KC_MULT = float(os.getenv("SMI_KC_MULT", "1.5"))
SMI_LOCAL_MIN_WINDOW = int(os.getenv("SMI_LOCAL_MIN_WINDOW", "100"))
SMI_REQUIRE_NEGATIVE_PIVOT = os.getenv("SMI_REQUIRE_NEGATIVE_PIVOT", "true").lower() == "true"
SMI_REQUIRE_POSITIVE_PIVOT = os.getenv("SMI_REQUIRE_POSITIVE_PIVOT", "true").lower() == "true"

# 이동평균 파라미터
MA_SMA50 = int(os.getenv("MA_SMA50", "50"))
MA_SMA200 = int(os.getenv("MA_SMA200", "200"))

# 시그널 필터
SIGNAL_ENABLE_SMA50_FILTER = os.getenv("SIGNAL_ENABLE_SMA50_FILTER", "false").lower() == "true"

# 백테스팅 파라미터
BACKTEST_INITIAL_CASH = int(os.getenv("BACKTEST_INITIAL_CASH", "10000000"))
BACKTEST_POSITION_SIZE_PCT = float(os.getenv("BACKTEST_POSITION_SIZE_PCT", "0.10"))
BACKTEST_FEE_RATE = float(os.getenv("BACKTEST_FEE_RATE", "0.0005"))
BACKTEST_STOP_LOSS_PCT = float(os.getenv("BACKTEST_STOP_LOSS_PCT", "0.02"))
BACKTEST_TAKE_PROFIT_PCT = float(os.getenv("BACKTEST_TAKE_PROFIT_PCT", "0.05"))
BACKTEST_ENTRY_USE_OPEN = os.getenv("BACKTEST_ENTRY_USE_OPEN", "true").lower() == "true"

# 시장 레짐 필터 (BTC 1D SMA200)
BACKTEST_REGIME_ENABLED = os.getenv("BACKTEST_REGIME_ENABLED", "false").lower() == "true"
BACKTEST_REGIME_MODE = os.getenv("BACKTEST_REGIME_MODE", "BLOCK_ENTRY")  # BLOCK_ENTRY | REDUCE_SIZE
BACKTEST_REGIME_REDUCE_SIZE_FACTOR = float(os.getenv("BACKTEST_REGIME_REDUCE_SIZE_FACTOR", "0.2"))

# 타임스탑 (N bars 내 익절 미달 시 청산)
BACKTEST_TIME_STOP_ENABLED = os.getenv("BACKTEST_TIME_STOP_ENABLED", "true").lower() == "true"
BACKTEST_TIME_STOP_BARS_4H = int(os.getenv("BACKTEST_TIME_STOP_BARS_4H", "18"))  # 3일 (24h*3/4h) — fixed 모드용
BACKTEST_TIME_STOP_BARS_1D = int(os.getenv("BACKTEST_TIME_STOP_BARS_1D", "7"))
BACKTEST_TIME_STOP_BARS_1H = int(os.getenv("BACKTEST_TIME_STOP_BARS_1H", "24"))  # 1h는 24 bars (1일)

# 동적(모멘텀 기반) 타임스탑 — 동적A: SMI가 2봉 연속 하락하면 청산, 하한/상한 봉수로 제한
#   adaptive: 모멘텀 살아있으면 보유, 꺾이면 청산 (최소~최대 봉 사이)
#   fixed   : 위 BACKTEST_TIME_STOP_BARS_* 고정 봉수 사용
BACKTEST_TIME_STOP_MODE = os.getenv("BACKTEST_TIME_STOP_MODE", "adaptive")
BACKTEST_TIME_STOP_MIN_BARS_4H = int(os.getenv("BACKTEST_TIME_STOP_MIN_BARS_4H", "6"))   # 1일
BACKTEST_TIME_STOP_MAX_BARS_4H = int(os.getenv("BACKTEST_TIME_STOP_MAX_BARS_4H", "42"))  # 7일
BACKTEST_TIME_STOP_MIN_BARS_1D = int(os.getenv("BACKTEST_TIME_STOP_MIN_BARS_1D", "1"))
BACKTEST_TIME_STOP_MAX_BARS_1D = int(os.getenv("BACKTEST_TIME_STOP_MAX_BARS_1D", "7"))

# 노출 상한 (리스크 캡)
BACKTEST_MAX_POSITIONS = int(os.getenv("BACKTEST_MAX_POSITIONS", "10"))
BACKTEST_MAX_SINGLE_POSITION_WEIGHT = float(os.getenv("BACKTEST_MAX_SINGLE_POSITION_WEIGHT", "0.15"))
BACKTEST_MIN_INVEST_AMOUNT = int(os.getenv("BACKTEST_MIN_INVEST_AMOUNT", "10000"))  # 최소 투자 금액

# Upbit API
UPBIT_API_BASE = "https://api.upbit.com/v1"
UPBIT_RATE_LIMIT_RETRIES = 5
UPBIT_RATE_LIMIT_BACKOFF_BASE = 2.0

# KST 타임존
KST_OFFSET_HOURS = 9

