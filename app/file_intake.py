"""上传文件进入解析流程前的安全校验与元数据生成。"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import PurePath
from typing import Collection, Mapping
from uuid import uuid4

from app.state import SourceDocument


class IntakeErrorCode(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNSUPPORTED = "unsupported"
    TYPE_MISMATCH = "type_mismatch"
    DAMAGED = "damaged"
    ENCRYPTED = "encrypted"
    UNSAFE_CONTENT = "unsafe_content"


class FileIntakeError(ValueError):
    """可安全展示给调用方的文件接入错误。"""

    def __init__(self, code: IntakeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FileTypeRule:
    media_type: str
    extensions: tuple[str, ...]


DEFAULT_FILE_TYPES = (
    FileTypeRule("application/pdf", (".pdf",)),
    FileTypeRule("image/png", (".png",)),
    FileTypeRule("image/jpeg", (".jpg", ".jpeg")),
    FileTypeRule(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (".docx",),
    ),
    FileTypeRule(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        (".xlsx",),
    ),
    FileTypeRule(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        (".pptx",),
    ),
)


@dataclass(frozen=True, slots=True)
class FileIntakePolicy:
    max_bytes: int = 20 * 1024 * 1024
    max_archive_entries: int = 10_000
    max_archive_uncompressed_bytes: int = 100 * 1024 * 1024
    file_types: tuple[FileTypeRule, ...] = DEFAULT_FILE_TYPES

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("max_bytes 必须大于 0")
        if self.max_archive_entries < 1 or self.max_archive_uncompressed_bytes < 1:
            raise ValueError("压缩文档安全限制必须大于 0")


@dataclass(frozen=True, slots=True)
class IntakeResult:
    document: SourceDocument
    is_duplicate: bool


class FileIntakeService:
    """校验不可信上传内容并创建受控文件引用。"""

    def __init__(self, policy: FileIntakePolicy | None = None) -> None:
        self.policy = policy or FileIntakePolicy()
        self._by_extension = {
            extension: rule for rule in self.policy.file_types for extension in rule.extensions
        }

    def accept(
        self,
        filename: str,
        content: bytes,
        *,
        declared_media_type: str | None = None,
        known_hashes: Mapping[str, str] | Collection[str] = (),
    ) -> IntakeResult:
        safe_basename = PurePath(filename.replace("\\", "/")).name
        if (
            not safe_basename.strip()
            or len(safe_basename) > 255
            or any(ord(character) < 32 for character in safe_basename)
        ):
            raise FileIntakeError(IntakeErrorCode.UNSAFE_CONTENT, "文件名包含不安全字符")
        extension = PurePath(safe_basename).suffix.lower()
        rule = self._by_extension.get(extension)
        if rule is None:
            raise FileIntakeError(IntakeErrorCode.UNSUPPORTED, "不支持该文件格式")
        if not content:
            raise FileIntakeError(IntakeErrorCode.EMPTY, "文件内容为空")
        if len(content) > self.policy.max_bytes:
            raise FileIntakeError(IntakeErrorCode.TOO_LARGE, "文件超过允许的大小限制")
        if declared_media_type and declared_media_type.lower() != rule.media_type:
            raise FileIntakeError(IntakeErrorCode.TYPE_MISMATCH, "文件扩展名与声明类型不一致")

        self._validate_content(rule.media_type, content)
        digest = hashlib.sha256(content).hexdigest()
        duplicate_of = _duplicate_document_id(known_hashes, digest)
        document_id = uuid4().hex
        stored_filename = f"{document_id}{extension}"
        return IntakeResult(
            document=SourceDocument(
                document_id=document_id,
                filename=safe_basename,
                stored_filename=stored_filename,
                media_type=rule.media_type,
                content_hash=digest,
                byte_size=len(content),
                duplicate_of=duplicate_of,
            ),
            is_duplicate=duplicate_of is not None,
        )

    def _validate_content(self, media_type: str, content: bytes) -> None:
        if media_type == "application/pdf":
            if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
                raise FileIntakeError(IntakeErrorCode.DAMAGED, "PDF 文件结构不完整")
            if re.search(rb"/Encrypt\b", content):
                raise FileIntakeError(IntakeErrorCode.ENCRYPTED, "PDF 文件已加密")
            if re.search(rb"/(?:JavaScript|JS|Launch|EmbeddedFile)\b", content):
                raise FileIntakeError(
                    IntakeErrorCode.UNSAFE_CONTENT, "PDF 包含不允许的主动内容"
                )
            return
        if media_type == "image/png":
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or not content.endswith(b"IEND\xaeB`\x82"):
                raise FileIntakeError(IntakeErrorCode.DAMAGED, "PNG 文件结构不完整")
            return
        if media_type == "image/jpeg":
            if not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
                raise FileIntakeError(IntakeErrorCode.DAMAGED, "JPEG 文件结构不完整")
            return
        _validate_openxml(media_type, content, self.policy)


def _validate_openxml(
    media_type: str, content: bytes, policy: FileIntakePolicy
) -> None:
    if not content.startswith(b"PK"):
        if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and (
            b"EncryptedPackage" in content or b"EncryptionInfo" in content
        ):
            raise FileIntakeError(IntakeErrorCode.ENCRYPTED, "Office 文件已加密")
        raise FileIntakeError(IntakeErrorCode.DAMAGED, "Office 文件不是有效的 Open XML 文档")
    required_part = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word/document.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt/presentation.xml",
    }[media_type]
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > policy.max_archive_entries:
                raise FileIntakeError(IntakeErrorCode.TOO_LARGE, "Office 文件包含过多压缩条目")
            if sum(info.file_size for info in entries) > policy.max_archive_uncompressed_bytes:
                raise FileIntakeError(IntakeErrorCode.TOO_LARGE, "Office 文件解压后超过安全限制")
            if any(info.flag_bits & 0x1 for info in entries):
                raise FileIntakeError(IntakeErrorCode.ENCRYPTED, "Office 文件已加密")
            _validate_archive_members(entries)
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or required_part not in names:
                raise FileIntakeError(IntakeErrorCode.TYPE_MISMATCH, "Office 文件内容与扩展名不一致")
            for info in entries:
                if info.filename.lower().endswith((".xml", ".rels")):
                    xml_prefix = archive.read(info, pwd=None)[:4096].lower()
                    if b"<!doctype" in xml_prefix or b"<!entity" in xml_prefix:
                        raise FileIntakeError(
                            IntakeErrorCode.UNSAFE_CONTENT,
                            "Office 文件包含不允许的 XML 外部实体声明",
                        )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise FileIntakeError(IntakeErrorCode.DAMAGED, "Office 文件结构损坏")
    except FileIntakeError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise FileIntakeError(IntakeErrorCode.DAMAGED, "Office 文件结构损坏") from exc


def _validate_archive_members(entries: list[zipfile.ZipInfo]) -> None:
    normalized_names: set[str] = set()
    for info in entries:
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        normalized = name.casefold()
        unix_type = (info.external_attr >> 16) & 0o170000
        if (
            not name
            or "\x00" in name
            or name.startswith("/")
            or re.match(r"^[a-zA-Z]:", name)
            or ".." in parts
            or unix_type == 0o120000
            or normalized in normalized_names
        ):
            raise FileIntakeError(
                IntakeErrorCode.UNSAFE_CONTENT, "Office 压缩包包含不安全路径或重复条目"
            )
        normalized_names.add(normalized)
        if (
            normalized.endswith("vbaproject.bin")
            or "/embeddings/" in f"/{normalized}"
            or "/externallinks/" in f"/{normalized}"
        ):
            raise FileIntakeError(
                IntakeErrorCode.UNSAFE_CONTENT, "Office 文件包含宏、嵌入对象或外部链接"
            )


def _duplicate_document_id(
    known_hashes: Mapping[str, str] | Collection[str], digest: str
) -> str | None:
    if isinstance(known_hashes, Mapping):
        return next((document_id for document_id, value in known_hashes.items() if value == digest), None)
    return digest if digest in known_hashes else None
