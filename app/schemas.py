"""파이프라인 전체에서 주고받는 데이터 모델."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "warning"]


class LineItem(BaseModel):
    description: str = Field(default="", description="품목명")
    quantity: Optional[float] = Field(default=None, description="수량")
    unit_price: Optional[float] = Field(default=None, description="단가")
    amount: Optional[float] = Field(default=None, description="금액(수량 x 단가)")


class InvoiceHeader(BaseModel):
    """품목을 제외한 송장 머리말/합계 필드.

    LLM 추출은 이 스키마와 LineItemList를 따로 호출한다. 7B급 모델에 30행짜리
    품목 표와 머리말을 한 번에 요구하면 뒷부분을 잘라먹거나 머리말을 놓친다.
    """

    invoice_number: Optional[str] = Field(default=None, description="송장 번호")
    issue_date: Optional[str] = Field(default=None, description="발행일 (YYYY-MM-DD)")
    due_date: Optional[str] = Field(default=None, description="지급 기한 (YYYY-MM-DD)")
    vendor_name: Optional[str] = Field(default=None, description="공급자(발행사)명")
    buyer_name: Optional[str] = Field(default=None, description="수신자(구매자)명")
    po_number: Optional[str] = Field(default=None, description="발주 번호")
    currency: Optional[str] = Field(default=None, description="통화 코드 (예: EUR, USD, KRW)")
    subtotal: Optional[float] = Field(default=None, description="공급가액 합계")
    tax: Optional[float] = Field(default=None, description="세액")
    shipping: Optional[float] = Field(default=None, description="배송비 등 기타 비용")
    total_amount: Optional[float] = Field(default=None, description="총 청구 금액")


class InvoiceFields(InvoiceHeader):
    """송장에서 뽑아낼 구조화 필드 전체. 검수 화면의 편집 폼도 이 스키마를 따른다."""

    line_items: list[LineItem] = Field(default_factory=list, description="품목 목록")


# --------------------------------------------------------------------------
# LLM 추출 전용 원시 스키마
#
# Optional[...] 필드는 JSON 스키마에서 anyOf[..., null]이 되고, 제약 디코딩은
# 그 null 분기를 가장 싼 경로로 택해버린다. 실제로 문서에 있는 값도 null로
# 흘리는 일이 잦아서, 추출 단계에서는 전부 필수 문자열로 받고 파이썬에서
# None/숫자로 되돌린다.
# --------------------------------------------------------------------------

class RawHeader(BaseModel):
    invoice_number: str = Field(description="송장 번호 (INVOICE #)")
    issue_date: str = Field(description="발행일 YYYY-MM-DD")
    due_date: str = Field(description="지급 기한 YYYY-MM-DD")
    vendor_name: str = Field(description="공급자(발행사)명")
    buyer_name: str = Field(description="수신자(구매자)명")
    po_number: str = Field(description="발주 번호 (P.O. NUMBER)")
    currency: str = Field(description="통화 코드 (EUR, USD, KRW 등)")
    subtotal: str = Field(description="공급가액 (SUBTOTAL)")
    tax: str = Field(description="세액 (SALES TAX / VAT)")
    shipping: str = Field(description="배송비 (SHIPPING & HANDLING)")
    total_amount: str = Field(description="총 청구 금액 (TOTAL DUE)")


class RawLineItem(BaseModel):
    description: str = Field(description="품목명")
    quantity: str = Field(description="수량")
    unit_price: str = Field(description="단가")
    amount: str = Field(description="행 합계 금액")


class RawLineItemList(BaseModel):
    line_items: list[RawLineItem] = Field(default_factory=list)


class FieldProbe(BaseModel):
    """비어 있던 필드를 문서에서 다시 찾아본 결과."""

    field: str = Field(description="확인한 필드명")
    value: str = Field(description="문서에 적힌 값 그대로. 없으면 빈 문자열")


class FieldProbeResult(BaseModel):
    findings: list[FieldProbe] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    field: str = Field(default="document", description="문제가 발생한 필드명")
    message: str = Field(description="사람이 읽을 수 있는 오류 설명")
    severity: Severity = Field(default="critical")
    source: Literal["llm", "rule"] = Field(default="llm", description="탐지 주체")


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Docling 추출 산출물."""

    markdown: str
    docling_json: dict
    page_count: int = 0
