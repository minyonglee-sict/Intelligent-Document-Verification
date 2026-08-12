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

# 한 칸이 통째로 이 라벨이면 그 행은 합계 구역이다. 'TOTAL' 처럼 짧은 낱말은
# 부분 일치로 걸면 'Total Station' 같은 진짜 품목까지 지워지므로 칸 전체를 본다.
SUMMARY_LABELS = {
    "total", "subtotal", "sub total", "sub-total", "tax", "vat", "sales tax",
    "shipping", "handling", "discount", "balance", "amount due", "total due",
    "grand total", "합계", "소계", "총계", "부가세", "세액", "공급가액", "할인",
}


def _is_summary_row(cells: list[str]) -> bool:
    if SUMMARY_ROW.search(" ".join(cells)):
        return True
    return any(c.strip().lower().rstrip(":") in SUMMARY_LABELS for c in cells)

# 수량 열이 따로 없을 때 품목명 안에 섞여 있는 수량 표기.
#   'Exterior Protection (10)'  /  'Dwarf Senna Qty. 2'
EMBEDDED_COUNT = [
    re.compile(r"\((\d+(?:\.\d+)?)\)\s*$"),
    re.compile(r"(?i)\bqty\.?\s*[:x]?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(?i)\bx\s*(\d+(?:\.\d+)?)\s*$"),
    # 뒤에 단가까지 밀려 들어오면 괄호가 끝에 오지 않는다
    # ('14 - Conveying Systems (29) 86.2')
    re.compile(r"\((\d+(?:\.\d+)?)\)"),
]

# 열 이름 단서. 위에 놓인 것부터 배정하고, 이미 잡힌 열은 건너뛴다.
# 순서가 중요하다.
#   - price 계열이 description 보다 앞: 'Item Price' 가 품목명으로 잡히면 안 된다.
#   - description 이 quantity 보다 앞: 'Item # Ordered Service' 의 'Ordered' 가
#     수량으로 먼저 잡혀 품목명 열을 통째로 잃는 일이 있었다.
COLUMN_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("unit_price", ("unit price", "item price", "unit cost", "rate", "price", "단가")),
    ("tax", ("tax", "vat", "세액", "부가세")),
    ("amount", ("line total", "total", "amount", "금액", "합계")),
    ("description", ("description", "service", "product", "item", "품목", "내역", "적요")),
    ("quantity", ("quantity", "qty", "ordered", "units", "수량")),
]

# 행 번호 열의 이름. 'Item #' 는 'item' 을 품고 있어 품목명 열로 잡히기 쉽지만
# 실제 내용은 행 번호다. Docling이 'Item # Ordered Service' 한 열을 둘로 쪼개 놓으면
# 이 열이 품목명 자리를 차지해 품목명을 통째로 잃는다 (invoice-2-4.pdf).
ROW_NUMBER_NAMES = {
    "#", "no", "no.", "num", "item", "item #", "item#", "item no", "item no.",
    "번호", "순번", "품번",
}

NUMERIC = re.compile(r"^\s*[-+]?[\d.,\s]+\s*$")

# 품목명 끝에 붙어 들어온 숫자. 단가 칸이 비었을 때 후보로 쓴다.
TRAILING_NUMBER = re.compile(r"(\d[\d,]*\.?\d*)\s*$")

# 품목 번호로 볼 수 있는 칸. 소수점·쉼표가 있으면 금액이지 번호가 아니다.
INTEGER = re.compile(r"^\s*(\d{1,4})\s*$")


def _to_int(cell: str) -> Optional[int]:
    match = INTEGER.match(cell or "")
    return int(match.group(1)) if match else None


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
            low = name.strip().lower()
            # 'Item #' 는 번호 열이지 품목명 열이 아니다. 여기서 걸러내지 않으면
            # 'item' 단서에 걸려 진짜 품목명 열을 놓친다.
            if field == "description" and low.rstrip(":") in ROW_NUMBER_NAMES:
                continue
            if any(h in low for h in hints):
                mapping[field] = idx
                taken.add(idx)
                break
    return mapping


def _merge_spilled_text(rows: list[list[str]], mapping: dict[str, int]) -> list[list[str]]:
    """여러 칸에 걸쳐 끊긴 품목명을 품목명 칸으로 합친다.

    Docling이 'Item # Ordered Service' 한 열을 'Item #' 과 'Ordered Service' 로
    쪼개면 품목명이 두 칸에 나뉜다 ('1 8-200 - Wood and Plastic' + 'Doors (9)').
    매핑에 쓰이지 않은 글자 열은 품목명의 일부로 본다. 숫자 열은 건드리지 않는다 --
    품목 번호나 값이 들어 있다.
    """
    target = mapping.get("description")
    if target is None:
        return rows

    numeric, text_len = _column_shapes(rows)
    spill = [
        i
        for i in range(len(numeric))
        if i != target and i not in mapping.values() and not numeric[i] and text_len[i] > 0
    ]
    if not spill:
        return rows

    order = sorted(spill + [target])
    merged: list[list[str]] = []
    for cells in rows:
        if _is_summary_row(cells):
            merged.append(list(cells))  # 합계 행은 라벨 위치가 판정에 쓰이므로 그대로
            continue
        row = list(cells)
        joined = " ".join(
            row[i].strip() for i in order if i < len(row) and row[i].strip()
        )
        for i in spill:
            if i < len(row):
                row[i] = ""
        if target < len(row):
            row[target] = joined
        merged.append(row)
    return merged


def _looks_like_data(cells: list[str], mapping: dict[str, int]) -> bool:
    """이 행이 헤더가 아니라 데이터인지 본다 (페이지 넘김 조각의 첫 행 판별)."""
    idx = mapping.get("amount")
    if idx is None or idx >= len(cells):
        return False
    return bool(NUMERIC.match(cells[idx])) and cells[idx].strip() != ""


def _is_number(cell: str) -> bool:
    return bool(cell.strip()) and bool(NUMERIC.match(cell))


def _column_shapes(rows: list[list[str]]) -> tuple[list[bool], list[float]]:
    """열마다 (숫자 열인가, 글자 길이 평균)을 낸다.

    합계 구역 행과 빈 칸은 빼고 센다. 페이지가 넘어간 조각은 헤더 자리에 엉뚱한
    행이 들어오고 끝에 SUBTOTAL/SALES TAX 행이 붙는데, 그것까지 세면 멀쩡한 숫자
    열의 비율이 문턱 아래로 떨어져 표 전체를 놓친다 (invoice-0-4 2페이지에서
    13행을 통째로 잃었다).
    """
    data = [r for r in rows if not _is_summary_row(r)]
    width = max((len(r) for r in data), default=0)

    numeric, text_len = [], []
    for idx in range(width):
        cells = [r[idx].strip() for r in data if idx < len(r) and r[idx].strip()]
        if not cells:
            numeric.append(False)
            text_len.append(0.0)
            continue
        numeric.append(sum(_is_number(c) for c in cells) / len(cells) >= 0.8)
        non_numeric = [c for c in cells if not _is_number(c)]
        text_len.append(sum(len(c) for c in non_numeric) / len(cells))
    return numeric, text_len


def infer_mapping(rows: list[list[str]]) -> dict[str, int]:
    """헤더가 없는 조각에서 데이터 모양만 보고 열을 추정한다.

    Docling은 페이지가 넘어갈 때 같은 표를 열 개수까지 다르게 쪼개기도 한다
    (품목번호가 품목명에 붙은 3열 -> 따로 떨어진 4열). 그때는 헤더 문자열이
    없으므로 값의 생김새로 판단한다: 오른쪽 끝 숫자 두 열이 단가와 금액,
    가장 긴 글자 열이 품목명.
    """
    if len(rows) < 2:
        return {}
    numeric, text_len = _column_shapes(rows)
    width = len(numeric)
    if width < 3:
        return {}

    numeric_cols = [i for i, n in enumerate(numeric) if n]
    if len(numeric_cols) < 2:
        return {}
    unit_price, amount = numeric_cols[-2], numeric_cols[-1]

    remaining = [i for i in range(width) if i not in (unit_price, amount)]
    if not remaining:
        return {}
    description = max(remaining, key=lambda i: text_len[i])
    if text_len[description] < 3:  # 글자다운 열이 없으면 품목 표가 아니다
        return {}

    mapping = {"description": description, "unit_price": unit_price, "amount": amount}

    # 남은 숫자 열이 수량일 수 있다. 검산이 성립할 때만 받아들인다.
    for idx in numeric_cols:
        if idx in (unit_price, amount):
            continue
        candidate = {**mapping, "quantity": idx}
        score, checked = _score_mapping(rows, candidate)
        if checked and score >= 0.8:
            return candidate
    return mapping


def _single_amount_mapping(rows: list[list[str]]) -> dict[str, int]:
    """숫자 열이 하나뿐인 품목 표. 그 열을 금액으로, 가장 긴 글자 열을 품목명으로 본다.

    'Make/Model | Product Code | Unit Price' 처럼 합계 열 없이 값을 한 번만 적는
    양식이 있다. 한 줄에 하나씩 사는 문서라 그 값이 곧 그 줄의 금액이다.
    문서가 스스로 증명한다 -- invoice-3-0.pdf 는 단가 합계 187,700 이 공급가액과
    정확히 같다.
    """
    numeric, text_len = _column_shapes(rows)
    numeric_cols = [i for i, n in enumerate(numeric) if n]
    if len(numeric_cols) != 1:
        return {}

    text_cols = [i for i in range(len(numeric)) if not numeric[i] and text_len[i] >= 3]
    if not text_cols:
        return {}
    return {
        "description": max(text_cols, key=lambda i: text_len[i]),
        "amount": numeric_cols[0],
    }


def _cell(cells: list[str], mapping: dict[str, int], field: str) -> Optional[str]:
    idx = mapping.get(field)
    if idx is None or idx >= len(cells):
        return None
    return cells[idx]


def _embedded_quantities(description: str) -> list[float]:
    """품목명 안에 섞인 수량 후보 ('(10)', 'Qty. 2', 'x 3')."""
    out = []
    for pattern in EMBEDDED_COUNT:
        match = pattern.search(description)
        if match:
            out.append(float(match.group(1)))
    return out


def _build_item(cells: list[str], mapping: dict[str, int]) -> Optional[LineItem]:
    from .validator import to_number, to_text  # 순환 임포트를 피해 지연 임포트

    description = to_text(_cell(cells, mapping, "description")) or ""
    amount = to_number(_cell(cells, mapping, "amount"))
    values = {
        "unit_price": to_number(_cell(cells, mapping, "unit_price")),
        "quantity": to_number(_cell(cells, mapping, "quantity")),
        "tax": to_number(_cell(cells, mapping, "tax")),
    }
    position = None

    # 품목명 칸에 번호만 들어오고 정작 품목명은 옆 칸으로 밀린 행이 있다
    # ('| 26 | 3-230 - Anchor Bolts (21) 41.41 | 869.61 |'). 품목명이 숫자뿐이면
    # 글자가 있는 칸을 품목명으로 삼고, 그 숫자는 품목 번호로 남긴다.
    if description and INTEGER.match(description):
        for field in values:
            idx = mapping.get(field)
            cell = cells[idx] if idx is not None and idx < len(cells) else ""
            if any(ch.isalpha() for ch in cell):
                position = _to_int(description)
                description, values[field] = cell.strip(), None
                break

    unit_price, quantity, tax = (
        values["unit_price"], values["quantity"], values["tax"]
    )

    if not description and amount is None:
        return None

    # 숫자가 하나도 없는 행은 품목이 아니라 안내문·주석이다.
    # ('Please make the payment by the due date.' 가 품목으로 들어온 적이 있다)
    if all(v is None for v in (amount, unit_price, quantity, tax)) and not any(
        _is_number(c) for c in cells
    ):
        return None

    embedded = _embedded_quantities(description)

    # 단가 칸이 비었는데 품목명 꼬리에 숫자가 붙어 있는 경우. Docling이 열을 밀어
    # 놓으면 단가가 품목명 칸으로 넘어온다 ('2-782 - Brick Pavers (7) 41.69').
    # 추측이 되지 않도록 '수량 x 단가 = 금액' 이 성립할 때만 받아들이고,
    # 받아들였을 때만 품목명에서 그 숫자를 떼어낸다.
    if unit_price is None and amount is not None and embedded:
        tail = TRAILING_NUMBER.search(description)
        candidate = to_number(tail.group(1)) if tail else None
        if candidate:
            for count in embedded:
                if abs(count * candidate - amount) <= 0.02:
                    unit_price, quantity = candidate, count
                    description = description[: tail.start()].strip()
                    break

    # Docling은 같은 표 안에서도 행마다 칸을 다르게 밀어 놓는 일이 있다. 금액 칸이
    # 비었으면 매핑 밖의 숫자 칸을 살펴보되, 검산이 성립할 때만 받아들인다.
    # (invoice-2-1 마지막 행: [품목명, 99.25, '', 2481.25] -> 25 x 99.25 = 2481.25)
    if amount is None and unit_price and embedded:
        used = set(mapping.values())
        for idx, cell in enumerate(cells):
            if idx in used:
                continue
            candidate = to_number(cell)
            if candidate is None:
                continue
            if any(abs(q * unit_price - candidate) <= 0.02 for q in embedded):
                amount = candidate
                break

    # 수량 열이 없는 양식은 품목명 안에 수량이 섞여 있다.
    # 추측이 되지 않도록, 수량 x 단가 = 금액 이 성립할 때만 받아들인다.
    if quantity is None and unit_price and amount is not None:
        for candidate in embedded:
            if abs(candidate * unit_price - amount) <= 0.02:
                quantity = candidate
                break

    return LineItem(
        position=position,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        tax=tax,
    )


def _number_column(rows: list[list[str]], mapping: dict[str, int]) -> Optional[int]:
    """품목 번호가 따로 놓인 열을 찾는다.

    Docling이 표를 넓게 복원하면 'Item #' 이 제 열을 갖는다
    ('| 55 | 10-340 - Manufactured Exterior Specialties (11) | 46.94 | 516.34 |').
    매핑되지 않은 열 중 값이 모두 정수이고 1씩 오름차순인 것을 번호로 본다.
    수량·금액이 우연히 그 모양이 되는 일은 없다.
    """
    used = set(mapping.values())
    width = max((len(r) for r in rows), default=0)
    for idx in range(width):
        if idx in used:
            continue
        values = [
            _to_int(r[idx])
            for r in rows
            if idx < len(r) and not _is_summary_row(r)
        ]
        if len(values) < 2 or any(v is None for v in values):
            continue
        if all(b - a == 1 for a, b in zip(values, values[1:])):
            return idx
    return None


LEADING_NUMBER = re.compile(r"^(\d{1,4})\s+(?=\S)")


def _strip_row_numbers(items: list[LineItem]) -> None:
    """품목명 앞에 붙은 품목 번호를 떼어내 position 으로 옮긴다.

    Docling이 표를 좁게 복원하면 'Item #' 열과 품목명 열을 한 칸에 합쳐
    '18 1-013 - Project Coordinator (26)' 처럼 만든다. 번호는 품목명이 아니므로
    떼어내되, 원문과 대조할 때 필요하니 버리지 않고 position 에 남긴다.

    다만 '1-013' 이나 '10-700' 처럼 숫자로 시작하는 진짜 품목코드를 잘라내면 안
    되므로, 그 숫자가 번호라는 근거가 있을 때만 지운다.
      - 표의 대부분(60% 이상) 행이 앞 숫자를 갖고 그 값이 오름차순이거나
      - 그 숫자가 표 안에서의 행 순번과 일치할 때
    """
    leading: list[Optional[int]] = []
    for item in items:
        match = LEADING_NUMBER.match(item.description)
        leading.append(int(match.group(1)) if match else None)

    present = [n for n in leading if n is not None]
    ascending = len(present) >= max(2, int(len(items) * 0.6)) and all(
        a < b for a, b in zip(present, present[1:])
    )

    for idx, (item, number) in enumerate(zip(items, leading), start=1):
        if number is None:
            continue
        if not ascending and number != idx:
            continue  # 번호라는 근거가 없다. 품목코드일 수 있으므로 둔다
        stripped = LEADING_NUMBER.sub("", item.description, count=1).strip()
        if stripped:
            item.description = stripped
            item.position = number


def _score_mapping(rows: list[list[str]], mapping: dict[str, int]) -> tuple[float, int]:
    """이 매핑으로 읽었을 때 '수량 x 단가 = 금액' 이 맞는 행의 비율과 검산 가능 행 수.

    헤더 이름을 믿을 수 없을 때 쓰는 심판이다. 송장의 산술은 문서가 스스로 증명하는
    사실이라, 열 배정이 옳으면 대부분의 행에서 성립하고 틀리면 거의 성립하지 않는다.
    """
    checked = ok = 0
    for cells in rows:
        if _is_summary_row(cells):
            continue
        item = _build_item(cells, mapping)
        if item is None or None in (item.quantity, item.unit_price, item.amount):
            continue
        checked += 1
        if abs(item.quantity * item.unit_price - item.amount) <= 0.02:
            ok += 1
    return (ok / checked if checked else 0.0), checked


def _candidate_mappings(rows: list[list[str]]) -> list[dict[str, int]]:
    """숫자 열 조합을 (단가, 금액)으로 놓아 본 후보들.

    Docling이 표를 어긋나게 복원하면 헤더 이름이 실제 값과 한 칸씩 밀린다. 그때는
    이름을 버리고 값의 산술로 판정해야 한다.
    """
    if not rows:
        return []
    numeric, text_len = _column_shapes(rows)
    width = len(numeric)

    numeric_cols = [i for i, n in enumerate(numeric) if n]
    text_cols = [i for i in range(width) if not numeric[i] and text_len[i] >= 3]
    if len(numeric_cols) < 2 or not text_cols:
        return []
    description = max(text_cols, key=lambda i: text_len[i])

    candidates = []
    for unit_price in numeric_cols:
        for amount in numeric_cols:
            if unit_price == amount:
                continue
            base = {"description": description, "unit_price": unit_price, "amount": amount}
            candidates.append(base)
            for quantity in numeric_cols:
                if quantity not in (unit_price, amount):
                    candidates.append({**base, "quantity": quantity})
    return candidates


def _best_mapping(
    header_mapping: dict[str, int], rows: list[list[str]]
) -> dict[str, int]:
    """헤더로 만든 매핑을 검산으로 확인하고, 틀렸으면 더 맞는 것으로 바꾼다."""
    score, checked = _score_mapping(rows, header_mapping)
    if checked and score >= 0.5:
        return header_mapping  # 대체로 맞으면 헤더를 믿는다

    # 검산할 수 없어도(checked == 0) 후보를 찾아본다. '확인할 수 없다'와 '맞다'는
    # 다르다 -- 헤더와 데이터가 통째로 어긋난 표(invoice-2-1, 2-3)에서는 헤더대로
    # 읽으면 수량 자리가 비어 검산 자체가 성립하지 않는다.
    # 다만 우연히 한 행이 맞아떨어진 후보에 넘어가지 않도록 근거 2행 이상을 요구한다.
    best, best_score = header_mapping, score
    for candidate in _candidate_mappings(rows):
        candidate_score, candidate_checked = _score_mapping(rows, candidate)
        if candidate_checked >= 2 and candidate_score > best_score:
            best, best_score = candidate, candidate_score
    return best


def parse_line_items(markdown: str) -> list[LineItem]:
    """마크다운에서 품목을 읽어낸다. 품목 표를 찾지 못하면 빈 목록."""
    items: list[LineItem] = []
    active: Optional[dict[str, int]] = None

    for header, rows in _iter_tables(markdown):
        mapping = map_columns(header)

        if "description" in mapping and "amount" in mapping:
            # 헤더가 읽히는 표. 다만 Docling이 헤더와 데이터를 어긋나게 복원하는
            # 경우가 있어(invoice-2-1), 이름만 믿지 않고 검산으로 확인한다.
            mapping = _best_mapping(mapping, rows)
            # 열 배정이 확정된 뒤에 합친다. 잘못된 매핑으로 합치면 진짜 품목명 열을
            # 엉뚱한 칸에 밀어 넣어 되돌릴 수 없다.
            active = mapping
            data_rows = _merge_spilled_text(rows, mapping)
        elif "unit_price" in mapping and "amount" not in mapping and len(rows) >= 2:
            # 값을 한 번만 적는 표(합계 열 없음). 헤더가 값 열을 밝히고 있으므로
            # 주소·날짜 표를 품목으로 오인할 위험이 낮다.
            mapping = _single_amount_mapping(rows)
            if "description" not in mapping:
                continue
            active = mapping
            data_rows = _merge_spilled_text(rows, mapping)
        elif active is not None and _looks_like_data(header, active) and len(header) == max(active.values()) + 1:
            # 앞 표와 같은 모양으로 이어지는 조각. 헤더 자리의 행도 데이터다.
            mapping = active
            data_rows = [header, *rows]
        elif active is not None:
            # 헤더 자리에 데이터도 아닌 엉뚱한 행이 온 조각.
            # 품목 표를 이미 한 번 만난 뒤에만 시도해, 주소·날짜 표를 잘못
            # 품목으로 읽는 일을 막는다.
            candidate_rows = [header, *rows]

            # 먼저 앞 표의 열 배치를 그대로 대 본다. 산술이 맞으면 같은 표가
            # 이어지는 것이다 (invoice-0-4: 헤더 자리에 '| | BPXPN-00052 | | |'
            # 가 와서 데이터로도 헤더로도 읽히지 않았다). 이렇게 이어붙여야
            # infer_mapping 이 못 잡는 수량 열까지 앞 표에서 물려받는다.
            score, checked = _score_mapping(candidate_rows, active)
            mapping = active if checked and score >= 0.5 else infer_mapping(candidate_rows)
            if "description" not in mapping:
                continue
            active = mapping
            data_rows = candidate_rows
        else:
            continue

        # 표 조각 단위로 모은다. 앞 숫자가 품목 번호인지 판단하려면 그 조각의
        # 행들을 함께 봐야 하기 때문이다.
        number_idx = _number_column(data_rows, mapping)
        fragment: list[LineItem] = []
        for cells in data_rows:
            if _is_summary_row(cells):
                continue
            item = _build_item(cells, mapping)
            if item is None:
                continue
            if number_idx is not None and number_idx < len(cells):
                number = _to_int(cells[number_idx])
                if number is not None:  # _build_item 이 찾아둔 번호를 지우지 않는다
                    item.position = number
            fragment.append(item)

        _strip_row_numbers(fragment)  # 번호가 품목명에 붙어 있는 조각을 처리한다
        items += fragment

    _keep_document_numbers(items)
    return items


def _keep_document_numbers(items: list[LineItem]) -> None:
    """문서의 품목 번호를 그대로 쓸지 정한다.

    전부 번호가 있고 오름차순일 때만 남긴다. 한 행이라도 번호를 못 읽었거나
    순서가 어긋나면 번호를 잘못 짚은 것이므로 전부 버리고 저장 순서에 맡긴다.
    반쯤 맞는 번호는 원문 대조에 쓸 수 없어 없느니만 못하다.
    """
    numbers = [item.position for item in items]
    trustworthy = all(n is not None for n in numbers) and all(
        a < b for a, b in zip(numbers, numbers[1:])
    )
    if not trustworthy:
        for item in items:
            item.position = None
