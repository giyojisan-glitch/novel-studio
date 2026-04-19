"""Multi-Head Audit aggregator：把多个 head 的 AuditReport 聚合成 AuditVerdict。"""
from __future__ import annotations
from .state import AuditReport, AuditVerdict, Layer


PASS_SCORE_THRESHOLD = 0.7
PASS_AVG_THRESHOLD = 0.7
MIN_HEADS_PASSED = 1     # MVP 宽松：至少 1 头通过 + 平均分达标即整体通过
MAX_REVISION = 2          # 每层最多重写次数


def aggregate(layer: Layer, target_idx: int | None, reports: list[AuditReport]) -> AuditVerdict:
    """聚合多头审稿报告。

    规则（MVP 宽松版）：
    - 每头 score >= PASS_SCORE_THRESHOLD 视为该头通过
    - 至少 MIN_HEADS_PASSED 头通过 且 平均 score >= PASS_AVG_THRESHOLD → 整体通过
    """
    if not reports:
        raise ValueError("reports 为空")

    pass_count = sum(1 for r in reports if r.passed and r.score >= PASS_SCORE_THRESHOLD)
    avg = sum(r.score for r in reports) / len(reports)
    passed = (pass_count >= MIN_HEADS_PASSED) and (avg >= PASS_AVG_THRESHOLD)

    hint_parts = []
    for r in reports:
        if not r.passed and r.issues:
            hint_parts.append(f"[{r.head}] " + "；".join(r.issues))
            if r.suggestions:
                hint_parts.append(f"[{r.head} 建议] " + "；".join(r.suggestions))
    retry_hint = "\n".join(hint_parts)

    return AuditVerdict(
        layer=layer,
        target_index=target_idx,
        reports=reports,
        passed=passed,
        retry_hint=retry_hint,
    )


def should_force_pass(revision: int) -> bool:
    """超过最大重写次数后强制放行（防死循环）。"""
    return revision >= MAX_REVISION
