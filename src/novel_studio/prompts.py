"""中文 prompt 模板。所有 LLM 调用（含对话里的 Claude）都从这里取 prompt。

prompt 文件会写到 projects/{slug}/queue/{step_id}.prompt.md，
对话里的 Claude 读后必须输出严格 JSON 到 responses/{step_id}.response.json。
"""
from __future__ import annotations
import json
from .state import (
    NovelState,
    L1Skeleton,
    L2ChapterOutline,
    L3ChapterDraft,
    L4PolishedChapter,
    FinalVerdict,
    AdversarialCut,
)


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


_LANG_HEADER = {
    "zh": "",  # default, prompt 本身已是中文
    "ja": (
        "🌏 **OUTPUT LANGUAGE: JAPANESE (日本語)**\n"
        "本指示書は中国語で書かれていますが、**生成される物語本文（content フィールド）は"
        "日本語で書いてください**。JSON のキー名や revision 等のメタデータは英数字のままで構いません。\n\n"
    ),
}


# L1/L2 用的轻量级语言头——这些层主要是结构设计（title/summary/events），
# 本体是日文小说时，title/logline/summary/hook/key_events 等字段也应该用日文写。
_LANG_HEADER_META = {
    "zh": "",
    "ja": (
        "🌏 **OUTPUT LANGUAGE: JAPANESE (日本語)**\n"
        "物語のタイトル・ログライン・章概要・キーイベント等はすべて**日本語で記入**してください。"
        "指示文は中国語ですが、出力されるフィールド値は日本語にしてください。JSON の構造は保持。\n\n"
    ),
}


def _lang_meta_header(language: str) -> str:
    return _LANG_HEADER_META.get(language, "")


def l3_system_for(genre: str, language: str = "zh") -> str:
    style = _load_style(genre)
    header = _LANG_HEADER.get(language, "")
    base = header + L3_SYSTEM_BASE
    if not style:
        return base
    return base + "\n\n" + style

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

`content` 字段放纯正文文本；`word_count` 是文本字符数（中文：汉字数；日文：假名+汉字数；英文：单词数）——不含标点和空白；`index` 是 {chapter_idx}；`revision` 是 {revision}。

## 🔴 JSON 格式铁律（违反会导致整章作废）
- **第一个字符必须是 `{{`**；不要 markdown 包裹，不要写任何解释
- **`content` 里的换行用 `\\n`**（literal 两字符：反斜杠+n），**不要真实换行**
- **`content` 里的引号用中文 `「」`**，不要用英文 `"` 或 `'`
- **`content` 里不要出现孤立反斜杠 `\\ `**（反斜杠+空格）—— 这会炸 JSON 解析器
- **不要在字符串值里用 markdown 语法**（比如 `**加粗**` `- 列表`）
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
        _lang_meta_header(ui.language)
        + L1_SYSTEM.format(chapter_count=ui.chapter_count, words_per_chapter=ui.target_words_per_chapter)
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
        _lang_meta_header(state.user_input.language)
        + L2_SYSTEM
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

    # —— 灵感库 RAG 注入（最初 vision 的 Cross-Attention 式分层参考） ——
    inspiration_block = _inspiration_few_shot(state, l2)

    return (
        l3_system_for(state.user_input.genre, state.user_input.language)
        + inspiration_block
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


def _inspiration_few_shot(state: NovelState, l2) -> str:
    """L3 生成前：用 L2 summary + hook 作 query，从灵感库拉 3 个最像的片段注入 prompt。

    设计：
    - 库空/model 未下载 → 静默返回空字符串，不影响现有流程
    - 库有内容 → 插入一段「## 风格参考片段（来自灵感库）」的 few-shot
    - 每段带标签「【作家·作品】」，让 AI 知道是引用不是原创
    - 明确说"这是参考腔调和质感，不是抄情节"——防止 AI 直接套抄
    """
    try:
        from .inspiration.retriever import get_retriever
        from .inspiration.schemas import StyleQuery

        retriever = get_retriever()
        # 库空 → 返回空，不触发模型下载
        try:
            if retriever.collection.count() == 0:
                return ""
        except Exception:
            return ""

        query_text = f"{l2.summary}\n\n{l2.hook}"
        chunks = retriever.retrieve(StyleQuery(
            query_text=query_text, top_k=3, min_chinese_chars=60, max_chinese_chars=400,
        ))
        if not chunks:
            return ""

        lines = [
            "\n\n## 🎨 风格参考片段（灵感库检索）",
            "以下是从你的灵感库里检索出的最接近本章语境的几段原文。**模仿其腔调、质感、节奏**，不要抄情节，不要直接借用原文句子：",
            "",
        ]
        for i, c in enumerate(chunks, 1):
            lines.append(f"### 参考 {i} · {c.short_label()}")
            lines.append(f"> {c.text}")
            lines.append("")
        lines.append("---")
        lines.append("*以上是参考腔调。下面按 premise / L1 / L2 正常写本章，用你自己的情节和句子。*")
        return "\n".join(lines)
    except Exception:
        # 灵感库不可用时静默 fallback（chromadb/sentence-transformers 未装也不影响）
        return ""


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


# ============ V2: Final-stage Audit ============
FINAL_AUDIT_SYSTEM = """你是小说质检总编。你不管单层对不对，只管**这本小说能不能用**。

你的职责是抓跨层 bug：
- 时间线矛盾：章节里的日期/年龄/时段前后不一致
- 伏笔漏掉：前面铺垫了但没兑现
- 配角坍塌：L1 骨架里重要的角色在 L3 完全消失
- 主题跑偏：成品的主题和 premise 承诺的明显偏离
- 世界规则违反：L1 定的 world_rules 被后续违反

别管单句美不美、有没有 slop（那有单独的 audit 头）。**只判"能给人看吗"**。

你要诚实：系统已经给每层打过分，但那些分不是你的依据。你的依据只有两样——**原始 premise** 和**成品全文**。"""

FINAL_AUDIT_TASK = """## 任务
做成品审。对照 premise 原文，通读整本书，判断是否"可用"。

## 原始 Premise（用户原始输入）
{premise}

## Genre：{genre}   目标长度：{total_target} 字

## 完整成品（L3 全文拼接）
{full_markdown}

## 机械检查附加
- 各章平均 slop 分：{slop_avg:.2f} / 10.0
{slop_hint}

## 必须检查
1. **时间线一致性**：成品里出现的任何日期/年龄/时段，彼此自洽吗？和 premise 说的对得上吗？
2. **伏笔闭合**：第 1 章铺垫的元素（人物、物件、承诺、威胁）后续是否兑现？
3. **角色兑现**：premise 提到的所有角色是否在成品里至少出现一次？戏份是否匹配 premise 设定的权重？
4. **世界规则**：L1 定的 world_rules 是否被剧情违反？
5. **主题兑现**：premise 承诺的主题冲突（外在 want vs 内在 need）是否真被展开？

## 判定标准
- `usable=true` 要求：上述 5 项没有**硬伤级**问题（小瑕疵不算）。硬伤 = 读者一读就迷惑/破戏。
- `usable=false`：至少 1 项硬伤存在。必须写清楚具体症状。

## 打回定位
如果 usable=false，推断问题源自：
- `premise`：原始输入就有矛盾/不完整（用户要修 premise）
- `L1`：骨架里的规则/角色/三幕已经错了（退回 L1 重写）
- `L2`：章节梗概割裂或漏接（退回 L2）
- `L3`：梗概对但执行偏（退回 L3）
- `L4`：润色改坏了（退回 L4）

## 输出
严格 JSON：

```json
{schema}
```

- `overall_score` 0.0-1.0（≥0.7 可用）
- `symptoms` 具体问题（每条 ≤30 字），如果可用则为空
- `suspect_layer` 从 premise/L1/L2/L3/L4/none 选
- `retry_hint` 打回时给那层的定向反馈（具体到"第 X 章的 Y 问题"，不要空话）
- `slop_avg` 填 {slop_avg:.2f}
"""


def final_audit_prompt(state: NovelState, full_markdown: str, slop_avg: float) -> str:
    ui = state.user_input
    total_target = ui.chapter_count * ui.target_words_per_chapter
    slop_hint = (
        "（所有章节都在干净区间，词汇层无明显 AI 味）"
        if slop_avg < 2.0
        else f"（平均 {slop_avg:.2f}，偏向轻度 AI 味，但结构问题优先）"
        if slop_avg < 4.0
        else f"（平均 {slop_avg:.2f}，slop 已偏高，考虑让 L4 彻底清洗）"
    )
    return FINAL_AUDIT_SYSTEM + "\n\n" + FINAL_AUDIT_TASK.format(
        premise=ui.premise,
        genre=ui.genre,
        total_target=total_target,
        full_markdown=full_markdown,
        slop_avg=slop_avg,
        slop_hint=slop_hint,
        schema=schema_of(FinalVerdict),
    )


# ============ V2: L4 Adversarial Edit ============
ADVERSARIAL_EDIT_SYSTEM = """你是狠心的删稿编辑。你的任务**不是润色**，是**暴露弱点**。

灵感来自 autonovel/adversarial_edit.py：最弱的内容是**什么会被最先切掉**。

你的工具只有一个：从这一章里砍 {cut_target} 字。砍完之后章节逻辑要依然成立。

砍掉的每一段都要归类：
- **FAT**：可有可无的形容词、修饰词、无意义的节奏补丁
- **REDUNDANT**：和前文/本段已表达过的内容重复
- **OVER_EXPLAIN**：旁白解释了场景本来已经演出来的东西（show vs tell 违反）
- **GENERIC**：没有本篇特色、换个作品也能用的通用句子
- **TELL**：直接告诉读者情绪/事实，而没用动作/物件/对白带出来
- **STRUCTURAL**：结构性赘笔（段落落点偏、转场生硬、无效过场）

你必须诚实：如果一章真的没废话，你也可以砍到上限以下，但必须给出至少 3 条切割以证明你在认真读。"""

ADVERSARIAL_EDIT_TASK = """## 任务
对本章执行对抗编辑：砍 {cut_target} 字。

## 章节信息
- 第 {chapter_idx} / {total_chapters} 章 · 《{chapter_title}》
- 当前字数：{current_words}
- 目标砍掉：{cut_target} 字（≈ {cut_percent}%）

## L1 骨架（参考）
- 主角 want：{protagonist_want}
- 主角 need：{protagonist_need}

## 本章 L2 梗概（章节目标）
{l2_summary}

## L3 原文
```
{l3_content}
```

## 输出
严格 JSON——一个 AdversarialCut 列表：

```json
{schema}
```

每条 `AdversarialCut`：
- `category` 从 FAT / REDUNDANT / OVER_EXPLAIN / GENERIC / TELL / STRUCTURAL 选
- `quoted_text` 是原文里**一字不差**的片段（用来后续定位删除），≤ 80 字
- `reason` ≤ 30 字，说清为什么砍

输出一个 JSON 数组，至少 3 条。不要 markdown 包裹。
"""


def adversarial_edit_prompt(state: NovelState, chapter_idx: int, cut_target: int) -> str:
    l3 = next(d for d in state.l3 if d.index == chapter_idx)
    l2 = next(c for c in state.l2 if c.index == chapter_idx)
    cut_percent = int(100 * cut_target / max(l3.word_count, 1))
    return ADVERSARIAL_EDIT_SYSTEM.format(cut_target=cut_target) + "\n\n" + ADVERSARIAL_EDIT_TASK.format(
        cut_target=cut_target,
        chapter_idx=chapter_idx,
        total_chapters=state.user_input.chapter_count,
        chapter_title=l2.title,
        current_words=l3.word_count,
        cut_percent=cut_percent,
        protagonist_want=state.l1.protagonist.want,
        protagonist_need=state.l1.protagonist.need,
        l2_summary=l2.summary,
        l3_content=l3.content,
        schema=json.dumps([AdversarialCut.model_json_schema()], ensure_ascii=False, indent=2),
    )


# ============ V2: L4 Scrubber ============
SCRUBBER_SYSTEM = """你是出版级清理编辑（灵感来自 AIStoryWriter/Writer/Scrubber.py）。

把一份 L3 原稿 + 对抗编辑切割清单 → 出版级成品。

清理三件事：
1. **应用对抗编辑建议**：把清单里每个 `quoted_text` 所在的原文片段删/改——具体怎么处理看你判断（删除 / 改写更紧 / 融合进邻段）
2. **去 AI 味**：扫全文，凡是 `styles/_anti_slop.md` 里定义的烂俗词/固定搭配/填充短语，全部改掉
3. **保持本章主旨**：不要删关键情节点，尤其是 `key_events` 和 `hook`

不做：
- 不改变情节走向
- 不新增设定
- 不润色到作者本人都认不出

输出**完整重写后的章节正文**，不是 diff。"""

SCRUBBER_TASK = """## 任务
清洗第 {chapter_idx} / {total_chapters} 章《{chapter_title}》。

## 原稿（L3）
```
{l3_content}
```

## 对抗编辑建议（需要应用）
{cuts_formatted}

## 本章核心剧情点（不能删）
Key events:
{key_events}
Hook: {hook}

## 输出
严格 JSON：

```json
{schema}
```

- `content` 是完整重写后的正文（可以有换行，用 `\\n` 表示）
- `adversarial_cuts` 照抄输入里那份清单（不要重新生成，不要改动）
- `polish_notes` 列出 3-8 条你做过的修改（每条 ≤ 30 字，如"删除第 2 段的'仿佛时间静止'"）
- `index` 是 {chapter_idx}
- `revision` 是 {revision}
"""


def scrubber_prompt(state: NovelState, chapter_idx: int) -> str:
    l3 = next(d for d in state.l3 if d.index == chapter_idx)
    l2 = next(c for c in state.l2 if c.index == chapter_idx)
    # 找到当前章节的 L4（如果已经有对抗编辑结果）
    l4 = next((p for p in state.l4 if p.index == chapter_idx), None)
    cuts = l4.adversarial_cuts if l4 else []
    if cuts:
        cuts_formatted = "\n".join(
            f"{i}. [{c.category}] 『{c.quoted_text}』 — {c.reason}"
            for i, c in enumerate(cuts, 1)
        )
    else:
        cuts_formatted = "（本章没有对抗编辑切割建议，只做 slop 清理即可）"
    key_events = "\n".join(f"- {e}" for e in l2.key_events)
    return SCRUBBER_SYSTEM + "\n\n" + SCRUBBER_TASK.format(
        chapter_idx=chapter_idx,
        total_chapters=state.user_input.chapter_count,
        chapter_title=l2.title,
        l3_content=l3.content,
        cuts_formatted=cuts_formatted,
        key_events=key_events,
        hook=l2.hook,
        schema=schema_of(L4PolishedChapter),
        revision=l4.revision if l4 else 0,
    )
