"""애플리케이션 전역 설정."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"

# 화면에서 올린 오류 신고. data/ 아래에 두어 .gitignore 가 그대로 덮는다 --
# 캡처에는 송장 내용이 찍히므로 저장소로 새어 나가면 안 된다.
REPORTS_DIR = DATA_DIR / "reports"

# 예전 SQLite 파일. 지금은 MS-SQL로 옮기는 migrate_sqlite_to_mssql.py 에서만 쓴다.
SQLITE_PATH = DATA_DIR / "documents.db"

# MS SQL Server
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
MSSQL_SERVER = os.getenv("MSSQL_SERVER", r"localhost\SQLEXPRESS")
# 기본값을 개발용 DB로 둔다. 원본(DocumentVerification)은 전환 작업 중 건드리지 않는
# 사본으로 남겨 두고, Streamlit·HTTP API·MCP 가 모두 이 하나를 본다 -- 화면마다 다른
# DB를 보면 같은 문서가 다르게 보인다. 원본이 필요하면 MSSQL_DATABASE 로 명시한다.
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "DocumentVerification_Dev")
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

# 업로드를 허용할 확장자. Docling이 읽을 수 있는 것 중, 송장·영수증이 실제로
# 오갈 만한 형식만 열어 둔다. 음성·영상까지 열면 이 파이프라인과 무관한 파일이
# 들어와 처리 시간만 낭비된다.
UPLOAD_EXTENSIONS = [
    "pdf",
    "docx", "doc",
    "pptx", "ppt",
    "xlsx", "xls",
    "html", "htm",
    "md", "txt",
    "csv",
    "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp",
]

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


# 신고 첨부로 받을 이미지 확장자. 화면 캡처만 받으면 되므로 문서 형식은 열지 않는다.
REPORT_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]

# 신고 한 건에 붙일 수 있는 캡처 수. 붙여넣은 이미지는 base64로 브라우저에서
# 넘어오므로, 무제한으로 열어 두면 한 번의 rerun payload 가 지나치게 커진다.
MAX_REPORT_IMAGES = 4


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
