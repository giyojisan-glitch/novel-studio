"""中文 prompt 模板。所有 LLM 调用（含对话里的 Claude）都从这里取 prompt。

prompt 文件会写到 projects/{slug}/queue/{step_id}.prompt.md，
对话里的 Claude 读后必须输出严格 JSON 到 responses/{step_id}.response.json。
"""
from __future__ import annotations
import json
from .state import NovelState, L1Skeleton, L2ChapterOutline, L3ChapterDraft


# ============ L1 骨架 ============
L1_SYSTEM = """你是资深类型小说策划，擅长从一句话前提推导出完整的小说骨架。
你要做的是给故事一个能支撑 {chapter_count} 章 × {words_per_chapter} 字的结构。"""

L1_TASK = """## 任务
根据以下前提生成小说骨架。

- **前提**：{premise}
- **类型**：{genre}
- **章节数**：{chapter_count}
- **每章目标字数**：{words_per_chapter}

## 要求
1. `logline` ≤ 25 字，比前提更精炼，必须点出主角 + 困境 + 赌注
2. 主角必须有 `want`（外在目标）和 `need`（内在成长）—— 两者可冲突
3. `three_act` 每幕 ≤ 60 字，三幕之间是因果链（不是并列）
4. `world_rules` 3-5 条，必须是能约束后续情节的硬规则（如"力量有代价"）
5. 反派可选；若有，`want` 要和主角对立

## 输出
严格 JSON，符合以下 schema：

```json
{schema}
```

只输出 JSON，不要 markdown 包裹，不要解释。
"""


# ============ L2 章节梗概 ============
L2_SYSTEM = """你是结构编辑，擅长把骨架拆解成有钩子、有伏笔的章节梗概。
每章都要推进主线、制造冲突、在章末留悬念。"""

L2_TASK = """## 任务
生成第 {chapter_idx} / {total} 章的梗概。

## 已有骨架（L1）
```json
{l1_json}
```

## 已完成的前面章节梗概
{prev_chapters}

## 要求
1. 明确本章在三幕中的位置（如"第1章属于 setup 后半"）
2. `summary` ≤ 200 字
3. `prev_connection`：如何接上一章的 hook（第1章就写"开篇"）
4. `hook`：章末悬念或情感钩子
5. `key_events` 3-5 条，每条是"谁在哪做了什么导致什么"的因果短句
6. `pov` 视角（第一人称 / 第三人称限知 / 第三人称全知）

{retry_hint}

## 输出
严格 JSON：

```json
{schema}
```
"""


# ============ L3 段落写作（按 genre 分风格）============
# 通用风格基础（硬编码，所有 genre 共享）
L3_SYSTEM_BASE = """你是中文网文写手。文风自然、有画面感、节奏利落，严禁 AI 腔。

通用严禁：
- 用"他的眼神中透露出..."这种形容词堆叠
- "仿佛时间静止了一般"这种套话
- 对话全是感叹号
- 过度解释人物内心（show don't tell，用动作和物体说话）
"""

# 风格包从独立文件读取（NOVEL-Studio/styles/{genre}.md），改这些文件立即生效
from .utils import STYLES_ROOT


def _load_style(genre: str) -> str:
    path = STYLES_ROOT / f"{genre}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text


def l3_system_for(genre: str) -> str:
    style = _load_style(genre)
    if not style:
        return L3_SYSTEM_BASE
    return L3_SYSTEM_BASE + "\n\n" + style

L3_TASK = """## 任务
写第 {chapter_idx} / {total} 章的正文。

## 骨架（L1 精简）
- 标题：{l1_title}
- 主角：{protagonist_name}（{protagonist_trait}），想要 {want}，需要 {need}
- 世界规则：{world_rules}

## 本章梗概（L2）
```json
{l2_json}
```

## 上一章结尾（最后 150 字）
{prev_tail}

## 要求
1. 严格按 `key_events` 顺序推进，不得跳过
2. 开头必须自然接上 `prev_connection`
3. 结尾必须落到 `hook`
4. 目标字数 {target_words} ± 20%
5. 场景 + 动作 + 对话 + 内心独白四要素齐全，但不平均分配
6. **不要写章节标题**，只写正文
7. **不要解决本章之外的问题**（别一口气写完整本书）

{retry_hint}

## 输出
严格 JSON：

```json
{schema}
```

`content` 字段放纯正文文本（可以有换行，用 "\\n" 表示）；`word_count` 是中文字符数（不含标点）；`index` 是 {chapter_idx}；`revision` 是 {revision}。
"""


# ============ 审稿头 ============
AUDIT_LOGIC_SYSTEM = """你是严格的逻辑编辑。你只管一件事：**逻辑一致性**。
检查：
- 角色行为是否符合其 `want/need/traits`
- 时间线是否矛盾
- 因果是否断裂
- 世界规则是否被违反
- 伏笔是否合理

给 score（0-1）和具体问题列表。不看文采，不看节奏。"""

AUDIT_PACE_SYSTEM = """你是节奏感编辑。你只管一件事：**节奏与张力**。
检查：
- 本层是否匹配其在三幕中的位置（第1章不该有高潮）
- 冲突密度是否合理
- 钩子是否有劲
- 字数分配是否合理
- 信息密度是否过载或过稀

给 score（0-1）和具体问题列表。不管逻辑细节，只看节奏。"""

AUDIT_TASK = """## 任务
审阅以下 {layer} 层产出（{target_desc}）。

## 被审对象
```json
{target_json}
```

## 参考上下文（L1 骨架等）
```json
{context_json}
```

## 要求
1. 给出 `score`（0.0-1.0）：≥ 0.7 算通过
2. `passed` 布尔值：score ≥ 0.7
3. `issues` 列表：若不通过，具体指出问题（每条 ≤ 30 字）
4. `suggestions` 列表：给下次重写的定向指导（每条 ≤ 30 字）
5. 宽松一点：MVP 阶段先让故事跑通，别卡太死（但明显矛盾必须打回）

## 输出
严格 JSON：

```json
{schema}
```

`head` 字段固定为 "{head}"。
"""


# ============ 辅助 ============
def schema_of(cls) -> str:
    """导出 Pydantic 模型的 JSON schema（简化版），用于 prompt 里让 Claude 知道要输出什么结构。"""
    schema = cls.model_json_schema()
    return json.dumps(schema, ensure_ascii=False, indent=2)


def l1_prompt(state: NovelState) -> str:
    ui = state.user_input
    retry = _retry_hint(state, "L1")
    return (
        L1_SYSTEM.format(chapter_count=ui.chapter_count, words_per_chapter=ui.target_words_per_chapter)
        + "\n\n"
        + L1_TASK.format(
            premise=ui.premise,
            genre=ui.genre,
            chapter_count=ui.chapter_count,
            words_per_chapter=ui.target_words_per_chapter,
            schema=schema_of(L1Skeleton),
        )
        + (f"\n\n## 上轮审稿反馈（请针对性修正）\n{retry}" if retry else "")
    )


def l2_prompt(state: NovelState, chapter_idx: int) -> str:
    prev = "\n".join(
        f"- 第{c.index}章《{c.title}》：{c.summary}" for c in state.l2 if c.index < chapter_idx
    ) or "（这是第一章）"
    retry = _retry_hint(state, f"L2_{chapter_idx}")
    return (
        L2_SYSTEM
        + "\n\n"
        + L2_TASK.format(
            chapter_idx=chapter_idx,
            total=state.user_input.chapter_count,
            l1_json=state.l1.model_dump_json(indent=2, by_alias=False),
            prev_chapters=prev,
            retry_hint=(f"## 上轮审稿反馈（请针对性修正）\n{retry}" if retry else ""),
            schema=schema_of(L2ChapterOutline),
        )
    )


def l3_prompt(state: NovelState, chapter_idx: int) -> str:
    l2 = next(c for c in state.l2 if c.index == chapter_idx)
    prev_tail = ""
    if chapter_idx > 1:
        prev_draft = next((d for d in state.l3 if d.index == chapter_idx - 1), None)
        if prev_draft:
            prev_tail = prev_draft.content[-150:]
    retry = _retry_hint(state, f"L3_{chapter_idx}")
    l1 = state.l1
    return (
        l3_system_for(state.user_input.genre)
        + "\n\n"
        + L3_TASK.format(
            chapter_idx=chapter_idx,
            total=state.user_input.chapter_count,
            l1_title=l1.title,
            protagonist_name=l1.protagonist.name,
            protagonist_trait="、".join(l1.protagonist.traits),
            want=l1.protagonist.want,
            need=l1.protagonist.need,
            world_rules="；".join(l1.world_rules),
            l2_json=l2.model_dump_json(indent=2, by_alias=False),
            prev_tail=prev_tail or "（这是第一章）",
            target_words=state.user_input.target_words_per_chapter,
            retry_hint=(f"## 上轮审稿反馈\n{retry}" if retry else ""),
            schema=schema_of(L3ChapterDraft),
            revision=_current_revision(state, f"L3_{chapter_idx}"),
        )
    )


def audit_prompt(state: NovelState, layer: str, target_idx: int | None, head: str) -> str:
    from .state import AuditReport

    head_system = {"logic": AUDIT_LOGIC_SYSTEM, "pace": AUDIT_PACE_SYSTEM}[head]
    if layer == "L1":
        target = state.l1
        desc = "L1 骨架整体"
        ctx = {"user_input": state.user_input.model_dump()}
    elif layer == "L2":
        target = next(c for c in state.l2 if c.index == target_idx)
        desc = f"L2 第 {target_idx} 章梗概"
        ctx = {"l1": state.l1.model_dump()}
    else:  # L3
        target = next(c for c in state.l3 if c.index == target_idx)
        desc = f"L3 第 {target_idx} 章正文"
        ctx = {
            "l1": state.l1.model_dump(),
            "l2_for_chapter": next(c for c in state.l2 if c.index == target_idx).model_dump(),
        }

    return (
        head_system
        + "\n\n"
        + AUDIT_TASK.format(
            layer=layer,
            target_desc=desc,
            target_json=target.model_dump_json(indent=2),
            context_json=json.dumps(ctx, ensure_ascii=False, indent=2, default=str),
            schema=schema_of(AuditReport),
            head=head,
        )
    )


def _retry_hint(state: NovelState, step_key: str) -> str:
    v = state.last_audit(step_key)
    if v and not v.passed:
        return v.retry_hint
    return ""


def _current_revision(state: NovelState, step_key: str) -> int:
    if step_key.startswith("L3_"):
        idx = int(step_key.split("_")[1])
        draft = next((d for d in state.l3 if d.index == idx), None)
        return draft.revision if draft else 0
    return 0
