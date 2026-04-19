"""Provider 基类。每个 provider 回答两个问题：
1. 这个 step_id 的响应准备好了吗？（query）
2. 如果准备好了，响应内容是什么？（fetch）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProviderResult:
    """Provider 的响应结果。

    data 类型：
    - 大部分 step 返回 dict（对应单个 Pydantic 模型）
    - L4_adversarial_N 返回 list（对应 list[AdversarialCut]）
    """
    ready: bool
    data: Any = None            # dict / list / None
    error: str | None = None    # 解析/获取失败的原因（ready=True 但有 error 说明拿到东西但不合法）


class BaseProvider(ABC):
    """所有 provider 的契约。

    两类 provider：
    - **异步式**（HumanQueue）：prompt 被写到外部队列，响应需要等待。CLI 多次 step 才能推进。
    - **同步式**（Stub / Anthropic API）：call 立即返回响应。

    设计上把两种统一成 `request + query`：request 发起请求（不阻塞），query 取结果。
    异步式里 request 把 prompt dump 到文件后返回；query 检查 response 文件是否到位。
    同步式里 request 直接调 API 拿到结果缓存在内存；query 立即返回。
    """

    name: str = "base"

    @abstractmethod
    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        """发起一次请求。对异步 provider，这里 dump prompt 文件；对同步 provider，这里真调 API 并缓存。"""
        raise NotImplementedError

    @abstractmethod
    def query(self, step_id: str, pdir: Path) -> ProviderResult:
        """检查某个 step 的响应是否就绪并返回。"""
        raise NotImplementedError

    def reset(self, step_id: str, pdir: Path) -> None:
        """重写时清理旧数据（prompt + response）。默认空实现。"""
        return None
