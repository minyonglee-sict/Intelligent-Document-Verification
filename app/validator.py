"""Ollama 기반 필드 추출 + 검증.

기계가 확실히 할 수 있는 일은 기계에게, 판단이 필요한 것만 LLM에게 맡긴다.

  머리말 추출   LLM   자유 서식이라 문서마다 자리·이름이 달라 규칙으로 못 잡는다
  품목 추출     파서  Docling이 복원해 둔 표를 table_parser가 그대로 읽는다.
                      표를 못 읽은 문서에서만 LLM 폴백이 돈다
  규칙 검증     규칙  산술·날짜·필수값
  근거 대조     규칙  추출값이 원문에 실제로 있는지. 환각을 결정적으로 잡는다
  빈 필드 재확인 LLM  비어 있는 필드만 좁게 되묻고, 답한 값이 원문에 있을 때만 채택

즉 LLM 호출은 문서당 최대 2회(머리말 + 빈 필드 재확인)이고, 빈 필드가 없으면
두 번째는 아예 건너뛴다.

추출 스키마가 Optional이 아닌 필수 문자열인 이유는 schemas.py의 주석을 참고할 것.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from . import config, table_parser
from .schemas import (
    FieldProbeResult,
    InvoiceFields,
    InvoiceHeader,
    LineItem,
    RawHeader,
    RawLineItem,
    RawLineItemList,
    ValidationIssue,
    ValidationResult,
)

_EXTRACT_BASE = """You are a document data extraction engine for invoices.
Return ONLY JSON matching the provided schema.

Rules:
- Copy values verbatim from the document. Never invent or guess a value.
- EVERY field is required. If a value is genuinely absent from the document,
  return the empty string "". Never return null and never invent a placeholder.
- Numeric fields are strings too: write the digits as they appear in the document.
"""

HEADER_SYSTEM = _EXTRACT_BASE + """
This call extracts the header and summary fields ONLY. Ignore the individual rows
of the goods/services table.

Field guidance:
- invoice_number: the document's own number. Labelled "INVOICE #", "INVOICE NO",
  "RECEIPT #", "Receipt#", "영수증 번호", "번호" depending on the document type.
  Not the P.O. number and not the customer ID.
- po_number: labelled "P.O. NUMBER", "PURCHASE ORDER".
- issue_date: the invoice date ("DATE:", "발행일"). due_date: the payment deadline.
  Both must be normalized to YYYY-MM-DD. Ambiguous formats like 04.05.2021 are
  day-first (DD.MM.YYYY) unless the document clearly says otherwise. A relative term
  such as "Due after 30 days" is not a date — return "" for due_date.
- vendor_name: the company ISSUING the invoice — the letterhead at the top, or the
  party named in "make checks payable to".
- buyer_name: the party the invoice is addressed to ("TO:", "BILL TO:").
- subtotal / tax / shipping / total_amount: the summary rows at the bottom of the
  table ("SUBTOTAL", "SALES TAX", "SHIPPING & HANDLING", "TOTAL DUE").
"""

ITEMS_SYSTEM = _EXTRACT_BASE + """
This call extracts the goods/services table ONLY.

- Emit one entry per data row, in document order, for EVERY row in the table.
  Do not stop early, do not summarize, do not merge rows.
- The table may continue across several pages — include the rows from all of them.
- "amount" is the row's line total, "unit_price" the per-unit price.
- Do NOT emit the summary rows (SUBTOTAL, SALES TAX, SHIPPING & HANDLING, TOTAL DUE)
  as line items. Skip them entirely.
"""

PROBE_SYSTEM = """You are a document lookup engine for invoices.

You receive an invoice document and a list of field names that came back EMPTY
during extraction. For each field, look through the document once more and report
the value if the document actually contains it.

Rules:
- Copy the value EXACTLY as written in the document. Do not reformat, normalize,
  or compute it. If the document says "23.09.2019", return "23.09.2019".
- If the field is genuinely not stated in the document, return the empty string "".
  Many invoices legitimately have no due date, no tax line, or no total. Returning
  "" is the correct answer in those cases, not a failure.
- Never derive a value by adding up other numbers. Only report what is written.
- Return one entry per requested field, no more.
"""


@lru_cache(maxsize=1)
def get_client():
    import ollama

    return ollama.Client(host=config.OLLAMA_HOST, timeout=config.OLLAMA_TIMEOUT)


def health_check() -> tuple[bool, str]:
    """Ollama 서버와 모델 사용 가능 여부를 확인한다."""
    try:
        models = get_client().list().get("models", [])
    except Exception as exc:  # 서버 미기동 등
        return False, f"Ollama 서버에 연결할 수 없습니다 ({config.OLLAMA_HOST}): {exc}"

    names = {m.get("model", "") for m in models}
    if config.OLLAMA_MODEL not in names:
        return False, (
            f"모델 '{config.OLLAMA_MODEL}' 이(가) 없습니다. "
            f"`ollama pull {config.OLLAMA_MODEL}` 로 내려받으세요. "
            f"사용 가능: {', '.join(sorted(n for n in names if n)) or '(없음)'}"
        )
    return True, f"{config.OLLAMA_MODEL} 사용 가능"


def _truncate(text: str, limit: int = config.MAX_DOC_CHARS) -> str:
    """긴 문서는 앞부분과 뒷부분(합계가 있는 곳)을 남기고 중간을 잘라낸다."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n\n...[중략]...\n\n{text[-tail:]}"


def _chat_text(system: str, user: str, schema: dict, *, num_predict: int) -> str:
    """제약 디코딩으로 스키마에 맞는 JSON 문자열을 받아온다.

    num_predict는 반드시 건다. 배열 스키마에서는 모델이 EOS를 못 찾고 컨텍스트가
    찰 때까지 행을 계속 찍어내는 일이 있고, 그러면 호출이 타임아웃까지 멈춘다.
    """
    response = get_client().chat(
        model=config.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        format=schema,
        options={
            "temperature": 0,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": num_predict,
        },
    )
    return response["message"]["content"]


def _chat_json(system: str, user: str, schema: dict, *, num_predict: int) -> dict:
    return json.loads(_chat_text(system, user, schema, num_predict=num_predict))


_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def _salvage_objects(text: str) -> list[dict]:
    """num_predict에 걸려 잘린 JSON에서 온전한 객체만 건져낸다.

    품목 일부라도 검수 화면에 올리는 편이 통째로 버리는 것보다 낫다.
    """
    salvaged = []
    for match in _OBJECT_RE.findall(text):
        try:
            item = json.loads(match)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or "description" not in item:
            continue
        # RawLineItem은 전 필드가 필수다. 잘린 객체는 빈 문자열로 채워 넣는다.
        salvaged.append({key: str(item.get(key, "")) for key in RawLineItem.model_fields})
    return salvaged


# --------------------------------------------------------------------------
# 1) 추출
# --------------------------------------------------------------------------

_BLANK = {"", "n/a", "na", "none", "null", "-", "없음", "미기재"}
_NUMBER_RE = re.compile(r"-?[\d.,]+")


def to_text(raw: str | None) -> Optional[str]:
    value = (raw or "").strip()
    return None if value.lower() in _BLANK else value


def to_number(raw: str | None) -> Optional[float]:
    """'$4,271.05', '4.271,05', '1 234.5' 같은 표기를 float으로 되돌린다."""
    value = to_text(raw)
    if value is None:
        return None

    match = _NUMBER_RE.search(value.replace(" ", ""))
    if not match:
        return None
    token = match.group()

    if "," in token and "." in token:
        # 뒤에 오는 구분자가 소수점이다: 4,271.05 / 4.271,05
        decimal_sep = "," if token.rfind(",") > token.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        token = token.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in token:
        # 소수점 자리가 1~2자리면 소수 구분자, 아니면 천 단위 구분자로 본다.
        head, _, tail = token.rpartition(",")
        token = f"{head}.{tail}" if len(tail) in (1, 2) else token.replace(",", "")

    try:
        return float(token)
    except ValueError:
        return None


def extract_fields(markdown: str) -> tuple[InvoiceFields, list[ValidationIssue]]:
    """머리말/합계와 품목 표를 각각 따로 뽑아 하나로 합친다.

    추출 도중 생긴 문제(품목 호출 실패·응답 잘림)는 검증 오류로 함께 돌려준다.
    """
    document = _truncate(markdown)

    raw_header = RawHeader.model_validate(
        _chat_json(
            HEADER_SYSTEM,
            f"Extract the header and summary fields from this invoice:\n\n{document}",
            RawHeader.model_json_schema(),
            num_predict=config.NUM_PREDICT_HEADER,
        )
    )
    header = InvoiceHeader(
        doc_type=classify_document(markdown),
        invoice_number=to_text(raw_header.invoice_number),
        issue_date=to_text(raw_header.issue_date),
        due_date=to_text(raw_header.due_date),
        vendor_name=to_text(raw_header.vendor_name),
        buyer_name=to_text(raw_header.buyer_name),
        po_number=to_text(raw_header.po_number),
        currency=to_text(raw_header.currency),
        subtotal=to_number(raw_header.subtotal),
        tax=to_number(raw_header.tax),
        shipping=to_number(raw_header.shipping),
        total_amount=to_number(raw_header.total_amount),
    )

    # 모델이 지어낸 값은 저장 전에 비운다. 근거가 없으면 값이 아니다.
    header, issues = drop_ungrounded(header, markdown)

    # 표 파싱에는 토큰 한계가 없으므로 잘리지 않은 원문을 그대로 넘긴다.
    line_items, failure = _extract_line_items(markdown)
    if failure:
        issues.append(
            ValidationIssue(
                field="line_items",
                message=failure,
                severity="critical",
                source="rule",
            )
        )
    fields = InvoiceFields(**header.model_dump(), line_items=line_items)

    # 상호는 첫 제목에서 결정적으로 건질 수 있는 경우가 많다. LLM이 놓쳤을 때만 쓴다.
    if fields.vendor_name in (None, ""):
        candidate = vendor_from_heading(markdown)
        if candidate:
            fields.vendor_name = candidate
            issues.append(
                ValidationIssue(
                    field="vendor_name",
                    message=(
                        f"공급자명이 비어 있어 문서 제목의 '{candidate}' 로 "
                        f"채웠습니다. 확인 후 승인하세요."
                    ),
                    severity="warning",
                    source="rule",
                )
            )

    # 비어 있는 필드를 좁게 되묻는다. 여기서 하는 이유는, 근거가 확인된 값을
    # 그 자리에서 채워 넣어야 저장까지 이어지기 때문이다. 검증 단계에서 하면
    # 값을 찾아 놓고도 빈 칸으로 남는다.
    try:
        issues += probe_missing_fields(markdown, fields)
    except Exception as exc:
        issues.append(
            ValidationIssue(
                field="document",
                message=f"빈 필드 재확인을 수행하지 못했습니다: {exc}",
                severity="warning",
                source="rule",
            )
        )
    return fields, issues


def _extract_line_items(markdown: str) -> tuple[list[LineItem], Optional[str]]:
    """(품목, 실패 사유) 를 돌려준다.

    Docling이 이미 표를 복원해 두었으므로 먼저 그대로 읽는다. 표를 읽지 못한
    경우에만 LLM으로 넘긴다.

    품목 추출만 실패해도 머리말 값은 살려서 검수 화면에 올린다. 다만 실패를
    조용히 삼키면 '품목이 없는 문서'와 '품목 추출이 실패한 문서'가 화면에서
    똑같이 0건으로 보인다. 검수자가 그 차이를 모르고 승인하면 안 되므로
    사유를 함께 돌려 검증 오류로 남긴다.
    """
    parsed = table_parser.parse_line_items(markdown)
    if parsed:
        return parsed, None

    # 폴백: 표 구조를 못 찾았다. LLM에게 읽혀 본다.
    document = _truncate(markdown)
    try:
        raw = _chat_text(
            ITEMS_SYSTEM,
            f"Extract every row of the goods/services table:\n\n{document}",
            RawLineItemList.model_json_schema(),
            num_predict=config.NUM_PREDICT_ITEMS,
        )
    except Exception as exc:
        return [], (
            f"품목 표를 읽지 못해 LLM으로 시도했으나 그것도 실패했습니다: {exc}. "
            f"품목이 비어 있으니 원문을 직접 확인하세요."
        )

    truncated = False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"line_items": _salvage_objects(raw)}
        truncated = True

    try:
        parsed = RawLineItemList.model_validate(payload)
    except Exception as exc:
        return [], f"품목 응답을 해석하지 못했습니다: {exc}"

    items = [
        LineItem(
            description=to_text(item.description) or "",
            quantity=to_number(item.quantity),
            unit_price=to_number(item.unit_price),
            amount=to_number(item.amount),
        )
        for item in parsed.line_items
    ]
    if truncated:
        return items, (
            f"품목 응답이 생성 한도({config.NUM_PREDICT_ITEMS} 토큰)에 걸려 잘렸습니다. "
            f"{len(items)}건만 복구했으므로 누락이 있을 수 있습니다."
        )
    return items, None


# --------------------------------------------------------------------------
# 2) 근거 대조 - 추출값이 원문에 실제로 있는지 결정적으로 확인한다
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")

# (필드, 화면 표기, 종류)
GROUNDED_FIELDS: list[tuple[str, str, str]] = [
    ("invoice_number", "송장 번호", "text"),
    ("po_number", "발주 번호", "text"),
    ("vendor_name", "공급자명", "text"),
    ("buyer_name", "수신자명", "text"),
    ("issue_date", "발행일", "date"),
    ("due_date", "지급 기한", "date"),
    ("subtotal", "공급가액", "number"),
    ("tax", "세액", "number"),
    ("shipping", "배송비", "number"),
    ("total_amount", "총 청구 금액", "number"),
]


def _normalize(text: str) -> str:
    # Docling 마크다운에는 '&amp;' 같은 HTML 엔티티가 그대로 남아 있고, 모델은
    # 보통 '&' 로 돌려준다. 풀어서 비교하지 않으면 멀쩡한 값이 근거 없음이 된다.
    return _WS.sub(" ", html.unescape(text)).lower()


_MONTH_NAMES = [
    ("Jan", "January"), ("Feb", "February"), ("Mar", "March"), ("Apr", "April"),
    ("May", "May"), ("Jun", "June"), ("Jul", "July"), ("Aug", "August"),
    ("Sep", "September"), ("Oct", "October"), ("Nov", "November"), ("Dec", "December"),
]


def _date_variants(value: str) -> list[str]:
    """YYYY-MM-DD 를 문서에 쓰였을 법한 표기들로 펼친다.

    숫자 표기만 만들면 'Jan 03, 2024' 같은 영문 월 이름을 못 찾아, 맞게 추출한
    날짜를 근거 없음으로 오판해 비워버린다(Receipt_1.pdf 에서 실제로 발생).
    """
    parsed = _parse_date(value)
    if parsed is None:
        return [value]
    y, m, d = parsed.year, parsed.month, parsed.day

    out = []
    for sep in (".", "/", "-", " "):
        out += [
            f"{d:02d}{sep}{m:02d}{sep}{y}",
            f"{m:02d}{sep}{d:02d}{sep}{y}",
            f"{y}{sep}{m:02d}{sep}{d:02d}",
            f"{d}{sep}{m}{sep}{y}",
            f"{m}{sep}{d}{sep}{y}",
        ]

    for name in _MONTH_NAMES[m - 1]:  # 약어와 전체 이름 둘 다
        out += [
            f"{name} {d:02d}, {y}", f"{name} {d}, {y}",
            f"{name} {d:02d} {y}", f"{name} {d} {y}",
            f"{d:02d} {name} {y}", f"{d} {name} {y}",
            f"{d}-{name}-{y}", f"{d:02d}-{name}-{y}",
        ]

    # 한국어 표기
    out += [f"{y}년 {m}월 {d}일", f"{y}년 {m:02d}월 {d:02d}일"]
    return out


def _number_variants(value: float) -> list[str]:
    """4936.71 을 문서 표기 후보로 펼친다 (천 단위 구분, 소수 표기 차이)."""
    out = {f"{value:.2f}", f"{value:,.2f}", f"{value:g}"}
    if value == int(value):
        out |= {str(int(value)), f"{int(value):,}", f"{int(value)}.00"}
    # 유럽식 표기
    out |= {s.replace(",", "\x00").replace(".", ",").replace("\x00", ".") for s in list(out)}
    return list(out)


def _appears_in(haystack: str, value, kind: str) -> bool:
    """값이 원문에 있는지 본다. 날짜·숫자는 표기 차이를 감안한다.

    모델이 숫자 자리에 여러 값을 뭉쳐 돌려주는 일이 있어(예: '1440.00\\n1632.00'),
    변환에 실패하면 글자 그대로 비교한다. 여기서 예외가 나면 검증 전체가 멈춘다.
    """
    if kind == "date":
        candidates = _date_variants(str(value))
    elif kind == "number":
        number = value if isinstance(value, (int, float)) else to_number(str(value))
        candidates = _number_variants(float(number)) if number is not None else [str(value)]
    else:
        candidates = [str(value)]
    return any(_normalize(c) in haystack for c in candidates if c)


def drop_ungrounded(
    fields: InvoiceFields, markdown: str
) -> tuple[InvoiceFields, list[ValidationIssue]]:
    """원문에서 확인되지 않는 추출값을 비운다.

    모델은 프롬프트로 금지해도 가끔 값을 지어낸다(invoice-2-2 에서 원문에 없는
    합계 12,387.69 를 만들어냈다). 지어낸 값을 저장해 두고 오류만 띄우면, 검수자가
    그것을 지우는 일까지 해야 하고 승인 시 잘못된 값이 그대로 남을 위험이 있다.
    근거가 없으면 애초에 비운 채로 둔다 -- 빈 값은 사람이 채우면 되지만, 그럴듯한
    거짓값은 눈에 잘 띄지 않는다.

    비웠다는 사실 자체는 반드시 알린다. 조용히 지우면 추출 실패와 구분되지 않는다.
    """
    haystack = _normalize(markdown)
    issues: list[ValidationIssue] = []

    for name, label, kind in GROUNDED_FIELDS:
        value = getattr(fields, name)
        if value in (None, ""):
            continue

        if _appears_in(haystack, value, kind):
            # 근거는 있다. 다만 날짜 칸에 날짜가 아닌 값이 들어오는 일이 있다
            # ('Due 30 days after receipt'). 원문에 분명히 있으니 근거 대조는
            # 통과하지만 날짜가 아니므로, 그대로 두면 규칙 검증이 곧바로
            # critical 로 반려한다. 채워 넣느니만 못하므로 비운다.
            if kind == "date" and _parse_date(value) is None:
                setattr(fields, name, None)
                issues.append(
                    ValidationIssue(
                        field=name,
                        message=(
                            f"{label}에 원문의 '{value}' 이(가) 들어왔지만 날짜가 아니라 "
                            f"비워 두었습니다. 필요하면 직접 채우세요."
                        ),
                        severity="warning",
                        source="rule",
                    )
                )
            continue

        # 거래처명은 모델이 상호와 주소를 뭉쳐 돌려주면서 줄 순서를 바꾸기도 한다
        # ('UiPath / 60th Floor, 1 Vanderbilt Ave' <- 원문은 순서가 반대).
        # 통째로 버리면 원문에 분명히 있는 상호까지 잃으므로 첫 줄만 살려 본다.
        if kind == "text":
            head = re.split(r"[\n,]", str(value))[0].strip()
            if head and head != str(value) and _appears_in(haystack, head, kind):
                setattr(fields, name, head)
                issues.append(
                    ValidationIssue(
                        field=name,
                        message=(
                            f"{label}으로 추출된 값이 원문과 달라 첫 줄 '{head}' 만 "
                            f"남겼습니다. 필요하면 직접 고치세요."
                        ),
                        severity="warning",
                        source="rule",
                    )
                )
                continue

        setattr(fields, name, None)
        issues.append(
            ValidationIssue(
                field=name,
                message=(
                    f"{label}으로 추출된 '{value}' 을(를) 원문에서 찾을 수 없어 "
                    f"비웠습니다. 원문에 실제로 있다면 직접 채워 넣으세요."
                ),
                severity="warning",
                source="rule",
            )
        )
    return fields, issues


def grounding_check(fields: InvoiceFields, markdown: str) -> list[ValidationIssue]:
    """추출값이 원문에 실제로 있는지 확인한다. 환각을 결정적으로 잡는다.

    LLM 검증과 달리 판단이 개입하지 않는다. 문서에 없는 값을 지어냈으면
    무조건 걸리고, 있으면 무조건 통과한다.
    """
    haystack = _normalize(markdown)
    issues: list[ValidationIssue] = []

    for name, label, kind in GROUNDED_FIELDS:
        value = getattr(fields, name)
        if value in (None, ""):
            continue
        if _appears_in(haystack, value, kind):
            continue
        issues.append(
            ValidationIssue(
                field=name,
                message=(
                    f"{label}으로 추출된 '{value}' 을(를) 원문에서 찾을 수 없습니다. "
                    f"모델이 지어냈을 수 있으니 원문과 대조하세요."
                ),
                severity="critical",
                source="rule",
            )
        )
    return issues


# --------------------------------------------------------------------------
# 3) 빈 필드 재확인 - LLM에게 좁게 되묻고, 근거가 확인된 답만 채택한다
# --------------------------------------------------------------------------

PROBE_FIELDS = [name for name, _, _ in GROUNDED_FIELDS] + ["currency"]
_FIELD_LABELS = {name: label for name, label, _ in GROUNDED_FIELDS}
_FIELD_KINDS = {name: kind for name, _, kind in GROUNDED_FIELDS}


def probe_missing_fields(markdown: str, fields: InvoiceFields) -> list[ValidationIssue]:
    """비어 있는 필드만 골라 문서에 있는지 되묻는다.

    '모순을 찾아라' 같은 열린 질문은 오탐이 많았다(문서 5건에서 유효 1건, 오탐 5건).
    좁게 묻고, **모델이 답한 값이 원문에 실제로 있을 때만** 채택한다. 근거가
    확인되지 않는 주장은 조용히 버린다.
    """
    missing = [n for n in PROBE_FIELDS if getattr(fields, n, None) in (None, "")]
    if not missing:
        return []  # 되물을 게 없으면 LLM 호출 자체를 건너뛴다

    haystack = _normalize(markdown)
    payload = _chat_json(
        PROBE_SYSTEM,
        "## Document\n"
        f"{_truncate(markdown)}\n\n"
        "## Fields that came back empty - look for each one\n"
        + "\n".join(f"- {n}" for n in missing),
        FieldProbeResult.model_json_schema(),
        num_predict=config.NUM_PREDICT_VALIDATE,
    )
    result = FieldProbeResult.model_validate(payload)

    # 모델은 합계 자리에 품목 한 줄의 값을 집어오곤 한다(총액으로 2426.58, 세액으로
    # 품목 세액 1440.00 을 제안한 적이 있다). 그 값들은 원문에 있으니 근거 대조는
    # 통과하지만 그 자리의 값이 아니다. 품목에 이미 있는 숫자는 제안에서 뺀다.
    item_numbers = {
        round(v, 2)
        for i in fields.line_items
        for v in (i.amount, i.tax, i.unit_price)
        if v is not None
    }

    issues: list[ValidationIssue] = []
    for finding in result.findings:
        name = finding.field
        if name not in missing:
            continue
        value = to_text(finding.value)
        if value is None:
            continue  # 문서에 없다고 답함 - 정상
        if not _appears_in(haystack, value, _FIELD_KINDS.get(name, "text")):
            continue  # 근거 없는 주장 - 버린다
        kind = _FIELD_KINDS.get(name, "text")
        label = _FIELD_LABELS.get(name, name)

        if kind == "number":
            number = to_number(value)
            if number is not None and round(number, 2) in item_numbers:
                continue  # 품목 값을 합계 자리에 옮겨 온 것 - 버린다
            # 금액은 어느 자리의 값인지 틀리기 쉬우므로 제안만 하고 채우지 않는다.
            issues.append(
                ValidationIssue(
                    field=name,
                    message=(
                        f"{label}이(가) 비어 있지만 원문에 '{value}' 이(가) 있습니다. "
                        f"확인 후 채워 넣으세요."
                    ),
                    severity="warning",
                    source="llm",
                )
            )
            continue

        # 날짜 칸에는 날짜만 넣는다. 원문의 지급 조건('Due 30 days after receipt')은
        # 근거 대조를 통과하지만 날짜가 아니어서, 채우면 규칙 검증이 곧바로
        # critical 로 반려한다. 채우지 않되 무엇을 봤는지는 알린다.
        if kind == "date" and _parse_date(value) is None:
            issues.append(
                ValidationIssue(
                    field=name,
                    message=(
                        f"{label} 자리에 원문의 '{value}' 이(가) 있지만 날짜가 아니라 "
                        f"채우지 않았습니다. 필요하면 직접 채우세요."
                    ),
                    severity="warning",
                    source="llm",
                )
            )
            continue

        # 글자 값은 원문에 그대로 있는 것이 확인됐으므로 채운다. 검수자에게
        # 이미 검증된 값을 다시 타이핑하게 할 이유가 없다.
        setattr(fields, name, value)
        issues.append(
            ValidationIssue(
                field=name,
                message=(
                    f"{label}이(가) 비어 있어 원문에서 찾은 '{value}' 로 채웠습니다. "
                    f"확인 후 승인하세요."
                ),
                severity="warning",
                source="llm",
            )
        )
    return issues




# --------------------------------------------------------------------------
# 4) 규칙 기반 검증 (결정적)
# --------------------------------------------------------------------------

# 이게 없으면 어떤 거래인지, 누구와 언제 거래했는지를 알 수 없는 것들.
# total_amount 는 일부러 뺐다 -- 총액 구역이 아예 없는 송장 양식이 실제로 있고
# (invoice-2-0.pdf), 그런 문서를 매번 검수로 돌리는 것은 소음이다.
# 대신 총액이 '있을 때'는 아래 산술 검사가 값을 확인한다.
REQUIRED_FIELDS = {
    "invoice_number": "문서 번호",
    "issue_date": "발행일",
    "vendor_name": "공급자명",
}

# 문서 유형별 표기. 같은 컬럼이라도 화면에 부르는 이름이 달라야 한다.
DOC_TYPE_LABELS = {
    "INVOICE": {"name": "송장", "invoice_number": "송장 번호"},
    "RECEIPT": {"name": "영수증", "invoice_number": "영수증 번호"},
    "UNKNOWN": {"name": "문서", "invoice_number": "문서 번호"},
}

# 유형 판별 단서. 문서 상단·제목에 쓰이는 낱말이라 결정적으로 볼 수 있다.
# 문서가 스스로를 밝히는 선언. 번호가 따라붙는 형태라 다른 문맥에 걸릴 일이 없다.
_TYPE_DECLARATIONS: list[tuple[str, tuple[str, ...]]] = [
    ("RECEIPT", ("receipt#", "receipt #", "receipt no", "영수증", "수령증")),
    ("INVOICE", ("invoice#", "invoice #", "invoice no", "세금계산서", "청구서")),
]

# 선언이 없을 때만 쓰는 약한 단서. 낱말 하나뿐이라 엉뚱한 문맥에 걸릴 수 있다.
_TYPE_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("RECEIPT", ("receipt",)),
    ("INVOICE", ("invoice",)),
]

# 'Due 30 days after receipt' 의 receipt 는 지급 조건이지 문서 유형이 아니다.
_PAYMENT_TERM = re.compile(r"(?:after|upon|on)\s+receipt")


_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.M)
_DOC_WORDS = re.compile(r"(?i)invoice|receipt|bill\s*to|ship\s*to|영수증|청구서|세금계산서")

# 상호가 아니라 문서의 구조를 가리키는 제목들. 'To' 같은 낱말이 상호로 잡힌 적이 있다.
_STRUCTURE_TITLES = {
    "to", "from", "bill to", "ship to", "sold to", "remit to", "comments", "notes",
    "description", "terms", "summary", "details", "items", "수신", "발신", "비고",
    "적요", "내역", "합계",
}


def vendor_from_heading(markdown: str) -> Optional[str]:
    """문서 맨 위 제목에서 발행사명을 건져낸다.

    송장·영수증은 대개 레터헤드(상호)가 첫 제목이다. LLM이 이걸 실행마다 다르게
    놓치므로(KCC I&C 를 한 번은 찾고 한 번은 못 찾았다) 결정적으로 보완한다.

    다만 'INVOICE 0012456' 처럼 제목이 문서 종류와 번호인 양식도 있어, 그런
    낱말이 들어 있거나 숫자뿐이면 상호로 보지 않는다.

    **첫 제목만** 본다. 아래로 훑으면 'Instructions', 'To' 같은 구조 제목을
    상호로 집어온다. 근거가 약하면 채우지 않는 편이 낫다.
    """
    match = _HEADING.search(markdown)
    if match is None:
        return None

    title = html.unescape(match.group(1)).strip()
    if len(title) < 3 or _DOC_WORDS.search(title):
        return None
    if title.lower().rstrip(":").strip() in _STRUCTURE_TITLES:
        return None
    letters = sum(c.isalpha() for c in title)
    if letters < 2 or letters < len(title.replace(" ", "")) * 0.5:
        return None  # 숫자·기호 위주면 상호가 아니다
    return title


def _find_hint(haystack: str, hint: str) -> int:
    """단서의 첫 위치. 지급 조건 표현('after receipt') 안의 것은 건너뛴다."""
    start = 0
    while True:
        pos = haystack.find(hint, start)
        if pos < 0:
            return -1
        if not _PAYMENT_TERM.search(haystack[max(0, pos - 12):pos + len(hint)]):
            return pos
        start = pos + 1


def _earliest_type(haystack: str, table) -> Optional[str]:
    """단서들 중 가장 앞(제목에 가까운 쪽)에 나온 것의 유형."""
    best_type, best_pos = None, len(haystack) + 1
    for doc_type, hints in table:
        for hint in hints:
            pos = _find_hint(haystack, hint)
            if pos >= 0 and pos < best_pos:
                best_type, best_pos = doc_type, pos
    return best_type


def classify_document(markdown: str) -> str:
    """문서 유형을 판별한다.

    LLM에 묻지 않는다. 'RECEIPT' / 'INVOICE' 같은 낱말은 문서가 스스로 밝히는
    사실이고, 그런 것은 규칙이 더 정확하고 빠르다.

    선언('Invoice No.' / 'Receipt#')을 먼저 찾고, 없을 때만 낱말 하나로 판단한다.
    낱말끼리 위치로만 겨루면 지급 조건이 유형을 이긴다 -- 'Due 30 days after
    receipt' 가 'Invoice No. 1210' 보다 앞에 놓인 송장(invoice-1-2.pdf)이
    RECEIPT 로 분류된 적이 있다. 같은 이유로 그 표현 안의 낱말은 아예 건너뛴다.
    """
    haystack = _normalize(markdown)
    return (
        _earliest_type(haystack, _TYPE_DECLARATIONS)
        or _earliest_type(haystack, _TYPE_WORDS)
        or "UNKNOWN"
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


# 품목명 칸에 수량 표기가 섞여 들어왔는지 본다. Docling이 표의 열 경계를 놓치면
# "Skid-Steer 822-79-9581 5 pcs. € 200" 처럼 한 칸에 뭉쳐 들어오고, 그러면 수량과
# 단가가 나란히 비어 아래 검산이 통째로 건너뛰어진다.
_QUANTITY_IN_TEXT = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:pcs\b\.?|pc\b\.?|pieces?\b|units?\b|ea\b\.?|개|EA\b)",
    re.IGNORECASE,
)


def _numbered_in_source(markdown: str, number: int) -> bool:
    """원문에 'N.' 꼴 품목 번호가 있는지. 표 안('|  7. |')과 밖('7.') 둘 다 본다.

    줄 첫머리로 한정한다. 'Date: 24.06.2020' 같은 본문 속 숫자에 걸리지 않게.
    """
    return (
        re.search(rf"^[ \t]*\|?[ \t]*{number}\.(?=[ \t|]|$)", markdown, re.MULTILINE)
        is not None
    )


def _line_item_integrity(
    fields: InvoiceFields, markdown: Optional[str], add
) -> None:
    """품목이 '통째로 안 뽑힌' 상태를 잡는다.

    값이 하나도 없으면 검산할 것도 없어 조용히 통과한다 -- 아래 수량 x 단가 검산은
    둘 다 비면 '금액만 적는 표'로 보고 건너뛰고, 합계 대조는 머리말 금액이 다 비면
    진입하지 못한다. 그 두 구멍으로 추출 실패가 그대로 VALIDATED 까지 간다.
    """
    items = fields.line_items
    if not items:
        return

    def number_of(order: int, item) -> int:
        return item.position if item.position is not None else order

    # 1) 수량·단가를 가진 행이 하나도 없는데, 품목명에 수량 표기가 섞여 있다.
    #    영수증처럼 금액만 적는 양식과 구분하기 위해 '품목명에 섞였을 것'을 함께 본다.
    if all(i.quantity is None and i.unit_price is None for i in items):
        collapsed = [
            (number_of(order, item), item.description or "")
            for order, item in enumerate(items, start=1)
            if _QUANTITY_IN_TEXT.search(item.description or "")
        ]
        if collapsed:
            numbers = ", ".join(str(n) for n, _ in collapsed[:5])
            more = f" 외 {len(collapsed) - 5}행" if len(collapsed) > 5 else ""
            sample = collapsed[0][1][:60]
            add(
                "line_items",
                f"{numbers}{more}번 품목의 품목명 칸에 수량·단가가 섞여 들어갔습니다 "
                f"(예: '{sample}'). 표의 열이 나뉘지 않은 것으로 보입니다. "
                f"수량·단가를 각 칸에 옮기면 금액을 검산할 수 있습니다.",
            )

    # 2) 머리말 금액이 하나도 없다. 이러면 합계 대조가 통째로 건너뛰어진다.
    #    다만 행별 수량 x 단가 검산이 돌고 있다면 아주 눈먼 상태는 아니다. 실제로
    #    총액 칸 없이 행별 Total 열만 두는 양식이 있어(invoice-2-*), 그것까지
    #    critical 로 올리면 멀쩡한 문서가 검수 대기로 밀린다. 둘 다 못 하는
    #    경우에만 critical 로 세운다.
    if all(
        getattr(fields, name) is None
        for name in ("subtotal", "tax", "shipping", "total_amount")
    ):
        blind = all(i.quantity is None or i.unit_price is None for i in items)
        add(
            "total_amount",
            f"품목이 {len(items)}행 있는데 공급가액·세액·배송비·총 청구액이 모두 "
            f"비어 있습니다. 대조할 기준이 없어 합계 검산을 하지 못합니다."
            + (
                " 행별 수량 x 단가 검산도 할 수 없어 이 문서는 금액을 전혀 "
                "검사하지 못합니다."
                if blind
                else " 행별 수량 x 단가 검산은 그대로 돕니다."
            )
            + " 원문을 확인해 채우거나, 문서에 없다면 그대로 승인하세요.",
            "critical" if blind else "warning",
        )

    # 3) 원문의 품목 번호가 저장된 것보다 뒤까지 이어진다 = 뒷부분이 잘렸다.
    #    문서가 매긴 번호가 1부터 빠짐없이 이어질 때만 본다. 그래야 '다음 번호'가
    #    의미를 갖고, 번호 없는 양식에 헛짚지 않는다.
    if not markdown:
        return
    positions = [i.position for i in items if i.position is not None]
    if len(positions) != len(items) or sorted(positions) != list(range(1, len(items) + 1)):
        return

    missing: list[int] = []
    following = len(items) + 1
    # 상한을 둔다. 번호처럼 보이는 목록이 끝없이 이어지는 문서에서 멈추지 못하면
    # 오류 메시지가 무의미해진다.
    while len(missing) < 200 and _numbered_in_source(markdown, following):
        missing.append(following)
        following += 1
    if missing:
        add(
            "line_items",
            f"품목을 {len(items)}행까지만 가져왔는데 원문에는 {missing[-1]}번까지 "
            f"있습니다. {len(missing)}행({missing[0]}~{missing[-1]}번)이 빠졌습니다. "
            f"표 밖으로 밀려난 행은 표 파싱에서 누락됩니다 -- 원문을 확인하세요.",
        )


def rule_check(
    fields: InvoiceFields, markdown: Optional[str] = None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(field: str, message: str, severity: str = "critical") -> None:
        issues.append(
            ValidationIssue(field=field, message=message, severity=severity, source="rule")
        )

    # 필수값.
    # '추출이 실패했다'로 읽히지 않게 쓴다. 이 시점에는 추출 실패인지 문서에 원래
    # 없는지 구분되지 않으며, 실제로 총액이 없는 송장 양식도 있다. 판단은 사람 몫이다.
    labels = DOC_TYPE_LABELS.get(fields.doc_type, DOC_TYPE_LABELS["UNKNOWN"])
    for name, label in REQUIRED_FIELDS.items():
        if getattr(fields, name) in (None, ""):
            add(
                name,
                f"{labels.get(name, label)}이(가) 비어 있습니다. "
                f"원문을 확인해 채우거나, 문서에 없다면 그대로 승인하세요.",
            )

    # 날짜 형식 / 순서
    issue_date = _parse_date(fields.issue_date)
    due_date = _parse_date(fields.due_date)
    if fields.issue_date and issue_date is None:
        add("issue_date", f"발행일 '{fields.issue_date}' 을(를) 날짜로 해석할 수 없습니다.")
    if fields.due_date and due_date is None:
        add("due_date", f"지급 기한 '{fields.due_date}' 을(를) 날짜로 해석할 수 없습니다.")
    if issue_date and due_date and due_date < issue_date:
        add("due_date", f"지급 기한({fields.due_date})이 발행일({fields.issue_date})보다 빠릅니다.")
    if issue_date and issue_date > date.today():
        add("issue_date", f"발행일({fields.issue_date})이 미래 날짜입니다.", "warning")

    # 품목 합계 vs 공급가액
    tol = config.AMOUNT_TOLERANCE
    item_sum = sum(i.amount for i in fields.line_items if i.amount is not None)
    if fields.line_items and fields.subtotal is not None:
        if abs(item_sum - fields.subtotal) > tol:
            add(
                "line_items",
                f"품목 금액 합계({item_sum:,.2f})가 공급가액({fields.subtotal:,.2f})과 "
                f"{abs(item_sum - fields.subtotal):,.2f} 만큼 다릅니다.",
            )

    # 품목이 통째로 안 뽑힌 상태. 아래 검산들은 값이 있어야 도는 것들이라, 값이
    # 전부 비면 아무것도 걸리지 않는다. 그 구멍을 여기서 먼저 막는다.
    _line_item_integrity(fields, markdown, add)

    # 수량·단가를 갖춘 행이 얼마나 되는지. '금액만 적는 표'라는 면제는 양식 전체에
    # 주는 것이지 행 하나하나에 주는 것이 아니다. 다른 행들이 다 달고 있는데 한 행만
    # 비었다면 그건 양식이 아니라 그 행이 값을 잃은 것이다.
    priced_rows = sum(
        1
        for i in fields.line_items
        if i.quantity is not None and i.unit_price is not None
    )
    form_has_prices = priced_rows >= max(2, len(fields.line_items) // 2)

    # 품목별 수량 x 단가
    for order, item in enumerate(fields.line_items, start=1):
        # 오류 메시지의 번호는 검수 화면·DB가 보여주는 번호와 같아야 검수자가 그 행을
        # 찾을 수 있다. 문서가 품목 번호를 달고 있으면 그것을 쓴다.
        idx = item.position if item.position is not None else order

        # 값이 빠진 행을 그냥 건너뛰면, 금액이 없는 품목이 합계에서 조용히 사라진다.
        # 검산을 못 하는 것과 문제가 없는 것은 다르므로 반드시 알린다.
        if item.amount is None:
            add(
                f"line_items[{idx}]",
                f"{idx}번 품목 '{item.description[:40]}'의 금액이 비어 있습니다. "
                f"품목 합계에서 빠지니 원문을 확인해 채우세요.",
            )
            continue
        if (item.quantity is None) != (item.unit_price is None):
            missing = "수량" if item.quantity is None else "단가"
            add(
                f"line_items[{idx}]",
                f"{idx}번 품목 '{item.description[:40]}'의 {missing}이(가) 비어 있어 "
                f"금액({item.amount:,.2f})을 검산할 수 없습니다.",
                "warning",
            )
            continue
        if item.quantity is None or item.unit_price is None:
            # 둘 다 없는 행. 금액만 적는 양식이면 검산 대상이 아니지만, 같은 문서의
            # 다른 행들이 수량·단가를 달고 있다면 이 행만 값을 잃은 것이다.
            if form_has_prices:
                add(
                    f"line_items[{idx}]",
                    f"{idx}번 품목 '{item.description[:40]}'의 수량·단가가 비어 "
                    f"있습니다. 같은 표의 다른 {priced_rows}행은 둘 다 있으니, 이 행만 "
                    f"값이 빠졌을 수 있습니다. 금액({item.amount:,.2f})은 검산되지 "
                    f"않은 채로 남습니다.",
                    "warning",
                )
            continue
        expected = item.quantity * item.unit_price
        if abs(expected - item.amount) > tol:
            add(
                f"line_items[{idx}]",
                f"{idx}번 품목 '{item.description[:40]}': "
                f"수량 x 단가 = {expected:,.2f} 이지만 금액은 {item.amount:,.2f} 입니다.",
            )
        if item.quantity is not None and item.quantity <= 0:
            add(f"line_items[{idx}]", f"{idx}번 품목의 수량이 {item.quantity} 입니다.")

    # 총액 = 공급가액 + 세액 + 배송비.
    # 세액을 합계 칸이 아니라 품목별 TAX 열로 적는 양식(영수증에 흔하다)이 있어,
    # 합계 칸이 비어 있으면 품목 세액의 합을 쓴다.
    line_tax = sum(i.tax for i in fields.line_items if i.tax is not None)
    effective_tax = fields.tax if fields.tax is not None else (line_tax or None)

    if fields.subtotal is not None and fields.total_amount is not None:
        expected_total = fields.subtotal + (effective_tax or 0) + (fields.shipping or 0)
        if abs(expected_total - fields.total_amount) > tol:
            source = "품목별 세액 합" if fields.tax is None and line_tax else "세액"
            add(
                "total_amount",
                f"공급가액 + {source} + 배송비 = {expected_total:,.2f} 이지만 "
                f"총 청구 금액은 {fields.total_amount:,.2f} 입니다.",
            )
    elif (
        fields.total_amount is not None
        and fields.line_items
        and fields.subtotal is None
        and fields.tax is None
        and fields.shipping is None
    ):
        # 내역(공급가액·세액·배송비)이 하나도 없는 양식. 그렇다면 총액은 품목 합계와
        # 같아야 한다. 할인처럼 문서에 안 적힌 조정이 있을 수 있어 warning 으로 둔다.
        if abs(item_sum - fields.total_amount) > tol:
            add(
                "total_amount",
                f"품목 금액 합계({item_sum:,.2f})와 총 청구 금액"
                f"({fields.total_amount:,.2f})이 {abs(item_sum - fields.total_amount):,.2f} "
                f"만큼 다릅니다. 문서에 내역(공급가액·세액·배송비) 표기가 없습니다.",
                "warning",
            )

    # 음수 금액
    for name in ("subtotal", "tax", "shipping", "total_amount"):
        value = getattr(fields, name)
        if value is not None and value < 0:
            add(name, f"{name} 값이 음수({value:,.2f})입니다.")

    return issues


# --------------------------------------------------------------------------
# 통합
# --------------------------------------------------------------------------

def _dedupe(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """규칙 결과를 먼저 넣고, 같은 필드에 대한 중복 LLM 지적은 버린다."""
    seen: set[tuple[str, str]] = set()
    out: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.field, issue.message.strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def validate(
    markdown: str,
    fields: InvoiceFields,
    extra_issues: Optional[list[ValidationIssue]] = None,
) -> ValidationResult:
    """결정적인 두 갈래로 판정한다.

      - rule_check      : 산술·날짜·필수값
      - grounding_check : 추출값이 원문에 실제로 있는지 (환각 차단)

    LLM이 관여하는 빈 필드 재확인은 extract_fields 에서 이미 끝났고, 그 결과는
    extra_issues 로 넘어온다. 검증 단계에는 판단이 개입하지 않는다.
    """
    issues = list(extra_issues or [])
    issues += rule_check(fields, markdown)
    issues += grounding_check(fields, markdown)
    issues = _dedupe(issues)
    critical = [i for i in issues if i.severity == "critical"]
    return ValidationResult(is_valid=not critical, errors=issues)


def revalidate_fields(
    fields: InvoiceFields, markdown: Optional[str] = None
) -> ValidationResult:
    """검수 화면에서 수정한 필드에 대한 빠른 재검증(규칙만, LLM 호출 없음).

    markdown 을 주면 원문과 대조하는 검사(품목 잘림)까지 함께 돈다. 주지 않으면
    그 검사만 빠지므로, 고치는 도중 오류가 사라졌다 나타났다 하지 않게 호출부에서
    되도록 넘긴다.
    """
    issues = rule_check(fields, markdown)
    critical = [i for i in issues if i.severity == "critical"]
    return ValidationResult(is_valid=not critical, errors=issues)
