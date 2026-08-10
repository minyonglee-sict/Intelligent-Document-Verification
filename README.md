# Intelligent Document Verification

송장·영수증을 업로드하면 **Docling**으로 텍스트를 추출하고, 표는 파서가 직접 읽고,
머리말은 **Ollama**로 뽑아낸 뒤, 규칙 엔진과 근거 대조로 검증해 **MS SQL Server**에
저장하고, 오류 건만 골라 사람이 검수·승인하는 파이프라인입니다.

PDF뿐 아니라 **Word·PowerPoint·Excel·HTML·이미지**도 받습니다 (`config.UPLOAD_EXTENSIONS`).
Docling이 형식별로 읽어 같은 Markdown으로 만들어 주므로, 그 뒤 과정은 형식과
무관하게 동일합니다. 이미지는 텍스트 레이어가 없어 OCR이 자동으로 켜집니다.

```
Streamlit 업로드(다건 드래그앤드롭)
   PDF / DOCX / PPTX / XLSX / HTML / 이미지
      ↓
Docling   → Markdown + Docling JSON  (형식이 달라도 여기서 하나로 합쳐진다)
      ↓
[추출] 표 파서 → 품목 (Markdown 표를 직접 읽음, 즉시·정확)
       Ollama  → 머리말 1콜 + 빈 필드 재확인 1콜(조건부)
      ↓
[검증] 규칙 엔진 + 근거 대조 → is_valid, errors[]   (둘 다 결정적, LLM 없음)
      ↓
MS-SQL    → documents + line_items + validation_errors
      ↓
Streamlit 검수 화면 (ERROR / PENDING) → 수정 → 승인 → status='VALIDATED'
```

## DB 스키마 (MS SQL Server)

| 테이블 | 내용 |
|---|---|
| `documents` | 문서 1건당 1행. 상태·원문(`markdown`)과 **머리말 12개 컬럼** (`doc_type`, `invoice_number`, `issue_date`, `vendor_name`, `subtotal`, `total_amount` …) |
| `line_items` | 품목 1행당 1행. `document_id` + `position` 순서. 품목별 `tax` 포함 |
| `validation_errors` | 검증 오류 1건당 1행. `resolved=1`이면 해소됨 |

## 문서 유형

`doc_type` 은 `INVOICE` / `RECEIPT` / `UNKNOWN` 입니다. `validator.classify_document()`
가 원문의 낱말로 **결정적으로** 판별합니다 — 문서가 스스로 밝히는 사실이라 LLM에
물을 이유가 없습니다. 검수 화면에서 라디오 버튼으로 고칠 수 있습니다.

유형에 따라 같은 컬럼의 이름이 달라집니다 (`invoice_number` → 송장 번호 / 영수증 번호).
`DOC_TYPE_LABELS` 에 표기를 모아 두었습니다.

기본 접속은 `localhost\SQLEXPRESS` / `DocumentVerification` / Windows 인증입니다.
DB가 없으면 앱 시작 시 `db.init_db()`가 만듭니다.

송장 값은 JSON 덩어리가 아니라 **실제 컬럼**입니다. `json_extract` 없이 바로
조회·집계할 수 있습니다.

```sql
SELECT vendor_name, SUM(total_amount) FROM documents
WHERE status = 'VALIDATED' GROUP BY vendor_name;
```

`db.HEADER_COLUMNS` 와 `InvoiceHeader` 스키마가 어긋나면 import 시점에 바로
`RuntimeError`가 납니다. 필드를 추가하고 컬럼 추가를 잊어 값이 조용히 저장되지
않는 사고를 막기 위한 것입니다.

예전 SQLite(`data/documents.db`)로 쓰던 데이터는
[migrate_sqlite_to_mssql.py](migrate_sqlite_to_mssql.py)로 한 번 옮기면 됩니다.

역할 분담이 핵심입니다. **기계가 확실히 할 수 있는 일은 기계에게, 판단이 필요한
것만 LLM에게** 맡깁니다.

| 단계 | 담당 | 하는 일 | 왜 |
|---|---|---|---|
| 추출 | Docling | 픽셀 → 구조 (표·문단 인식) | 표 복원은 전용 모델이 낫다 |
| 추출 | 표 파서 | 복원된 표 → 품목 데이터 | 이미 구조화된 걸 LLM에 다시 받아쓰게 할 이유가 없다 |
| 추출 | **LLM** | 자유 서식 머리말 → 필드 | 문서마다 자리·이름이 달라 규칙으로 못 잡는다 |
| 추출 | **LLM** | 비어 있는 필드만 되묻기 | 규칙이 못 보는 "원문엔 있는데 빠졌다"를 잡는다 |
| **검증** | 규칙 엔진 | 산술·날짜·필수값 | 결정적이어야 하는 판단 |
| **검증** | 근거 대조 | 추출값이 원문에 실제로 있는지 | 지어낸 값을 결정적으로 차단 |

LLM은 **추출에만** 관여합니다. 그 값이 맞는지 가리는 검증 두 갈래는 모두 규칙이라,
같은 입력이면 항상 같은 결과가 나옵니다.

## 실행

```powershell
streamlit run main.py
```

### 최초 1회 세팅 (새 PC일 때만)

이미 세팅된 환경이면 건너뛰세요. 사이드바의 **Ollama 연결 확인** / **DB 연결 확인**
버튼으로 준비가 끝났는지 바로 볼 수 있습니다.

```powershell
pip install -r requirements.txt   # 1. 패키지 (docling이 torch를 함께 받아 용량이 큽니다)
ollama serve                      # 2. Ollama 서버. Windows는 트레이 앱이 이미 띄워둡니다
ollama pull qwen2.5:7b            # 3. 모델. `ollama list`에 있으면 불필요
```

DB 쪽은 세 가지가 필요합니다.

- **SQL Server** (Express 판으로 충분) — 인스턴스가 `localhost\SQLEXPRESS` 가 아니면
  `MSSQL_SERVER` 로 지정
- **ODBC Driver 18 for SQL Server** — `Get-OdbcDriver -Name "*SQL Server*"` 로 확인
- **명명 인스턴스를 쓴다면 SQL Server Browser 서비스** — 동적 포트를 해석하는 데 필요.
  꺼져 있으면 `Set-Service SQLBrowser -StartupType Automatic; Start-Service SQLBrowser`

데이터베이스 자체는 앱 시작 시 `db.init_db()` 가 없으면 만들어 줍니다.

UI 없이 배치로 돌리려면:

```powershell
python run_pipeline.py 송장.pdf
python run_pipeline.py data\uploads\*.pdf --force   # 중복 해시도 다시 처리
```

## 구조

| 파일 | 역할 |
|---|---|
| [main.py](main.py) | Streamlit 진입점, 탭 구성, 사이드바 현황 |
| [app/config.py](app/config.py) | 경로·모델·상태 상수, 환경변수 |
| [app/schemas.py](app/schemas.py) | Pydantic 모델 (추출 필드, 검증 결과) |
| [app/extractor.py](app/extractor.py) | Docling PDF → Markdown / JSON |
| [app/table_parser.py](app/table_parser.py) | Markdown 표 → 품목 (LLM 없이) |
| [app/validator.py](app/validator.py) | Ollama 머리말 추출 + 규칙·근거 대조·빈 필드 재확인 |
| [app/db.py](app/db.py) | MS-SQL 스키마와 CRUD (pyodbc) |
| [app/pipeline.py](app/pipeline.py) | 업로드→추출→검증→저장 오케스트레이션 |
| [app/ui_upload.py](app/ui_upload.py) | 업로드 화면 |
| [app/ui_review.py](app/ui_review.py) | 검수 화면 (ERROR 수정 + PENDING 승인) |
| [app/ui_documents.py](app/ui_documents.py) | 전체 문서 조회 |
| [run_pipeline.py](run_pipeline.py) | CLI 배치 실행 |
| [migrate_sqlite_to_mssql.py](migrate_sqlite_to_mssql.py) | 예전 SQLite 데이터 이관 (1회용) |
| [queries.sql](queries.sql) | DBeaver/SSMS용 조회 쿼리 모음 |

## 상태 흐름

| 상태 | 의미 | 검수 탭에서 |
|---|---|---|
| `PROCESSING` | 처리 중 | 표시 안 함. 처리 시작 즉시 기록되어 해시를 선점한다 |
| `ERROR` | 검증 오류 있음 | **검수 대기** 섹션. 빨간 뱃지로 오류를 보여주고, 값을 고쳐 승인 |
| `PENDING` | 검증 통과 | **승인 대기** 섹션. 고칠 것이 없으면 그대로 승인(개별 또는 전체) |
| `VALIDATED` | 담당자 승인 완료 (최종) | 목록에서 빠짐 |
| `FAILED` | 추출/LLM 호출 자체가 실패 | 검수 대상 아님. `failure_reason` 참조 |

`VALIDATED` 전환은 **승인 버튼으로만** 일어납니다. `ERROR`든 `PENDING`이든 사람이
한 번은 눌러야 마감됩니다 — 자동으로 완료되는 경로는 없습니다.

재검증 버튼은 필드와 오류 로그만 갱신하며 상태를 바꾸지 않습니다. 바꾸면 고치던
문서가 목록에서 사라져 승인할 수 없게 되기 때문입니다.

## 추출과 검증

**판정에는 LLM이 관여하지 않습니다.** LLM은 값을 채우는 데까지만 쓰이고,
그 값이 맞는지 가리는 일은 전부 결정적인 규칙이 합니다.

```
추출  ├ 머리말 LLM              값을 뽑는다
      ├ 표 파서                 품목을 읽는다 (LLM 없음)
      ├ drop_ungrounded         근거 없는 값을 비운다
      └ probe_missing_fields    빈 필드만 LLM에 되묻고, 근거 확인 후 채운다
        ─────────────────────────────────────────────────
검증  ├ rule_check              산술·날짜·필수값
      └ grounding_check         추출값이 원문에 실제로 있는지
        둘 다 결정적. 같은 입력이면 항상 같은 결과.
```

빈 필드 재확인은 **추출 단계**에 있습니다. 찾은 값을 그 자리에서 채워야 저장까지
이어지기 때문입니다. 검증 단계에서 하면 값을 찾아 놓고도 빈 칸으로 남습니다.

### 1. 규칙 엔진 (검증)

(`validator.rule_check`, source=`rule`) — 필수값 누락, 날짜 파싱·순서,
`수량 × 단가 = 금액`, `품목 합계 = 공급가액`, `공급가액 + 세액 + 배송비 = 총액`, 음수 금액.
검수 화면의 **재검증** 버튼은 이것만 씁니다 (즉시, 사람이 고친 값이 기준).

필수값은 **송장 번호 · 발행일 · 공급자명** 셋뿐입니다. "이게 없으면 어떤 청구인지,
누구에게 언제 지급할지를 알 수 없다"가 기준입니다. **총액은 필수가 아닙니다** —
총액 구역이 아예 없는 송장 양식이 실제로 있고(`invoice-2-0.pdf`), 그런 문서를 매번
검수로 돌리는 건 소음입니다. 대신 총액이 **있을 때는** 값을 확인합니다.

| 상황 | 결과 |
|---|---|
| 총액 없음 | 통과 (문서에 없으면 없는 것) |
| 총액 + 내역 있고 `공급가액+세액+배송비 = 총액` | 통과 |
| 총액 + 내역 있는데 안 맞음 | `critical` |
| 총액만 있고 내역 표기 없는데 품목 합계와 다름 | `warning` (할인 등 미표기 조정 가능) |

### 2. 근거 대조 (검증)

(`validator.grounding_check`, source=`rule`) — 추출된 값이 원문에
실제로 있는지 확인합니다. **모델이 지어낸 값을 결정적으로 잡습니다.**
날짜(`2021-05-04` ↔ `04.05.2021` ↔ `Jan 03, 2024`)와 숫자(`4,936.71` ↔ `4936.71`)는
표기 차이를 펼쳐 대조하므로, 정규화된 값이 잘못 걸리지 않습니다.

실측 (문서 8건 기준):

| | |
|---|---|
| 정상 문서에서 검사한 값 55개 | **잘못된 경고 0건** |
| 값을 일부러 바꿔 넣은 64건 | **64건 전부 검출 (100%)** |

두 숫자를 함께 봐야 의미가 있습니다. "경고 0건"만으로는 검사가 잘 도는 것인지
아무것도 안 잡는 것인지 구분되지 않고, "100% 검출"만으로는 멀쩡한 값까지 걸러내는
것인지 알 수 없습니다. 재현은 `_appears_in()` 에 값을 바꿔 넣어 보면 됩니다.

### 3. 빈 필드 재확인 (추출)

(`validator.probe_missing_fields`, source=`llm`) — 비어 있는 필드만 골라
"이게 문서에 있나?"를 되묻습니다. **답한 값이 원문에 실제로 있을 때만** 씁니다.

값의 종류에 따라 처리가 갈립니다.

| 종류 | 근거 확인 후 |
|---|---|
| 글자 (공급자명·문서번호 …) | **그 자리에서 채웁니다.** 이미 검증된 값을 검수자가 다시 타이핑할 이유가 없습니다 |
| 금액 (총액·공급가액 …) | **채우지 않고 제안만** 합니다 |

금액을 채우지 않는 이유는 **자리를 틀리기 쉽기 때문**입니다. 총액 자리에 품목 하나의
금액(`2426.58`)을 제안한 적이 있는데, 그 숫자가 원문에 있으니 근거 확인은
통과했습니다. 그래서 품목에 이미 있는 숫자는 합계 자리 제안에서 아예 뺍니다.

근거가 확인되지 않는 답은 조용히 버립니다. 빈 필드가 없으면 LLM 호출 자체를
건너뜁니다.

`critical` 오류가 하나라도 있으면 `is_valid=False` → `status='ERROR'`.
`warning`만 있으면 통과시키되 오류 로그에는 남깁니다.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MSSQL_SERVER` | `localhost\SQLEXPRESS` | SQL Server 인스턴스 |
| `MSSQL_DATABASE` | `DocumentVerification` | 데이터베이스명 (없으면 생성) |
| `MSSQL_DRIVER` | `ODBC Driver 18 for SQL Server` | ODBC 드라이버 |
| `MSSQL_USER` / `MSSQL_PASSWORD` | (비움) | 비우면 Windows 인증 |
| `OLLAMA_MODEL` | `qwen2.5:7b` | 추출·검증에 쓸 모델 |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 주소 |
| `OLLAMA_TIMEOUT` | `900` | 1콜당 타임아웃(초) |
| `MAX_DOC_CHARS` | `24000` | LLM에 넘길 최대 문자 수 |
| `OLLAMA_NUM_CTX` | `8192` | 컨텍스트 길이 |
| `NUM_PREDICT_HEADER` | `512` | 머리말 추출 생성 토큰 상한 |
| `NUM_PREDICT_ITEMS` | `3072` | 품목 추출(LLM 폴백) 생성 토큰 상한 |
| `NUM_PREDICT_VALIDATE` | `1024` | 빈 필드 재확인 생성 토큰 상한 |
| `STALE_PROCESSING_MINUTES` | `90` | 이 시간을 넘긴 `PROCESSING` 행은 `FAILED`로 정리 |
| `DOCLING_OCR` | `0` | 스캔 PDF면 `1` (느려짐) |
| `DOCLING_COMPILE` | `0` | `torch.compile` 사용 여부 |

## 구현 메모

세 가지는 실제로 걸려서 고친 것들이라 되돌리지 마세요.

**1. Optional 필드는 추출용 스키마에 쓰지 않습니다.**
`Optional[str]`은 JSON 스키마에서 `anyOf[string, null]`이 되고, Ollama의 제약 디코딩은
그 `null` 분기를 가장 싼 경로로 골라버립니다. 문서에 멀쩡히 있는 값도 `null`로
흘렸습니다. 그래서 추출 단계는 `RawHeader`/`RawLineItem`처럼 **전부 필수 문자열**로 받고
(`""`가 "없음"), `validator.to_text` / `to_number`로 파이썬에서 되돌립니다.
같은 문서에서 `invoice_number`, `issue_date`, `vendor_name`, 합계 4종이
`null` → 전부 정상 추출로 바뀌었습니다.

**2. 품목 표는 LLM에게 받아쓰게 하지 않습니다** (`app/table_parser.py`).
처음에는 LLM으로 뽑았는데, 20행에 315초가 걸리고 **93행짜리 문서에서는 900초
타임아웃으로 실패**했습니다. 게다가 수량 15를 10으로 읽는 오독까지 냈습니다.
Docling이 이미 표를 복원해 둔 마당에 그걸 다시 받아쓰게 한 것이 설계 실수였습니다.

지금은 Markdown 표를 파이썬으로 직접 읽습니다. 실측 결과 **문서 8건 195행 전부
`수량 × 단가 = 금액` 검산 통과**, 0건이던 93행 문서가 84행 전부 추출됩니다.

까다로웠던 지점 둘:
- Docling은 페이지가 넘어가면 같은 표를 별개 표로 쪼개고, 이어지는 조각의 첫
  데이터 행이 헤더 자리에 옵니다. 열 개수까지 달라지는 경우가 있어
  (품목번호가 품목명에 붙은 3열 → 따로 떨어진 4열), 값의 생김새로 열을 다시
  추정하는 `infer_mapping()`이 필요했습니다.
- 열 이름 매칭은 **순서가 중요합니다.** `Item # Ordered Service` 의 'Ordered' 가
  수량으로 먼저 잡히면 품목명 열을 통째로 잃습니다. price → amount →
  description → quantity 순으로 배정합니다.

수량 열이 없는 양식은 품목명 안의 `(10)` / `Qty. 2` 를 수량으로 읽되,
**`수량 × 단가 = 금액` 이 성립할 때만** 받아들입니다. 추측이 되지 않도록.

**3. LLM 호출에 `num_predict` 상한을 겁니다** (`config.NUM_PREDICT_*`).
배열 스키마에서는 모델이 EOS를 못 찾고 컨텍스트가 찰 때까지 찍어내는 일이 있습니다.
상한에 걸려 JSON이 잘리면 `validator._salvage_objects`가 온전한 객체만 건져냅니다.

**3-1. 추출 실패를 조용히 삼키지 않습니다.**
품목 추출이 타임아웃으로 실패했을 때 `except Exception: return []` 이 그것을
삼켜서, 화면에서는 **"품목이 없는 문서"와 "추출이 실패한 문서"가 똑같이 0건**으로
보였습니다. 검수자가 그 차이를 모른 채 승인할 수 있는 상태였습니다. 지금은 실패
사유가 `critical` 검증 오류로 검수 화면에 뜹니다.

**3-2. LLM 검증을 "모순을 찾아라"에서 "빈 필드만 되묻기"로 바꿨습니다.**
처음에는 원문과 추출 JSON을 통째로 주고 모순을 찾게 했습니다. 실측 성적이
**문서 5건에서 유효 1건, 오탐 5건**이었습니다. 원문에 없는 총액이 "있는데 누락됐다"고
하고, 프롬프트가 금지한 "누락 필드" 지적을 반복하고, 한국어로 쓰라는데 영어로 답했습니다.

같은 모델이 같은 Markdown을 읽으니 **자기 채점**이라는 한계도 있습니다. 그래서
- 환각 탐지는 LLM에서 떼어내 `grounding_check`(결정적)로 옮기고,
- LLM에는 빈 필드만 좁게 되묻되 **답한 값의 근거를 원문에서 확인한 뒤에만** 채택합니다.

바꾼 뒤 같은 문서에서 **지적 1건, 오탐 0건**. 그 1건이 실제 누락이었습니다
(원문 `DUE DATE 23.09.2019`, 추출값 `null`).

이후 이 되묻기를 `validate()` 에서 `extract_fields()` 로 옮겼습니다. 찾은 값을 그
자리에서 채워야 저장까지 이어지는데, 검증 단계에서 하면 값을 찾아 놓고도 빈 칸으로
남기 때문입니다. 그 결과 **검증 단계에는 LLM이 하나도 남지 않았습니다.**

**4. 중복 검사는 처리 '시작' 시점에 자리를 예약합니다** (`db.reserve_document`).
문서 1건에 5~30분이 걸리는데, 끝나고 저장할 때 중복을 확인하면 그 사이에 시작한
두 번째 실행을 막지 못합니다. 실제로 같은 파일이 두 건(#17, #18)으로 들어갔습니다.

원인은 `pipeline.process_pdf` 안에 `st.*` 호출이 하나도 없다는 점입니다. Streamlit은
`st.*` 호출 사이에서만 중단 신호를 확인하므로, 처리 중에 rerun이 걸려도 실행 중인
스크립트를 멈추지 못하고 **새 스크립트가 나란히 돌아갑니다.**

그래서 두 겹으로 막습니다.
- `reserve_document()`가 한 트랜잭션 안에서 조회(`WITH (UPDLOCK, HOLDLOCK)`)와 삽입을
  함께 처리해 `PROCESSING` 행으로 해시를 선점합니다. 세션이나 탭이 달라도 막힙니다.
- `ui_upload`의 `pipeline_busy` 플래그가 같은 세션의 재진입을 막습니다.

앱이 처리 도중 죽으면 `PROCESSING` 행이 남아 재업로드를 영원히 막으므로,
`main.py` 시작 시 `cleanup_stale_processing()`이 오래된 행을 `FAILED`로 풀어줍니다.

**5. `torch.compile`을 끕니다** (`extractor.USE_TORCH_COMPILE`).
CPU 추론에서 이득이 거의 없는 데다, Windows 한국어 로캘(cp949)에서는
torch inductor가 UTF-8 템플릿 파일을 시스템 인코딩으로 읽다가
`UnicodeDecodeError`를 내고 Docling 레이아웃 모델 로딩 자체가 죽습니다.

## 성능

2페이지·품목 20행짜리 송장을 CPU + `qwen2.5:7b`로 돌린 실측:

| 단계 | 하는 일 | 시간 |
|---|---|---|
| Docling (첫 실행) | PDF → Markdown 변환. 레이아웃 모델 로딩 포함 | ~110초 |
| Docling (이후) | 모델이 메모리에 올라와 있어 변환만 | 수 초 |
| **표 파싱** | Markdown 표 → 품목. 행 수와 무관 | **0.01초 미만** |
| LLM 머리말 추출 | 송장번호·발행일·거래처와 아래쪽 합계 4종. 값 11개 | ~25–320초 |
| **근거 대조** | 추출값이 원문에 실제로 있는지 | **0.01초 미만** |
| LLM 빈 필드 재확인 | 비어 있는 필드만 되묻기. 빈 필드 없으면 **0초** | ~100–300초 |
| 규칙 검증 | 산술·날짜·필수값 | 0.02초 |

품목 추출을 LLM에서 파서로 옮기면서 **문서당 5분 이상, 대형 문서에서는 15분
이상이 사라졌습니다.**

**문서 1건에 5~8분**입니다. 대부분이 CPU 추론 시간이라, 다른 무거운 프로세스가
같이 돌면 눈에 띄게 더 느려집니다. GPU를 쓰거나 `OLLAMA_MODEL`을 바꾸면 정확도와
속도가 함께 개선됩니다.
