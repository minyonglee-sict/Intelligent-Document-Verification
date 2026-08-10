"""IBM Docling을 이용한 PDF -> 텍스트(Markdown) / JSON 추출."""

from __future__ import annotations

import html
import os
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

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def extract(pdf_path: str | Path) -> ExtractionResult:
    """PDF 한 건을 Markdown + Docling JSON으로 변환한다."""
    result = get_converter().convert(str(pdf_path))
    doc = result.document

    # Docling 마크다운에는 '&amp;' 같은 HTML 엔티티가 그대로 남는다. 그대로 두면
    # 'KCC I&amp;C' 를 회사명으로 못 알아보는 등 LLM 추출이 나빠지고, 화면에도
    # 그렇게 보인다. 여기서 한 번 풀어 downstream 전체가 깨끗한 글자를 보게 한다.
    markdown = html.unescape(doc.export_to_markdown())
    docling_json = doc.export_to_dict()

    try:
        page_count = len(doc.pages)
    except Exception:
        page_count = 0

    return ExtractionResult(
        markdown=markdown, docling_json=docling_json, page_count=page_count
    )
