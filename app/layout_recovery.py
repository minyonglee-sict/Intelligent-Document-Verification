"""표 밖으로 떨어진 품목을 Docling 좌표로 되살린다.

Docling이 표를 놓치면 그 행은 사라지는 게 아니라 문단으로 흩어진다. 그것도 행
단위가 아니라 조각 단위로 -- invoice-7-0.pdf 는 21행짜리 표에서 6행만 표로
복원되고, 7~21행이 '품목명', '품번', '5 pcs.', '€ 750' 같은 낱개 조각이 되어
본문에 널렸다. 마크다운만 보면 순서에 기대 짜맞추는 수밖에 없는데, 그렇게 엮으면
문서에 없는 값을 지어내게 된다.

원시 Docling JSON에는 조각마다 원래 페이지 좌표(prov.bbox)가 남아 있다. 같은
줄에 있던 것은 좌표가 말해 주므로 추측할 필요가 없다.

  1. '7.' 처럼 품목 번호 하나로 된 조각을 행의 닻으로 삼는다
  2. 닻에서 다음 닻 직전까지의 세로 구간에 있는 조각을 그 행의 것으로 모은다
  3. 모은 글자에서 개수 표기(5 pcs.)와 금액 표기(€ 750)를 뽑는다
  4. 수량 x 단가 = 금액 이 성립하는 배정만 받아들인다

4번이 이 모듈의 안전장치다. 좌표로 모으고 산술로 확인하므로, 확인되지 않은 행은
그냥 버린다. 덜 살리는 것이 없는 값을 지어내는 것보다 낫다 -- 빠진 행은 검증
규칙이 따로 잡아 검수자에게 알린다.

표가 멀쩡히 읽힌 문서는 이 코드를 타지 않는다. 빠진 번호가 있을 때만 돈다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from .schemas import LineItem
from .table_parser import _CURRENCY, _UNIT

# 행의 닻. '7.' 처럼 번호로 시작하는 조각.
_ITEM_NUMBER = re.compile(r"^\s*(\d{1,3})\.(?=\s|$)")

# '€ 750' / '750 EUR' 둘 다 받는다.
_MONEY = re.compile(
    rf"(?i)(?:(?:{_CURRENCY})\s*(?P<pre>\d[\d.,]*)|(?P<post>\d[\d.,]*)\s*(?:{_CURRENCY}))"
)

# '5 pcs.' / '10 units'
_COUNT = re.compile(rf"(?i)\b(?P<n>\d+(?:[.,]\d+)?)\s*(?:{_UNIT})")

_TOLERANCE = 0.02


def _number(raw: str) -> Optional[float]:
    from .validator import to_number  # 순환 임포트를 피해 지연 임포트

    return to_number(raw)


def _fragments(docling_json: Any) -> list[dict[str, Any]]:
    """(페이지, 위, 왼쪽, 글자) 조각 목록. 표 안의 셀은 texts 에 들어오지 않는다.

    dict 와 문자열을 모두 받는다. extractor 는 doc.export_to_dict() 결과를 그대로
    들고 다니고(dict), DB 는 json.dumps 해서 넣으므로 꺼내 오면 문자열이다. 한쪽만
    받으면 파이프라인과 재처리 중 한쪽이 조용히 아무것도 못 찾는다 -- 실제로
    dict 를 json.loads 에 넣어 TypeError 가 나는 것을 예외로 삼켜, 복원이 통째로
    건너뛰어졌다.
    """
    if isinstance(docling_json, dict):
        document = docling_json
    elif isinstance(docling_json, (str, bytes, bytearray)):
        try:
            document = json.loads(docling_json)
        except ValueError:
            return []
    else:
        return []
    if not isinstance(document, dict):
        return []

    out = []
    for text in document.get("texts") or []:
        body = (text.get("text") or "").strip()
        if not body:
            continue
        for prov in text.get("prov") or []:
            box = prov.get("bbox") or {}
            if "t" not in box or "l" not in box:
                continue
            out.append(
                {
                    "page": prov.get("page_no", 0),
                    "top": float(box["t"]),
                    "left": float(box["l"]),
                    "text": body,
                }
            )
            break  # 조각 하나는 한 자리에만 둔다
    return out


def _row_text(fragments: Iterable[dict[str, Any]]) -> str:
    """읽는 순서(위에서 아래, 왼쪽에서 오른쪽)로 이어 붙인다."""
    ordered = sorted(fragments, key=lambda f: (-f["top"], f["left"]))
    return " ".join(f["text"] for f in ordered)


def _build(number: int, text: str) -> Optional[LineItem]:
    """한 행의 글자에서 품목을 만든다. 산술로 확인되지 않으면 만들지 않는다."""
    counts = [_number(m.group("n")) for m in _COUNT.finditer(text)]
    monies = [_number(m.group("pre") or m.group("post")) for m in _MONEY.finditer(text)]
    counts = [c for c in counts if c is not None]
    monies = [m for m in monies if m is not None]

    # 금액 두 개(단가·합계)와 개수 하나가 있어야 검산이 성립한다. 그 밖의 모양은
    # 무엇이 무엇인지 확인할 길이 없으므로 손대지 않는다.
    if len(monies) != 2 or len(counts) != 1:
        return None

    quantity = counts[0]
    first, second = monies
    if abs(quantity * second - first) <= _TOLERANCE:
        unit_price, amount = second, first
    elif abs(quantity * first - second) <= _TOLERANCE:
        unit_price, amount = first, second
    else:
        return None  # 어느 쪽으로도 맞지 않는다. 지어내지 않는다.

    # 남은 글자가 품목명이다. 번호·개수·금액 표기를 걷어낸다.
    rest = _ITEM_NUMBER.sub("", text, count=1)
    rest = _MONEY.sub(" ", rest)
    rest = _COUNT.sub(" ", rest)
    description = re.sub(r"\s+", " ", rest).strip(" .,-|")
    if not description:
        return None

    return LineItem(
        position=number,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
    )


def recover_missing_items(
    items: list[LineItem], docling_json: Any
) -> tuple[list[LineItem], int]:
    """표에서 빠진 품목을 되살린다. (품목 목록, 되살린 수) 를 돌려준다.

    이미 읽어낸 품목은 건드리지 않는다. 번호가 비어 있는 자리만 채운다.
    """
    if not docling_json or not items:
        return items, 0

    known = {i.position for i in items if i.position is not None}
    if not known:
        return items, 0

    fragments = _fragments(docling_json)
    if not fragments:
        return items, 0

    # 페이지별로 닻을 찾는다. 닻은 위에서 아래로 읽는다(top 내림차순).
    by_page: dict[int, list[dict[str, Any]]] = {}
    for fragment in fragments:
        by_page.setdefault(fragment["page"], []).append(fragment)

    recovered: list[LineItem] = []
    for page, page_fragments in by_page.items():
        page_fragments.sort(key=lambda f: (-f["top"], f["left"]))
        anchors = [
            (index, fragment)
            for index, fragment in enumerate(page_fragments)
            if _ITEM_NUMBER.match(fragment["text"])
        ]
        for order, (index, anchor) in enumerate(anchors):
            number = int(_ITEM_NUMBER.match(anchor["text"]).group(1))
            if number in known:
                continue

            # 다음 닻 직전까지가 이 행의 구간이다. 마지막 닻은 줄 간격만큼만 본다.
            if order + 1 < len(anchors):
                limit = anchors[order + 1][1]["top"]
            else:
                gaps = [
                    anchors[i][1]["top"] - anchors[i + 1][1]["top"]
                    for i in range(len(anchors) - 1)
                ]
                span = min(gaps) if gaps else 40.0
                limit = anchor["top"] - span

            row = [
                f
                for f in page_fragments
                if limit < f["top"] <= anchor["top"] + 0.5
            ]
            item = _build(number, _row_text(row))
            if item is not None:
                recovered.append(item)
                known.add(number)

    if not recovered:
        return items, 0

    merged = list(items) + recovered
    # 번호가 있는 것은 번호대로 세우고, 없는 것은 뒤에 원래 순서로 붙인다.
    numbered = sorted(
        (i for i in merged if i.position is not None), key=lambda i: i.position
    )
    unnumbered = [i for i in merged if i.position is None]
    return numbered + unnumbered, len(recovered)
