"""文本型 PDF 与 Office 文档的页级解析适配器。"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.state import SourceDocument
from app.tools.document_parser import (
    DocumentParseError, DocumentParseErrorCode, ParsedDocument, ParsedPage,
    TextRegion,
)


class PDFTextParser:
    """使用 pdfplumber 提取 PDF 文本行和页面坐标。"""

    def __init__(self, max_pages: int = 100) -> None:
        if max_pages < 1:
            raise ValueError("max_pages 必须大于 0")
        self.max_pages = max_pages

    def supports(self, media_type: str) -> bool:
        return media_type == "application/pdf"

    def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
        try:
            import pdfplumber
            from pdfminer.pdfdocument import PDFPasswordIncorrect

            with pdfplumber.open(BytesIO(content)) as source:
                if len(source.pages) < 1:
                    raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "PDF 没有页面")
                if len(source.pages) > self.max_pages:
                    raise DocumentParseError(DocumentParseErrorCode.TOO_MANY_PAGES, "PDF 页数超限")
                pages = []
                for number, page in enumerate(source.pages, start=1):
                    regions = tuple(
                        TextRegion(line["text"].strip(), float(line["x0"]), float(line["top"]),
                                   float(line["x1"]), float(line["bottom"]))
                        for line in page.extract_text_lines(return_chars=False)
                        if line["text"].strip() and line["x1"] > line["x0"]
                        and line["bottom"] > line["top"]
                    )
                    pages.append(ParsedPage(number, "\n".join(region.text for region in regions), regions))
                return ParsedDocument(document.document_id, tuple(pages))
        except DocumentParseError:
            raise
        except PDFPasswordIncorrect:
            raise DocumentParseError(DocumentParseErrorCode.ENCRYPTED, "PDF 已加密") from None
        except Exception as exc:
            raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "PDF 解析失败") from None


class OfficeTextParser:
    """提取 DOCX、XLSX 和 PPTX 中的文字；页指逻辑单元。"""

    _PART = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word/document.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt/presentation.xml",
    }

    def __init__(self, max_pages: int = 100, max_uncompressed_bytes: int = 100 * 1024 * 1024) -> None:
        if max_pages < 1 or max_uncompressed_bytes < 1:
            raise ValueError("Office 解析限制必须大于 0")
        self.max_pages = max_pages
        self.max_uncompressed_bytes = max_uncompressed_bytes

    def supports(self, media_type: str) -> bool:
        return media_type in self._PART

    def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
        kind = document.media_type
        if kind not in self._PART:
            raise DocumentParseError(DocumentParseErrorCode.UNSUPPORTED, "不支持该 Office 类型")
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                members = archive.namelist()
                if len(members) > 10_000 or sum(item.file_size for item in archive.infolist()) > self.max_uncompressed_bytes:
                    raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "Office 文档超出解析资源限制")
                if self._PART[kind] not in members:
                    raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "Office 文档缺少必要内容")
                if kind.endswith("wordprocessingml.document"):
                    texts = [_extract_xml_text(archive.read("word/document.xml"), "w:t")]
                elif kind.endswith("spreadsheetml.sheet"):
                    texts = _spreadsheet_texts(archive, members)
                else:
                    slide_names = sorted(
                        (name for name in members if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                        key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
                    )
                    texts = [_extract_xml_text(archive.read(name), "a:t") for name in slide_names]
                if not texts:
                    raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "Office 文档没有可用页面")
                if len(texts) > self.max_pages:
                    raise DocumentParseError(DocumentParseErrorCode.TOO_MANY_PAGES, "Office 逻辑页数超限")
                return ParsedDocument(
                    document.document_id,
                    tuple(ParsedPage(number, text) for number, text in enumerate(texts, start=1)),
                )
        except DocumentParseError:
            raise
        except (zipfile.BadZipFile, OSError, ET.ParseError, KeyError, RuntimeError) as exc:
            raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "Office 文档解析失败") from None


def _extract_xml_text(data: bytes, tag: str) -> str:
    local_name = tag.split(":", 1)[1]
    root = ET.fromstring(data)
    return "\n".join(
        node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] == local_name and node.text
    )


def _spreadsheet_texts(archive: zipfile.ZipFile, members: list[str]) -> list[str]:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in members:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root:
            shared.append("".join(node.text or "" for node in item.iter() if node.tag.rsplit("}", 1)[-1] == "t"))
    sheet_names = sorted(
        (name for name in members if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=lambda name: int(re.search(r"sheet(\d+)", name).group(1)),
    )
    texts = []
    for name in sheet_names:
        root = ET.fromstring(archive.read(name))
        values = []
        for cell in root.iter():
            if cell.tag.rsplit("}", 1)[-1] != "c":
                continue
            cell_type = cell.attrib.get("t")
            value = next((node.text for node in cell if node.tag.rsplit("}", 1)[-1] == "v"), None)
            if cell_type == "s" and value is not None:
                value = shared[int(value)]
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter() if node.tag.rsplit("}", 1)[-1] == "t")
            if value:
                values.append(value)
        texts.append("\n".join(values))
    return texts
