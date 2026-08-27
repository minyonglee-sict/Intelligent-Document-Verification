"""표 밖으로 떨어진 품목을 Docling 좌표로 되살린다.

Docling이 표를 놓치면 그 행은 사라지는 게 아니라 문단으로 흩어진다. 흩어지는
단위가 문서마다 다르다.

  낱말 단위 (invoice-7-0.pdf) 21행짜리 표에서 6행만 표로 복원되고, 7~21행이
    '품목명', '품번', '5 pcs.', '€ 750' 같은 낱개 조각이 되어 본문에 널렸다.
    -> _build_marked() : '7.' 처럼 품목 번호 하나로 된 조각을 행의 닻으로 삼아,
       닻에서 다음 닻 직전까지의 세로 구간에 있는 조각을 모으고, 통화 기호·단위
       표기(€ 750 / 5 pcs.)로 무엇이 값인지 가린다.

  행 단위 (invoice-8-0.pdf) 품목 번호 열 자체가 없는 양식. 표가 페이지를 넘어가며
    깨지자 뒷부분 7행이 '650519018-X 10 140 설명... 1400' 처럼 조각 하나에
    행 전체가 그대로 남았다. 번호 닻이 없어 위 경로는 아무것도 못 건진다.
    -> _build_bare_row() : 통화 기호·단위 없이 자리(순서)만으로 수량·단가·금액을
       가른다. 닻도 기호도 없이 순서만 믿는 값이라, 아래 4번 안전장치가 더 크게
       걸린다.

마크다운만 보면 순서에 기대 짜맞추는 수밖에 없는데, 그렇게 엮으면 문서에 없는
값을 지어내게 된다. 원시 Docling JSON에는 조각마다 원래 페이지 좌표(prov.bbox)가
남아 있어 짜맞출 필요가 없다.

  1. 조각을 행 단위로 모은다 (번호 닻 + 세로 구간, 또는 조각 하나가 통째로 한 행)
  2. 모은 글자에서 수량·단가·금액을 뽑는다
  3. 수량 x 단가 = 금액 이 성립하는 배정만 받아들인다

3번이 이 모듈의 안전장치다. 좌표로 모으고 산술로 확인하므로, 확인되지 않은 행은
그냥 버린다. 덜 살리는 것이 없는 값을 지어내는 것보다 낫다 -- 빠진 행은 검증
규칙이 따로 잡아 검수자에게 알린다.

표가 멀쩡히 읽힌 문서는 이 코드를 타지 않는다. 빠진 행이 있을 때만 돈다.

이 파일은 품목(표) 복원 말고 하나를 더 한다 -- recover_header_hints(). 머리말도
비슷하게 깨지는 문서가 있다: '라벨 칸'과 '값 칸'이 나란히 있는 2단 레이아웃을
Docling이 왼쪽 칸을 통째로 다 읽은 뒤에야 오른쪽 칸으로 넘어가서, 마크다운엔
라벨 무더기 뒤에 값 무더기가 따로 떨어져 나온다(Receipt# / Issue Date / ... /
: KCC-RC-002 / : Jan 03, 2024). 품목과 달리 머리말엔 '수량 x 단가 = 금액' 같은
보편 검산이 없어서, 값을 직접 확정하지 않는다 -- 대신 같은 줄(top 좌표) 기준으로
라벨과 값을 다시 짝지어 LLM에게 참고 자료로만 얹어준다. 최종 값은 여전히
LLM이 정하고, grounding_check 는 이 힌트가 안 섞인 원문(markdown) 그대로 돈다.
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

# 라벨과 떨어져 나온 값. ': KCC-RC-002' 처럼 콜론으로 시작하는 조각 -- 원래
# 라벨과 한 줄이었는데 Docling이 칸(열) 단위로 읽어서 떨어져 나온 흔적이다.
# 이 콜론 접두어 자체가 신호라, 다른 문서에서 우연히 나올 일이 드물다.
_ORPHAN_VALUE = re.compile(r"^:\s*(?P<value>\S.*)$")

# 통화 기호도 단위도 없이 숫자만 나열된 행. '650519018-X 10 140 설명... 1400' 처럼
# 품번 다음에 수량·단가가 곧장 오고, 맨 끝에 금액이 오는 문서가 있다(invoice-8-0.pdf).
# 자리(순서)만 보고 무엇이 수량이고 단가인지 정하므로, 산술 검산이 훨씬 더 중요한
# 안전장치다 -- _build_marked 와 달리 기호로 골라낸 숫자가 아니기 때문이다.
_BARE_ROW = re.compile(
    r"^\s*\S+\s+(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<price>\d+(?:[.,]\d+)?)\s+"
    r"(?P<desc>.+?)\s+(?P<total>\d+(?:[.,]\d+)?)\s*$"
)

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


def _build_marked(number: int, text: str) -> Optional[LineItem]:
    """번호 닻으로 모은 조각에서 품목을 만든다. 산술로 확인되지 않으면 만들지 않는다.

    '€ 750'/'5 pcs.' 처럼 통화 기호·단위가 붙은 값을 찾는다 -- 조각이 낱말 단위로
    흩어진 문서(invoice-7-0.pdf)에서 무엇이 값인지 그 기호로 가려낸다.
    """
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


def _build_bare_row(text: str) -> Optional[LineItem]:
    """번호 닻도, 통화 기호·단위도 없는 문서에서 표 밖으로 떨어진 조각 하나를 그대로
    품목으로 만든다 (invoice-8-0.pdf: 품번 다음에 수량·단가가 곧장 오고 맨 끝에
    금액이 오는 양식, 품목 번호 열 자체가 없다).

    조각 하나가 이미 행 하나 전체다 -- 표가 페이지 넘어가며 깨질 때 Docling이
    행을 낱말 단위가 아니라 문단 단위로 흘려보내기 때문이다. 그래서 주변 조각을
    모을 필요가 없고, 자리(순서)로만 수량·단가·금액을 가른다. 기호로 고른 값이
    아니므로 산술 검산이 유일한 방어선이다 -- 하나라도 안 맞으면 버린다.
    """
    m = _BARE_ROW.match(text)
    if not m:
        return None

    quantity = _number(m.group("qty"))
    unit_price = _number(m.group("price"))
    amount = _number(m.group("total"))
    if quantity is None or unit_price is None or amount is None:
        return None
    if abs(quantity * unit_price - amount) > _TOLERANCE:
        return None  # 자리만 보고 가른 값이 검산을 통과하지 못했다 -- 지어내지 않는다.

    description = re.sub(r"\s+", " ", m.group("desc")).strip(" .,-|")
    if not description:
        return None

    # 번호 열 자체가 없는 문서이므로 자리를 매기지 않는다 -- 순서는 merge 단계에서
    # '번호 없는 행'으로 뒤에 붙는다.
    return LineItem(
        position=None,
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

    문서 번호 열이 있는지에 따라 두 갈래로 시도한다 -- 하나가 안 통해도 다른
    하나는 돌아야, 번호 열 자체가 없는 문서(invoice-8-0.pdf)에서 통째로 포기하는
    일이 없다.
    """
    if not docling_json or not items:
        return items, 0

    fragments = _fragments(docling_json)
    if not fragments:
        return items, 0

    known = {i.position for i in items if i.position is not None}
    recovered: list[LineItem] = []
    consumed: set[int] = set()  # id(fragment) -- 닻 경로가 이미 쓴 조각

    # 1) 번호 닻이 있는 문서: 닻 사이 구간의 조각을 모아 만든다 (기존 경로).
    by_page: dict[int, list[dict[str, Any]]] = {}
    for fragment in fragments:
        by_page.setdefault(fragment["page"], []).append(fragment)

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
            item = _build_marked(number, _row_text(row))
            if item is not None:
                recovered.append(item)
                known.add(number)
                consumed.update(id(f) for f in row)

    # 2) 닻 경로가 못 건진 조각: 조각 하나가 이미 행 하나 전체인 경우를 본다.
    #    번호 열이 아예 없는 문서는 1번이 애초에 아무것도 못 건지므로, 여기가
    #    유일한 경로다. 값이 같은 다른 행과 겹치는지는 보지 않는다 -- 표 안의
    #    셀은 애초에 texts 에 들어오지 않으므로(_fragments 참고) 이미 읽은 행과
    #    값 삼중이 우연히 같은 것도(수량20 x 단가160 이 두 번 나오는 등) 그대로
    #    받아야 한다. 값으로 걸러내면 진짜 행을 중복으로 오인해 버리게 된다.
    for fragment in fragments:
        if id(fragment) in consumed:
            continue
        item = _build_bare_row(fragment["text"])
        if item is not None:
            recovered.append(item)

    if not recovered:
        return items, 0

    merged = list(items) + recovered
    # 번호가 있는 것은 번호대로 세우고, 없는 것은 뒤에 원래 순서로 붙인다.
    numbered = sorted(
        (i for i in merged if i.position is not None), key=lambda i: i.position
    )
    unnumbered = [i for i in merged if i.position is None]
    return numbered + unnumbered, len(recovered)


_ROW_TOLERANCE = 1.0  # 포인트 단위. 이 안이면 "같은 줄"로 본다.


def recover_header_hints(docling_json: Any) -> str:
    """라벨 칸과 값 칸이 나란히 있다가 Docling이 세로로 통째로 나눠 읽어버린
    머리말을, 같은 줄(top 좌표) 기준으로 다시 짝지어 LLM에게 줄 힌트로 만든다.

    최종 값을 여기서 확정하지 않는다 -- 품목과 달리 머리말엔 보편 검산이 없어서,
    잘못 짝지어도 걸러낼 방법이 없다. 그래서 이 결과는 "참고 자료"로만 LLM에게
    얹어주고, 실제 채택 여부는 여전히 LLM 판단 + grounding_check 몫으로 남긴다.
    grounding_check 는 이 힌트가 안 섞인 원문 그대로 돈다(pipeline.py 참고).

    ':'로 시작하는 조각(_ORPHAN_VALUE)이 없으면 -- 즉 이 문제가 없는 보통
    문서라면 -- 빈 문자열을 돌려주고 아무 흔적도 안 남긴다.
    """
    fragments = _fragments(docling_json)
    if not fragments:
        return ""

    by_page: dict[int, list[dict[str, Any]]] = {}
    for fragment in fragments:
        by_page.setdefault(fragment["page"], []).append(fragment)

    pairs: list[tuple[str, str]] = []
    for page_fragments in by_page.values():
        orphans = [f for f in page_fragments if _ORPHAN_VALUE.match(f["text"])]
        if not orphans:
            continue
        labels = [f for f in page_fragments if not _ORPHAN_VALUE.match(f["text"])]

        for value_fragment in orphans:
            # 같은 줄(top 차이가 아주 작음) + 왼쪽에 있는 것들 중, 값에 제일
            # 가까운(가장 오른쪽) 조각을 그 라벨로 본다.
            same_row = [
                f
                for f in labels
                if abs(f["top"] - value_fragment["top"]) < _ROW_TOLERANCE
                and f["left"] < value_fragment["left"]
            ]
            if not same_row:
                continue
            label = max(same_row, key=lambda f: f["left"])
            value = _ORPHAN_VALUE.match(value_fragment["text"]).group("value").strip()
            if label["text"].strip() and value:
                pairs.append((label["text"].strip(), value))

    if not pairs:
        return ""

    lines = "\n".join(f"{label}: {value}" for label, value in pairs)
    return (
        "[참고: 아래는 원문에서 라벨과 값이 서로 떨어져 나온 자리를 같은 줄"
        " 좌표 기준으로 다시 짝지은 것입니다. 실제 원문이 아니라 참고용 힌트이니,"
        " 값 추출에만 참고하고 그대로 베끼지는 마세요.]\n"
        f"{lines}\n"
    )
