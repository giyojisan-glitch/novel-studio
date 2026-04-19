"""Prompt templates for benchmark framework."""
from __future__ import annotations

import json

from .schemas import BenchmarkVerdict, DimensionScore, DIMENSION_WEIGHTS


# =====================================================================
# Premise extractor
# =====================================================================
PREMISE_EXTRACTOR_SYSTEM = """你是资深类型小说策划。给你一篇完整的人类写作短篇小说，你要从它**逆向提取出 premise（前提）**——即：
如果这个故事是 AI 生成的，原作者在给 AI 的 prompt 里会怎么写？

你要抽取出的 premise 不是剧情总结，而是**创作指令**——主角轮廓、核心冲突、世界锚点、基调、关键画面。

必须严格遵守：
- **不要泄露剧情的结局和关键反转**——premise 是让一个"不知道结局的 AI"能写出类似作品的起点
- 长度 120-200 字
- 具体到可执行：主角要有具体职业/年龄、冲突要有时间锚和赌注、世界规则要是硬规则不是氛围
"""

PREMISE_EXTRACTOR_TASK = """## 任务
逆向提取这篇短篇的 premise。

## 原文
```
{original_text}
```

## 输出要求
输出一个 markdown 片段，包含以下 5 个小节：

1. **主角**：身份 / 年龄 / 当前状态 + 一个具体内心矛盾
2. **核心冲突**：外层（与谁对抗）+ 赌注（不做会失去什么）+ 时间锚点
3. **世界锚点**：1-2 条能约束剧情的硬规则（不要"修仙世界"这种泛泛概括）
4. **基调**：冷峻 / 爽文 / 黑色幽默 / 悲情 / 悬疑 等，带 1-2 个参考词
5. **关键画面**：1 个具体开场或高潮场景（不剧透结局）

**不要**输出情节梗概，不要泄露结局。只输出创作指令。
长度 120-200 字。
"""


def premise_extractor_prompt(original_text: str) -> str:
    return PREMISE_EXTRACTOR_SYSTEM + "\n\n" + PREMISE_EXTRACTOR_TASK.format(
        original_text=original_text[:8000]  # 长文本截断，避免超 context
    )


# =====================================================================
# Judge
# =====================================================================
JUDGE_SYSTEM = """你是公正的小说评审。你要比较两篇小说——一篇人写的**原文**，一篇 AI 写的**生成版**——给出 6 个维度的相似度评分。

相似度评分标准：
- 1.00 = 几乎一模一样（主角、冲突、世界、基调、走向、关键画面都对齐）
- 0.70 = 过关线（核心结构对上，细节可能跑偏）
- 0.50 = 及格线（骨架可辨但味道不对）
- 0.30 = 方向都不对
- 0.00 = 完全无关

你要诚实——不讨好 AI 生成版。看到明显跑偏直接打低分。"""

JUDGE_TASK = """## 任务
评估这两篇小说的相似度，输出 6 维度评分。

## 原文（人类作者）
```
{original_text}
```

## AI 生成版
```
{generated_text}
```

## 6 个评分维度

1. **plot_structure**（权重 25%）：三幕节奏 / 关键事件序列 / 主要转折是否对应
2. **character_core**（权重 20%）：主角的 want（外在目标）/ need（内在成长）/ 性格特质 / 创伤（wound）是否保留
3. **world_anchors**（权重 15%）：核心硬规则、设定锚点是否保留（忽略无关细节）
4. **tone**（权重 15%）：基调（冷峻 / 爽文 / 悲情 / 悬疑）是否一致
5. **ending_vector**（权重 15%）：结局方向（悲 / 喜 / 开放 / 逆转）是否一致，不要求结局细节一样
6. **key_scenes**（权重 10%）：关键画面 / 核心物件是否保留（名字不同但**功能相同**也算保留）

## 打分要求
- 每维给 `score` 0.0-1.0
- 每维给 `rationale` 一句话（≤40 字）说理由
- 每维给 `alignments`（生成版**对上**原文的具体点，最多 3 条，每条 ≤25 字）
- 每维给 `divergences`（生成版**偏离**原文的具体点，最多 3 条，每条 ≤25 字）

以及整体评价：
- `notes`：综合点评 ≤80 字

## 输出格式
严格 JSON，匹配这个 schema：

```json
{schema}
```

**不要** markdown 包裹，**不要**解释文字，只输出 JSON。
"""


def judge_prompt(original_text: str, generated_text: str) -> str:
    schema_sample = {
        "dimension_scores": [
            {
                "dimension": dim,
                "score": 0.0,
                "rationale": "",
                "alignments": [],
                "divergences": [],
            }
            for dim in DIMENSION_WEIGHTS
        ],
        "notes": "",
    }
    return JUDGE_SYSTEM + "\n\n" + JUDGE_TASK.format(
        original_text=original_text[:8000],
        generated_text=generated_text[:8000],
        schema=json.dumps(schema_sample, ensure_ascii=False, indent=2),
    )
