"""IBM Docling을 이용한 PDF -> 텍스트(Markdown) / JSON 추출."""

from __future__ import annotations

import html
import os
import re
from functools import lru_cache
from pathlib import Path

from .schemas import ExtractionResult

# 텍스트 레이어가 없는 스캔 PDF를 다룰 때만 켠다(느림).
USE_OCR = os.getenv("DOCLING_OCR", "0") == "1"

# torch.compile은 CPU 추론에서 이득이 거의 없고, Windows 비UTF-8 로캘(cp949 등)에서는
# inductor가 템플릿 파일을 읽다가 UnicodeDecodeError로 모델 로딩 자체를 깨뜨린다.
USE_TORCH_COMPILE = os.getenv("DOCLING_COMPILE", "0") == "1"


@lru_cache(maxsize=1)
def get_converter():
    """DocumentConverter는 모델 로딩 비용이 크므로 프로세스당 한 번만 만든다."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.settings import settings as docling_settings
    from docling.document_converter import DocumentConverter, PdfFormatOption

    docling_settings.inference.compile_torch_models = USE_TORCH_COMPILE

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = USE_OCR
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.do_cell_matching = True

    # 이미지는 텍스트 레이어가 없으므로 OCR이 반드시 필요하다.
    image_options = PdfPipelineOptions()
    image_options.do_ocr = True
    image_options.do_table_structure = True
    image_options.table_structure_options.do_cell_matching = True

    # format_options 는 PDF/이미지 파이프라인만 손본다. Word·PowerPoint·Excel 등은
    # Docling 기본 처리로 충분하고, allowed_formats 를 지정하지 않으므로 모두 열려 있다.
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: PdfFormatOption(pipeline_options=image_options),
        }
    )


_WORD = re.compile(r"[0-9a-z가-힣]+")


def _flatten(text: str) -> str:
    """표 문법과 줄바꿈을 지워 '글자만' 남긴다.

    PDF 텍스트 레이어는 표 한 줄을 셀 단위로 끊어 내놓는 반면 Markdown 은
    '| a | b |' 로 묶는다. 그대로 대조하면 멀쩡히 옮겨진 표까지 '빠진 줄'로
    잡히므로, 양쪽에서 구분 문자를 걷어내고 비교한다.
    """
    return re.sub(r"\s+", " ", re.sub(r"[|#*`_]+", " ", text)).strip().lower()


def _dropped_text(path: Path, markdown: str) -> list[str]:
    """Docling이 흘린 글자를 PDF 텍스트 레이어에서 주워 온다.

    레이아웃 모델은 세로로 회전된 글자를 본문으로 보지 않고 버린다. 실제로 왼쪽
    가장자리에 세로로 적힌 'Invoice 4235' 가 통째로 사라져 송장 번호를 뽑을 수
    없었다(invoice-3-0.pdf). 원문에 없는 값은 LLM이 읽지도 못하고 근거 대조도
    통과하지 못하므로, 사람이 손으로 채우는 수밖에 없었다.

    Markdown 에 이미 있는 줄은 뺀다. 남는 것만 부록으로 붙여, 표 파싱은 건드리지
    않으면서 머리말 추출과 근거 대조에는 보이게 한다.
    """
    if path.suffix.lower() != ".pdf":
        return []
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        raw = "\n".join(
            pdf[i].get_textpage().get_text_range() for i in range(len(pdf))
        )
    except Exception:
        return []  # 텍스트 레이어를 못 읽어도 본 추출은 이미 끝났다

    haystack = _flatten(markdown)
    known = set(_WORD.findall(haystack))

    seen, dropped = set(), []
    for line in raw.splitlines():
        line = line.strip()
        # 한 글자·기호뿐인 줄은 주워도 쓸모가 없다
        if len(line) < 3 or not any(ch.isalnum() for ch in line):
            continue
        key = _flatten(line)
        if key in seen or key in haystack:
            continue
        # 낱말이 모두 이미 있으면 옮겨진 것이다. Docling이 셀 순서를 바꿔 놓으면
        # 문자열로는 못 걸러지므로(번호 열이 맨 뒤로 간 표가 있다) 낱말로 본다.
        words = set(_WORD.findall(key))
        if words and words <= known:
            continue
        seen.add(key)
        dropped.append(line)
    return dropped


def augment_with_dropped_text(path: Path, markdown: str) -> str:
    """Docling이 흘린 글자를 부록으로 붙인 Markdown 을 돌려준다.

    표가 아닌 평문으로 붙이므로 표 파서에는 잡히지 않고, 머리말 추출과 근거 대조
    에만 보인다. 이미 저장된 문서를 재추출 없이 보강할 때도 이 함수를 쓴다.
    """
    dropped = _dropped_text(path, markdown)
    if not dropped:
        return markdown
    return markdown + "\n\n## (레이아웃 밖에서 읽은 텍스트)\n\n" + "\n\n".join(dropped)


def extract(pdf_path: str | Path) -> ExtractionResult:
    """PDF 한 건을 Markdown + Docling JSON으로 변환한다."""
    result = get_converter().convert(str(pdf_path))
    doc = result.document

    # Docling 마크다운에는 '&amp;' 같은 HTML 엔티티가 그대로 남는다. 그대로 두면
    # 'KCC I&amp;C' 를 회사명으로 못 알아보는 등 LLM 추출이 나빠지고, 화면에도
    # 그렇게 보인다. 여기서 한 번 풀어 downstream 전체가 깨끗한 글자를 보게 한다.
    markdown = html.unescape(doc.export_to_markdown())
    docling_json = doc.export_to_dict()

    markdown = augment_with_dropped_text(Path(pdf_path), markdown)

    try:
        page_count = len(doc.pages)
    except Exception:
        page_count = 0

    return ExtractionResult(
        markdown=markdown, docling_json=docling_json, page_count=page_count
    )
