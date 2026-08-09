"""애플리케이션 전역 설정."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"

# 예전 SQLite 파일. 지금은 MS-SQL로 옮기는 migrate_sqlite_to_mssql.py 에서만 쓴다.
SQLITE_PATH = DATA_DIR / "documents.db"

# MS SQL Server
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
MSSQL_SERVER = os.getenv("MSSQL_SERVER", r"localhost\SQLEXPRESS")
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "DocumentVerification")
# 둘 다 비어 있으면 Windows 인증(Trusted_Connection)을 쓴다.
MSSQL_USER = os.getenv("MSSQL_USER", "")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "")
MSSQL_TIMEOUT = int(os.getenv("MSSQL_TIMEOUT", "30"))


def connection_string(database: str | None = None) -> str:
    """pyodbc 연결 문자열. database=None 이면 설정된 기본 DB."""
    parts = [
        f"DRIVER={{{MSSQL_DRIVER}}}",
        f"SERVER={MSSQL_SERVER}",
        f"DATABASE={database or MSSQL_DATABASE}",
        "TrustServerCertificate=yes",
    ]
    if MSSQL_USER:
        parts += [f"UID={MSSQL_USER}", f"PWD={MSSQL_PASSWORD}"]
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts) + ";"

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# 품목이 많은 송장은 구조화 JSON 생성만으로도 수 분이 걸린다(CPU 추론 기준).
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "900"))

# LLM에 넘길 마크다운 최대 길이(문자). 초과 시 앞/뒤를 남기고 중간을 잘라낸다.
MAX_DOC_CHARS = int(os.getenv("MAX_DOC_CHARS", "24000"))

OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

# 호출별 생성 토큰 상한. 배열 스키마에서 모델이 멈추지 못하고 계속 찍어내는 것을 막는다.
NUM_PREDICT_HEADER = int(os.getenv("NUM_PREDICT_HEADER", "512"))
NUM_PREDICT_ITEMS = int(os.getenv("NUM_PREDICT_ITEMS", "3072"))
NUM_PREDICT_VALIDATE = int(os.getenv("NUM_PREDICT_VALIDATE", "1024"))

# 금액 검증 허용 오차
AMOUNT_TOLERANCE = 0.02

# 문서 상태
STATUS_PROCESSING = "PROCESSING"  # 처리 중. 해시 선점용으로 시작 즉시 기록한다
STATUS_PENDING = "PENDING"
STATUS_ERROR = "ERROR"
STATUS_VALIDATED = "VALIDATED"
STATUS_FAILED = "FAILED"  # 파이프라인 자체가 실패(추출/LLM 오류)

STATUS_LABELS = {
    STATUS_PROCESSING: "⏳ 처리 중",
    STATUS_PENDING: "🟡 검증 통과",
    STATUS_ERROR: "🔴 오류",
    STATUS_VALIDATED: "🟢 승인 완료",
    STATUS_FAILED: "⚫ 처리 실패",
    "SKIPPED": "⏭️ 중복 건너뜀",
}

# PROCESSING 상태로 이 시간을 넘긴 행은 중단된 처리로 보고 FAILED로 정리한다.
# 문서 1건이 최대 3콜 x OLLAMA_TIMEOUT 이므로 그보다 넉넉하게 잡는다.
STALE_PROCESSING_MINUTES = int(os.getenv("STALE_PROCESSING_MINUTES", "90"))


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
