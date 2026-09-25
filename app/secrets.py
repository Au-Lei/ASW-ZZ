"""外部服务凭据只从进程环境读取，避免进入状态和日志。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class SecretConfigurationError(ValueError):
    """凭据缺失或配置错误；消息不包含凭据内容。"""


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    _value: str

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def reveal(self) -> str:
        """仅在构建服务客户端时显式取值。"""

        return self._value


def read_secret(name: str, environ: Mapping[str, str] | None = None) -> SecretValue:
    """读取明确允许的应用凭据名，不从文件或调用参数获取。"""

    allowed = {"ASW_AI_API_KEY", "ASW_OCR_API_KEY", "ASW_STORAGE_KEY"}
    if name not in allowed:
        raise SecretConfigurationError("不支持的凭据配置项")
    source = os.environ if environ is None else environ
    value = source.get(name)
    if not value or not value.strip():
        raise SecretConfigurationError(f"缺少必需的环境变量：{name}")
    return SecretValue(value)
