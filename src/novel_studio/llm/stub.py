"""StubProvider — 测试用。

按 step_id 模式返回预设的、schema-valid 的 JSON。让整条 pipeline 不用 LLM 也能跑通。

设计规则：
- 如果 step_id 以 `_audit_logic` / `_audit_pace` 结尾，返回 passed=True 的 AuditReport
- 如果 step_id 是 `L1` / `L2_N` / `L3_N` / `L4_adversarial_N` / `L4_scrubber_N` / `final_audit`，返回最小合法样本
- 可通过构造函数参数覆盖特定 step_id 的响应（按需注入测试分支）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BaseProvider, ProviderResult


# 预设响应模板
_STUB_TEMPLATES: dict[str, dict[str, Any]] = {
    "L1": {
        "title": "测试小说",
        "logline": "一个测试主角面对测试冲突。",
        "theme": "测试主题",
        "protagonist": {
            "name": "测试主角",
            "traits": ["有一个特质"],
            "want": "完成测试",
            "need": "学会测试",
            "wound": "",
            "lie": "",
        },
        "antagonist": None,
        "three_act": {"setup": "建立", "confrontation": "对抗", "resolution": "解决"},
        "world_rules": ["规则 1", "规则 2", "规则 3"],
        "revision": 0,
    },
    "_L2_TEMPLATE": {
        "title": "测试章节",
        "summary": "测试章节梗概。",
        "hook": "测试钩子。",
        "pov": "第三人称限知",
        "key_events": ["事件 1", "事件 2", "事件 3"],
        "prev_connection": "上一章承接",
        "revision": 0,
        "foreshadow_planted": [],
        "foreshadow_paid": [],
    },
    "_L3_TEMPLATE": {
        "content": "测试正文。" * 50,
        "word_count": 300,
        "revision": 0,
    },
    "_AUDIT_LOGIC": {
        "head": "logic",
        "passed": True,
        "score": 0.8,
        "issues": [],
        "suggestions": [],
    },
    "_AUDIT_PACE": {
        "head": "pace",
        "passed": True,
        "score": 0.8,
        "issues": [],
        "suggestions": [],
    },
    "final_audit": {
        "usable": True,
        "overall_score": 0.8,
        "symptoms": [],
        "suspect_layer": "none",
        "retry_hint": "",
        "slop_avg": 1.0,
    },
    "_L4_ADVERSARIAL_TEMPLATE": [
        {"category": "FAT", "quoted_text": "可有可无的句子", "reason": "stub"},
        {"category": "TELL", "quoted_text": "他很开心", "reason": "stub"},
        {"category": "GENERIC", "quoted_text": "常见描述", "reason": "stub"},
    ],
    "_L4_SCRUBBER_TEMPLATE": {
        "content": "清洗后的正文。" * 40,
        "adversarial_cuts": [],
        "polish_notes": ["删除了某些 slop 词", "合并了重复段落"],
        "revision": 0,
    },
}


class StubProvider(BaseProvider):
    """按 step_id 模式返回预设 JSON 的 provider。不调用任何外部服务。"""

    name = "stub"

    def __init__(self, overrides: dict[str, dict] | None = None):
        self._cache: dict[str, dict] = {}  # step_id -> response dict
        self._overrides = overrides or {}

    def request(self, step_id: str, prompt: str, pdir: Path) -> None:
        """根据 step_id 模式查模板，缓存响应。"""
        if step_id in self._overrides:
            self._cache[step_id] = self._overrides[step_id]
            return

        data = self._template_for(step_id)
        self._cache[step_id] = data

    def query(self, step_id: str, pdir: Path) -> ProviderResult:
        if step_id not in self._cache:
            return ProviderResult(ready=False)
        return ProviderResult(ready=True, data=self._cache[step_id])

    def reset(self, step_id: str, pdir: Path) -> None:
        self._cache.pop(step_id, None)

    @staticmethod
    def _template_for(step_id: str) -> Any:
        t = _STUB_TEMPLATES

        if step_id == "L1":
            return dict(t["L1"])
        if step_id == "final_audit":
            return dict(t["final_audit"])

        # 带索引的模式：L2_N / L3_N / L4_adversarial_N / L4_scrubber_N
        if step_id.startswith("L2_") and not step_id.endswith(("_logic", "_pace")):
            idx = int(step_id.split("_")[1])
            return {**dict(t["_L2_TEMPLATE"]), "index": idx}
        if step_id.startswith("L3_") and not step_id.endswith(("_logic", "_pace")):
            idx = int(step_id.split("_")[1])
            return {**dict(t["_L3_TEMPLATE"]), "index": idx}
        if step_id.startswith("L4_adversarial_"):
            # 返回一个 AdversarialCut 列表（而不是单个 object）
            return list(t["_L4_ADVERSARIAL_TEMPLATE"])
        if step_id.startswith("L4_scrubber_"):
            idx = int(step_id.split("_")[2])
            return {**dict(t["_L4_SCRUBBER_TEMPLATE"]), "index": idx}

        # Audit：默认通过
        if step_id.endswith("_audit_logic"):
            return dict(t["_AUDIT_LOGIC"])
        if step_id.endswith("_audit_pace"):
            return dict(t["_AUDIT_PACE"])

        # 未知 step_id：空 dict，让上层 schema 校验报错
        return {}
