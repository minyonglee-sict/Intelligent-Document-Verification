"""Docling Markdown 표에서 품목을 직접 읽어낸다.

Docling이 이미 표 구조를 복원해 놓았으므로, 그걸 LLM에게 다시 받아쓰게 할 이유가
없다. 받아쓰게 하면 느리고(품목 93행에서 900초 타임아웃) 오독도 생긴다(수량 15를
10으로 읽은 사례). 여기서는 표를 그대로 읽는다. 읽지 못한 경우에만 LLM으로 넘긴다.

까다로운 지점 둘:
  - Docling은 페이지가 넘어가면 같은 표를 별개 표로 쪼개고, 이어지는 조각의 첫
    데이터 행이 헤더 자리에 온다. 앞 표의 열 구성을 기억해 두었다가 이어붙인다.
  - 열 이름이 문서마다 다르다. 'QUANTITY/DESCRIPTION/UNIT PRICE/TOTAL' 도 있고
    'Item # Ordered Service/Item Price/Total' 처럼 번호와 품목명이 한 열에 붙은
    것도 있다. 구체적인 단서부터 차례로 맞춰 나간다.
"""

from __future__ import annotations

import re
from typing import Optional

from .schemas import LineItem

# 합계 구역 행. 품목이 아니므로 제외한다.
SUMMARY_ROW = re.compile(
    r"(?i)\b(sub\s*total|sales\s*tax|vat|shipping|handling|total\s*due|amount\s*due|"
    r"balance\s*due|grand\s*total)\b|합계|소계|부가세|공급가액"
)

# 수량 열이 따로 없을 때 품목명 안에 섞여 있는 수량 표기.
#   'Exterior Protection (10)'  /  'Dwarf Senna Qty. 2'
EMBEDDED_COUNT = [
    re.compile(r"\((\d+(?:\.\d+)?)\)\s*$"),
    re.compile(r"(?i)\bqty\.?\s*[:x]?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(?i)\bx\s*(\d+(?:\.\d+)?)\s*$"),
]

# 열 이름 단서. 위에 놓인 것부터 배정하고, 이미 잡힌 열은 건너뛴다.
# 순서가 중요하다.
#   - price 계열이 description 보다 앞: 'Item Price' 가 품목명으로 잡히면 안 된다.
#   - description 이 quantity 보다 앞: 'Item # Ordered Service' 의 'Ordered' 가
#     수량으로 먼저 잡혀 품목명 열을 통째로 잃는 일이 있었다.
COLUMN_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("unit_price", ("unit price", "item price", "unit cost", "rate", "price", "단가")),
    ("amount", ("line total", "total", "amount", "금액", "합계")),
    ("description", ("description", "service", "product", "item", "품목", "내역", "적요")),
    ("quantity", ("quantity", "qty", "ordered", "units", "수량")),
]

NUMERIC = re.compile(r"^\s*[-+]?[\d.,\s]+\s*$")


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    joined = "".join(cells)
    return bool(joined) and set(joined) <= set("-: ")


def _iter_tables(markdown: str):
    """마크다운에서 표를 (첫 행, 나머지 행들) 로 끊어 낸다."""
    first: Optional[list[str]] = None
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("|"):
            cells = _split_row(line)
            if _is_separator(cells):
                continue
            if first is None:
                first = cells
            else:
                rows.append(cells)
        elif first is not None:
            yield first, rows
            first, rows = None, []
    if first is not None:
        yield first, rows


def map_columns(header: list[str]) -> dict[str, int]:
    """헤더 문자열로 열 위치를 추정한다. description 과 amount 가 필수다."""
    mapping: dict[str, int] = {}
    taken: set[int] = set()
    for field, hints in COLUMN_HINTS:
        for idx, name in enumerate(header):
            if idx in taken:
                continue
            low = name.lower()
            if any(h in low for h in hints):
                mapping[field] = idx
                taken.add(idx)
                break
    return mapping


def _looks_like_data(cells: list[str], mapping: dict[str, int]) -> bool:
    """이 행이 헤더가 아니라 데이터인지 본다 (페이지 넘김 조각의 첫 행 판별)."""
    idx = mapping.get("amount")
    if idx is None or idx >= len(cells):
        return False
    return bool(NUMERIC.match(cells[idx])) and cells[idx].strip() != ""


def _is_number(cell: str) -> bool:
    return bool(cell.strip()) and bool(NUMERIC.match(cell))


def infer_mapping(rows: list[list[str]]) -> dict[str, int]:
    """헤더가 없는 조각에서 데이터 모양만 보고 열을 추정한다.

    Docling은 페이지가 넘어갈 때 같은 표를 열 개수까지 다르게 쪼개기도 한다
    (품목번호가 품목명에 붙은 3열 -> 따로 떨어진 4열). 그때는 헤더 문자열이
    없으므로 값의 생김새로 판단한다: 오른쪽 끝 숫자 두 열이 단가와 금액,
    가장 긴 글자 열이 품목명.
    """
    if len(rows) < 2:
        return {}
    width = max(len(r) for r in rows)
    if width < 3:
        return {}

    numeric_ratio, text_len = [], []
    for idx in range(width):
        cells = [r[idx] for r in rows if idx < len(r)]
        if not cells:
            numeric_ratio.append(0.0)
            text_len.append(0.0)
            continue
        numeric_ratio.append(sum(_is_number(c) for c in cells) / len(cells))
        non_numeric = [c for c in cells if not _is_number(c)]
        text_len.append(sum(len(c) for c in non_numeric) / len(cells))

    numeric_cols = [i for i, r in enumerate(numeric_ratio) if r >= 0.8]
    if len(numeric_cols) < 2:
        return {}
    unit_price, amount = numeric_cols[-2], numeric_cols[-1]

    remaining = [i for i in range(width) if i not in (unit_price, amount)]
    if not remaining:
        return {}
    description = max(remaining, key=lambda i: text_len[i])
    if text_len[description] < 3:  # 글자다운 열이 없으면 품목 표가 아니다
        return {}

    return {"description": description, "unit_price": unit_price, "amount": amount}


def _cell(cells: list[str], mapping: dict[str, int], field: str) -> Optional[str]:
    idx = mapping.get(field)
    if idx is None or idx >= len(cells):
        return None
    return cells[idx]


def _build_item(cells: list[str], mapping: dict[str, int]) -> Optional[LineItem]:
    from .validator import to_number, to_text  # 순환 임포트를 피해 지연 임포트

    description = to_text(_cell(cells, mapping, "description")) or ""
    amount = to_number(_cell(cells, mapping, "amount"))
    unit_price = to_number(_cell(cells, mapping, "unit_price"))
    quantity = to_number(_cell(cells, mapping, "quantity"))

    if not description and amount is None:
        return None

    # 수량 열이 없는 양식은 품목명 안에 수량이 섞여 있다 ('(10)', 'Qty. 2').
    # 추측이 되지 않도록, 수량 x 단가 = 금액 이 성립할 때만 받아들인다.
    if quantity is None and unit_price and amount is not None:
        for pattern in EMBEDDED_COUNT:
            match = pattern.search(description)
            if not match:
                continue
            candidate = float(match.group(1))
            if abs(candidate * unit_price - amount) <= 0.02:
                quantity = candidate
                break

    return LineItem(
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
    )


def parse_line_items(markdown: str) -> list[LineItem]:
    """마크다운에서 품목을 읽어낸다. 품목 표를 찾지 못하면 빈 목록."""
    items: list[LineItem] = []
    active: Optional[dict[str, int]] = None

    for header, rows in _iter_tables(markdown):
        mapping = map_columns(header)

        if "description" in mapping and "amount" in mapping:
            # 헤더가 그대로 읽히는 표
            active = mapping
            data_rows = rows
        elif active is not None and _looks_like_data(header, active) and len(header) == max(active.values()) + 1:
            # 앞 표와 같은 모양으로 이어지는 조각. 헤더 자리의 행도 데이터다.
            mapping = active
            data_rows = [header, *rows]
        elif active is not None:
            # 열 개수까지 달라진 조각. 값의 생김새로 열을 다시 추정한다.
            # 품목 표를 이미 한 번 만난 뒤에만 시도해, 주소·날짜 표를 잘못
            # 품목으로 읽는 일을 막는다.
            candidate_rows = [header, *rows]
            mapping = infer_mapping(candidate_rows)
            if "description" not in mapping:
                continue
            active = mapping
            data_rows = candidate_rows
        else:
            continue

        for cells in data_rows:
            if SUMMARY_ROW.search(" ".join(cells)):
                continue
            item = _build_item(cells, mapping)
            if item is not None:
                items.append(item)

    return items
