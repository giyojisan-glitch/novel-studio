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
    WorldBible,
    BibleUpdate,
    CharacterState,
    WorldFact,
    SceneOutline,
    ChapterSceneList,
    L3SceneDraft,
    SceneCard,
    TrackedObject,
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
6. **V5 必填** `visual_anchors`（3-5 条）：premise 明确要求或强烈暗示的**具体可目视的视觉/超自然呈现画面**
   - 必须是能**拍出来**的具体画面，不是抽象主题
   - 正例：「父亲化作泥塑上的裂纹」「三碗酒同时见底」「书生袖里多出三枚铜钱」
   - 反例：「父子情」「归途」「旧债清讫」（这些是主题不是视觉）
   - 若 premise 明示结尾画面 / 超自然呈现方式 / 象征物件，**必须全部进 visual_anchors**
7. **V5 必填** `tracked_object_names`（2-5 个）：正文里会反复出现且状态会变化的**关键物件名**
   - 如「三碗酒」「半块木牌」「泥塑土地公」
   - 不包含一次性道具（鞋、书箱）或通用元素（雨、风）
8. **V6 必填** `plot_promises`（3-5 条）：premise 里的**叙事承诺 / 情节机巧**（非视觉，视觉已在 visual_anchors）
   - 这是 premise 暗示但需要在正文中**以情节方式兑现**的专业机巧，不是画面
   - 正例（围棋题材）：「沈清埋下三颗跨越十年的死子，决赛时引爆」「顾衍之的棋路暴露沈家独门棋招的破绽」
   - 正例（悬疑题材）：「凶手的真身与被害者第一次见面时已暗示」「时间线有一处断裂」
   - 反例：「主角复仇」「反派被击败」（这是结局，不是机巧）
   - 每条 `id` 用 `fs_1`/`fs_2`/... 稳定编号；`content` ≤50 字；`setup_ch`/`payoff_ch` 留 0（由 L2 分配）
   - **若 premise 出现专业术语（"死子"/"暗桩"/"内线"/"断语"），必须有对应 promise**
9. **V6 必填** 主角（及反派）的 `faction` 字段：所属阵营，如「沈家」/「顾府」/「官方」/「中立」
   - 解决多股势力角色身份混淆问题；空字符串仅用于纯主角型 premise（如无对抗阵营的独行）

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


# ============ 创意档位头（strict / balanced / creative） ============
# balanced 不加任何 header（模型默认行为）；strict / creative 显式注入约束。
# 对应 AnthropicProvider._CREATIVITY_TEMPERATURE：temp 0.3 / 0.7 / 1.0
_CREATIVITY_HEADER = {
    "strict": (
        "🎯 **创意档位：STRICT（严格）**\n"
        "本次生成要求**严格按 premise 推进**。具体约束：\n"
        "- 不得添加 premise 未明示的**主要人物**（配角可轻度补全，但不要抢戏）\n"
        "- 不得添加 premise 未暗示的**关键转折**（诸如「原来主角是双胞胎」这种反转禁止）\n"
        "- 不得替换 premise 指定的**场景锚点**（场景地点 / 时代 / 物件要忠实保留）\n"
        "- 不得改动 premise 暗示的**结局方向**（悲剧不能强行圆满，反之亦然）\n"
        "- 角色心理、动作细节、环境描写仍可自由发挥——限制的是**剧情主脊**不是**文本颗粒度**\n\n"
    ),
    "balanced": "",  # 默认档，不加 header
    "creative": (
        "🎨 **创意档位：CREATIVE（创意）**\n"
        "本次生成**鼓励大胆补全**。具体方向：\n"
        "- premise 中未明说的人物关系、背景设定，可以**合理补全**成有张力的版本\n"
        "- 允许加入 premise 未提的**次要角色**或**意外转折**，只要服务于主线\n"
        "- 鼓励用**非常规叙事手段**（非线性时间、多视角、象征）来承载 premise 的内核\n"
        "- 不要把 premise 当说明书——把它当**种子**，生长出它自己也没想到的分支\n"
        "- 唯一底线：不要和 premise **直接矛盾**（背景设定 / 结局走向如果要反转，要自洽）\n\n"
    ),
}


def l3_system_for(genre: str, language: str = "zh", creativity: str = "balanced") -> str:
    style = _load_style(genre)
    creativity_header = _CREATIVITY_HEADER.get(creativity, "")
    lang_header = _LANG_HEADER.get(language, "")
    base = creativity_header + lang_header + L3_SYSTEM_BASE
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
        _CREATIVITY_HEADER.get(ui.creativity, "")
        + _lang_meta_header(ui.language)
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
        + _final_audit_retry_block(state)
    )


def l2_prompt(state: NovelState, chapter_idx: int) -> str:
    prev = "\n".join(
        f"- 第{c.index}章《{c.title}》：{c.summary}" for c in state.l2 if c.index < chapter_idx
    ) or "（这是第一章）"
    retry = _retry_hint(state, f"L2_{chapter_idx}")
    # V3/V4: 如果启用了 bible，把跨章真相账本注入
    pv = state.user_input.pipeline_version
    use_bible = pv in ("v3", "v4", "v5", "v6")
    bible_block = _bible_context_block(state.world_bible, "L2") if use_bible else ""
    final_audit_block = _final_audit_retry_block(state)

    # V4: 兑现 interleaved 承诺——注入上一章最后场景 closing 800 字
    prev_chapter_tail = ""
    if pv == "v4" and chapter_idx > 1:
        prior = _prior_chapter_last_scenes(state, chapter_idx, n=1)
        if prior and prior[-1].actual_closing:
            # SceneCard.actual_closing 只存了最后 200 字，但 l3_scenes 有全文
            last_ch_scenes = sorted(
                [s for s in state.l3_scenes if s.chapter_index == chapter_idx - 1],
                key=lambda s: s.scene_index,
            )
            if last_ch_scenes:
                full_tail = last_ch_scenes[-1].content[-800:]
                prev_chapter_tail = (
                    f"\n\n## 📖 上一章最后场景结尾（本章梗概必须与此自然承接，而不是重启人物）\n"
                    f"```\n{full_tail}\n```"
                )

    return (
        _CREATIVITY_HEADER.get(state.user_input.creativity, "")
        + _lang_meta_header(state.user_input.language)
        + L2_SYSTEM
        + bible_block
        + prev_chapter_tail
        + final_audit_block
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
    # —— V3: 世界观 bible 注入（长篇跨章一致性） ——
    bible_block = _bible_context_block(state.world_bible, "L3") if _v3_active(state) else ""
    # —— Bug E fix: 若本次写作是 final_audit bounce-back 的产物，注入成品审 retry_hint
    #   （否则 LLM 根本看不到整本书视角的反馈，会重复同样的飘）——
    final_audit_block = _final_audit_retry_block(state)

    return (
        l3_system_for(state.user_input.genre, state.user_input.language, state.user_input.creativity)
        + inspiration_block
        + bible_block
        + final_audit_block
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
    elif layer == "L25":
        # V4: L2.5 场景列表审稿
        target = next(x for x in state.scene_lists if x.chapter_index == target_idx)
        desc = f"L2.5 第 {target_idx} 章场景列表"
        ctx = {
            "l1": state.l1.model_dump() if state.l1 else {},
            "l2_for_chapter": next(c.model_dump() for c in state.l2 if c.index == target_idx),
        }
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


def _load_genre_author_whitelist(genre: str) -> list[str]:
    """读 styles/inspiration_routing.json，按 genre 返回作家白名单。

    - 找不到配置文件 / 解析失败 → 返回 []（等价无过滤）
    - genre 没在 mapping 里 → 返回 []（无过滤，全库检索）
    - genre 列表为空 [] → 返回 []（显式声明无过滤）
    - genre 有列表 → 返回那个 list（retriever 会把 query 限制到这些作家）
    """
    import json
    path = STYLES_ROOT / "inspiration_routing.json"
    if not path.exists():
        return []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        authors = config.get(genre, [])
        # 剔除元数据字段（_comment 等）
        if not isinstance(authors, list):
            return []
        return [a for a in authors if isinstance(a, str) and a]
    except Exception:
        return []


def _inspiration_few_shot(state: NovelState, l2) -> str:
    """L3 生成前：用 L2 summary + hook 作 query，从灵感库拉 3 个最像的片段注入 prompt。

    设计：
    - 库空/model 未下载 → 静默返回空字符串，不影响现有流程
    - 库有内容 → 插入一段「## 风格参考片段（来自灵感库）」的 few-shot
    - 每段带标签「【作家·作品】」，让 AI 知道是引用不是原创
    - 明确说"这是参考腔调和质感，不是抄情节"——防止 AI 直接套抄
    - `NOVEL_STUDIO_NO_RAG=1` 环境变量 → 强制禁用 RAG（A/B 对照实验用）
    - **Genre → 作家白名单路由**：`styles/inspiration_routing.json` 定义每个 genre 允许
      检索的作家，避免 corpus 不均衡时 genre 不匹配被淹没（志怪 query 拉到武侠的坑）
    """
    import os
    if os.getenv("NOVEL_STUDIO_NO_RAG") == "1":
        return ""

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
        # Genre → 作家白名单过滤（见 _load_genre_author_whitelist）
        author_whitelist = _load_genre_author_whitelist(state.user_input.genre)
        chunks = retriever.retrieve(StyleQuery(
            query_text=query_text,
            top_k=3,
            authors=author_whitelist,  # 空列表 = 不过滤
            min_chinese_chars=60,
            max_chinese_chars=400,
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


def _v3_active(state: NovelState) -> bool:
    return state.user_input.pipeline_version == "v3"


def _v5_active(state: NovelState) -> bool:
    """V5 激活：prompt 要加视觉锚点/time_marker/tracked_objects/character status 注入块。

    V6 也激活 V5 的所有机制（V6 是正交增量，不替换 V5）。
    """
    return state.user_input.pipeline_version in ("v5", "v6")


def _v6_active(state: NovelState) -> bool:
    """V6 激活：prompt 要加 plot_promises / faction / technical_setup/payoff 注入块。"""
    return state.user_input.pipeline_version == "v6"


# ============ V5 辅助函数 · 多个 prompt 共用 ============

def _prior_time_markers_block(state: NovelState) -> str:
    """V5 L2.5 用：前面章节已用过的 time_markers 列表。"""
    wb = state.world_bible
    if wb is None or not wb.time_markers_used:
        return "（本书第 1 章，暂无前序 time_markers）"
    return "、".join(f"「{m}」" for m in wb.time_markers_used)


def _render_tracked_objects_block(state: NovelState) -> str:
    """V5 L3 用：把 bible.tracked_objects 的当前状态渲染成一段 context。"""
    wb = state.world_bible
    if wb is None or not wb.tracked_objects:
        return ""
    lines = ["\n\n## 📦 被追踪物件 · 当前状态（正文描写必须与此一致）"]
    for obj in wb.tracked_objects:
        hist = ""
        if len(obj.state_history) >= 2:
            hist = f"（历史：{' → '.join(obj.state_history[-3:])}）"
        lines.append(f"- **{obj.name}**：{obj.current_state}{hist}")
    lines.append("> 正文里再描写这些物件时，**不得与当前状态矛盾**（如已裂的碗不得复原）。")
    return "\n".join(lines)


def _character_status_hints_block(state: NovelState) -> str:
    """V5 L3 用：角色存续状态（fading/gone 影响笔法）。"""
    wb = state.world_bible
    if wb is None:
        return ""
    non_active = [c for c in wb.characters if c.status != "active"]
    if not non_active:
        return ""
    lines = ["\n\n## 🎭 角色存续状态（V5 · 影响描写笔法）"]
    for c in non_active:
        if c.status == "fading":
            lines.append(
                f"- **{c.name}** [fading · 可信度 {c.reliability:.1f}]："
                f"只在回忆/间接提及中出现，描写用**模糊笔法**（具体细节模糊化）"
            )
        elif c.status == "gone":
            lines.append(
                f"- **{c.name}** [gone · 可信度 {c.reliability:.1f}]："
                f"**不得直接现身**，只能通过遗物、他人回忆、物件触发提及"
            )
    return "\n".join(lines)


def _unfulfilled_anchors_block(state: NovelState) -> str:
    """V5 L3 用：提示本场景应考虑兑现的 visual_anchors（未兑现的）。"""
    wb = state.world_bible
    if wb is None or not wb.visual_anchors:
        return ""
    fulfilled = set(wb.fulfilled_anchors)
    unfulfilled = [a for a in wb.visual_anchors if a not in fulfilled]
    if not unfulfilled:
        return ""
    lines = ["\n\n## 🎯 premise 视觉锚点 · 未兑现列表（必保细节）"]
    for a in unfulfilled:
        lines.append(f"- {a}")
    lines.append(
        "> 这些是 premise 明确或强烈暗示的**必保视觉画面**，整本书**必须**在某章真实出现"
        "（不是概括，而是具体写出来）。若本场剧情正合适兑现，请**写出来**；"
        "若不合适，留给后面章节——但**禁止整本书都不兑现**。"
    )
    return "\n".join(lines)


# ============ V6 辅助函数 · plot_promises / faction / technical_setup/payoff ============


def _render_plot_promises_block(state: NovelState) -> str:
    """V6 L3 通用：渲染 bible.plot_promises 当前账本状态。"""
    wb = state.world_bible
    if wb is None or not wb.plot_promises:
        return ""
    lines = ["\n\n## 📜 V6 叙事承诺账本（整本书的情节机巧 · 不可漏引爆）"]
    for p in wb.plot_promises:
        setup_tag = f"setup@ch{p.setup_ch}" if p.setup_ch > 0 else "未埋"
        payoff_tag = (
            f"payoff@ch{p.payoff_ch}✓" if p.fulfilled
            else (f"计划 payoff@ch{p.payoff_ch}" if p.payoff_ch > 0 else "未兑现")
        )
        lines.append(f"- **{p.id}**：{p.content}  [{setup_tag} / {payoff_tag}]")
    lines.append(
        "> **铁律**：若本章分配了某个 promise 的 setup，正文里必须**具体写出**埋设行为；"
        "若分配了 payoff，正文里必须**具体引爆**（用体裁专业术语，不是「真气喷血」式的模糊动作）。"
    )
    return "\n".join(lines)


def _chapter_promise_assignments_block(state: NovelState, chapter_idx: int) -> str:
    """V6 L3 专用：本章被 L2 分配了哪些 promise setup/payoff。"""
    l2 = next((o for o in state.l2 if o.index == chapter_idx), None)
    if l2 is None:
        return ""
    setups = list(getattr(l2, "promise_setups", []))
    payoffs = list(getattr(l2, "promise_payoffs", []))
    if not setups and not payoffs:
        return ""
    wb = state.world_bible
    lookup = {p.id: p.content for p in (wb.plot_promises if wb else [])}
    lines = [f"\n\n## 🎯 V6 本章（ch{chapter_idx}）分配到的叙事承诺"]
    if setups:
        lines.append("### 需要本章 setup（埋设）：")
        for pid in setups:
            lines.append(f"- **{pid}**：{lookup.get(pid, '(未知 id)')}")
    if payoffs:
        lines.append("### 需要本章 payoff（引爆）：")
        for pid in payoffs:
            lines.append(f"- **{pid}**：{lookup.get(pid, '(未知 id)')}")
    lines.append(
        "> 正文**必须具体兑现**这些机巧，不是「大意是这样」式的概括。"
        "若是围棋题材必须写出具体**棋位/招法**，若是悬疑题材必须留下具体**线索实体**。"
    )
    return "\n".join(lines)


def _character_faction_block(state: NovelState) -> str:
    """V6 L3 专用：渲染带阵营标签的角色列表（同名不同阵营矛盾示警）。"""
    wb = state.world_bible
    if wb is None:
        return ""
    tagged = [c for c in wb.characters if c.faction]
    if not tagged:
        return ""
    lines = ["\n\n## 🏴 V6 角色阵营图谱（正文描写同名角色时**禁止**改阵营）"]
    by_faction: dict[str, list[str]] = {}
    for c in tagged:
        by_faction.setdefault(c.faction, []).append(c.name)
    for fac, names in by_faction.items():
        lines.append(f"- **{fac}**：{'、'.join(names)}")
    lines.append(
        "> 若本场出现「暗桩」「内线」「下属」等多方共用词汇，**必须**通过服色/标记/阵营名显式区分"
        "（如「玄色劲装的沈家暗桩」vs「灰衣顾府眼线」）。"
    )
    return "\n".join(lines)


def _scene_technical_block(scene_outline) -> str:
    """V6 L3 专用：渲染本场的 technical_setup/payoff（体裁技术性铺垫）。"""
    ts = getattr(scene_outline, "technical_setup", "") or ""
    tp = getattr(scene_outline, "technical_payoff", "") or ""
    if not ts and not tp:
        return ""
    lines = ["\n\n## 🔧 V6 本场技术性伏笔（体裁专业术语硬约束）"]
    if ts:
        lines.append(f"- **setup**：{ts}")
        lines.append("  → 正文必须**具体写出**这个铺垫动作（用体裁术语，不是「真气/金光/喷血」式的模糊描写）")
    if tp:
        lines.append(f"- **payoff**：{tp}")
        lines.append("  → 正文必须**具体引爆**这个铺垫，并**回指 setup 章节**（而不是凭空出现结果）")
    return "\n".join(lines)


def _unfulfilled_promises_block(state: NovelState) -> str:
    """V6 final_audit 专用：列出整本书未兑现的 plot_promises。"""
    wb = state.world_bible
    if wb is None or not wb.plot_promises:
        return ""
    unfulfilled = [p for p in wb.plot_promises if not p.fulfilled]
    if not unfulfilled:
        return "✓ 所有叙事承诺均已兑现。"
    lines = ["⚠️ 以下 plot_promises 整本书未兑现（必须在 `unfulfilled_promises` 字段列出）："]
    for p in unfulfilled:
        lines.append(f"- {p.id}: {p.content}")
    return "\n".join(lines)


def _final_audit_retry_block(state: NovelState) -> str:
    """若存在上一轮 final_audit 反馈（usable=False，退回到 L1/L2/L3），在 L2/L3 prompt 里注入。

    这修的是 Bug E：之前 bounce-back 只清 state，但 final_verdict.retry_hint 这条"整本书层面"
    的具体反馈没有流到下游 prompt → LLM 看不到问题、重写后同样飘。
    """
    fv = state.final_verdict
    if fv is None or fv.usable:
        return ""
    if fv.suspect_layer not in ("L1", "L2", "L3"):
        return ""
    bounce_num = state.final_bounce_count
    symptoms = "\n".join(f"- {s}" for s in fv.symptoms) if fv.symptoms else "（无具体症状）"
    return (
        "\n\n## ⚠️ 成品审反馈（整本书层面 · 必须修正 · 第 "
        f"{bounce_num} 轮）\n"
        f"**整本书在 final_audit 被打回，总分 {fv.overall_score:.2f}。问题：**\n"
        f"{symptoms}\n\n"
        f"**定向反馈**（直接针对本章写作）：\n"
        f"> {fv.retry_hint}\n\n"
        "⚠️ 这不是层级 audit 反馈（那是单章节奏/逻辑层面），是**整本书的系统性偏离**。"
        "请严格按 premise 原文 + 上述反馈重写，不要沿用上一版的方向。"
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


# ============ V3: 世界观知识库（WorldBible） ============
def build_initial_bible(l1: L1Skeleton) -> WorldBible:
    """从 L1 骨架构造初始 WorldBible。纯函数，无 LLM。

    把 L1 里已有的结构化信息（主角/反派/世界规则）落到 bible：
    - CharacterState: 主角 + 反派（如有）
    - WorldFact: world_rules 每条转一条 rule 类 fact
    - active_foreshadow: 从 L1.theme + protagonist.wound/lie 隐含的承诺
    """
    characters: list[CharacterState] = []
    characters.append(CharacterState(
        name=l1.protagonist.name,
        traits=list(l1.protagonist.traits),
        arc_state="起点：尚未觉察 need",
        last_appeared_in=0,
        faction=getattr(l1.protagonist, "faction", ""),
    ))
    if l1.antagonist:
        characters.append(CharacterState(
            name=l1.antagonist.name,
            traits=list(l1.antagonist.traits),
            arc_state="起点：立场已定",
            last_appeared_in=0,
            faction=getattr(l1.antagonist, "faction", ""),
        ))
    facts: list[WorldFact] = [
        WorldFact(category="rule", content=r, ch_introduced=0)
        for r in l1.world_rules
    ]
    # L1 层暗示的承诺——theme + wound/need 的张力
    active_foreshadow: list[str] = []
    if l1.protagonist.need:
        active_foreshadow.append(f"主角须学会：{l1.protagonist.need}")
    if l1.protagonist.wound:
        active_foreshadow.append(f"主角创伤未愈：{l1.protagonist.wound}")
    # V5: 从 L1 copy 视觉锚点 + 初始化追踪物件（零 LLM 调用）
    visual_anchors = list(l1.visual_anchors)
    tracked_objects = [
        TrackedObject(name=n, current_state="初始 / 未使用", last_changed_ch=0)
        for n in l1.tracked_object_names
    ]
    # V6: 从 L1 copy 叙事承诺（保留 setup_ch/payoff_ch 如已预设）
    plot_promises = [p.model_copy() for p in l1.plot_promises]
    return WorldBible(
        characters=characters,
        facts=facts,
        timeline=[],
        active_foreshadow=active_foreshadow,
        paid_foreshadow=[],
        last_updated_ch=0,
        visual_anchors=visual_anchors,
        tracked_objects=tracked_objects,
        fulfilled_anchors=[],
        time_markers_used=[],
        plot_promises=plot_promises,
    )


def apply_bible_update(bible: WorldBible, update: BibleUpdate) -> WorldBible:
    """把 BibleUpdate 合并进 WorldBible。返回新的 bible（不修改原对象）。

    规则：
    - new_characters: 直接 append（去重：同名覆盖）
    - character_updates: 按 name 匹配 → 替换已有条目的 arc_state/last_appeared_in/notable_events
    - new_facts / timeline_additions: 直接追加
    - new_foreshadow: 追加到 active_foreshadow
    - paid_foreshadow: 从 active 移除 → 追加到 paid
    """
    chars_by_name = {c.name: c for c in bible.characters}
    for c in update.new_characters:
        chars_by_name[c.name] = c
    for c in update.character_updates:
        if c.name in chars_by_name:
            existing = chars_by_name[c.name]
            # 合并：traits 不动（稳定），voice_markers 合并去重，其余覆盖
            merged_voice = list(dict.fromkeys(existing.voice_markers + c.voice_markers))
            merged_events = list(existing.notable_events) + [
                e for e in c.notable_events if e not in existing.notable_events
            ]
            chars_by_name[c.name] = CharacterState(
                name=c.name,
                traits=existing.traits or c.traits,
                voice_markers=merged_voice,
                arc_state=c.arc_state or existing.arc_state,
                last_appeared_in=max(c.last_appeared_in, existing.last_appeared_in),
                notable_events=merged_events,
                status=existing.status,
                reliability=existing.reliability,
                # V6: 保留原 faction，除非更新项显式提供
                faction=c.faction or existing.faction,
            )
        else:
            chars_by_name[c.name] = c

    new_active = [f for f in bible.active_foreshadow if f not in update.paid_foreshadow]
    new_active += [f for f in update.new_foreshadow if f not in new_active]
    new_paid = list(bible.paid_foreshadow) + [
        f for f in update.paid_foreshadow if f not in bible.paid_foreshadow
    ]

    # V3 Bug C: facts 按 (category, content) 去重；timeline 按字面内容去重
    # 原因：bounce-back 会重跑 bible_update，LLM 可能再抽一遍相同的事实，
    # 导致 bible 膨胀（实测 214 条里 unique 只 39，5.5× 重复）
    existing_fact_keys = {(f.category, f.content) for f in bible.facts}
    deduped_new_facts = [
        f for f in update.new_facts
        if (f.category, f.content) not in existing_fact_keys
    ]
    existing_timeline = set(bible.timeline)
    deduped_new_timeline = [
        e for e in update.timeline_additions if e not in existing_timeline
    ]

    # V5: tracked_objects 按 name 更新（新状态覆盖旧；state_history 追加）
    objs_by_name = {o.name: o for o in bible.tracked_objects}
    for change in update.object_state_changes:
        existing = objs_by_name.get(change.name)
        if existing:
            new_history = list(existing.state_history) + [
                f"ch{update.chapter_index}: {change.current_state}"
            ]
            objs_by_name[change.name] = TrackedObject(
                name=change.name,
                current_state=change.current_state,
                last_changed_ch=update.chapter_index,
                state_history=new_history,
            )
        else:
            # 新追加（通常 L1 已 declare，但兜底）
            objs_by_name[change.name] = TrackedObject(
                name=change.name,
                current_state=change.current_state,
                last_changed_ch=update.chapter_index,
                state_history=[f"ch{update.chapter_index}: {change.current_state}"],
            )

    # V5: character_status_changes 覆盖角色 status/reliability（其余字段不动）
    for status_change in update.character_status_changes:
        if status_change.name in chars_by_name:
            existing = chars_by_name[status_change.name]
            chars_by_name[status_change.name] = CharacterState(
                name=existing.name,
                traits=existing.traits,
                voice_markers=existing.voice_markers,
                arc_state=existing.arc_state,
                last_appeared_in=existing.last_appeared_in,
                notable_events=existing.notable_events,
                status=status_change.status,
                reliability=status_change.reliability,
                faction=status_change.faction or existing.faction,
            )
        else:
            # 新角色且直接标为 fading/gone（容错：LLM 没先用 new_characters 加）
            chars_by_name[status_change.name] = status_change

    # V5: 已兑现 anchors 追加（去重）
    existing_fulfilled = set(bible.fulfilled_anchors)
    new_fulfilled = list(bible.fulfilled_anchors) + [
        a for a in update.visual_anchors_fulfilled if a not in existing_fulfilled
    ]

    # V6: plot_promises 账本维护
    # - promise_setups_done: 把 promise.setup_ch 置为 update.chapter_index（如果还没设）
    # - promise_payoffs_done: 把 promise.payoff_ch 置为 update.chapter_index + fulfilled=True
    promises_by_id = {p.id: p.model_copy() for p in bible.plot_promises}
    for pid in update.promise_setups_done:
        if pid in promises_by_id:
            p = promises_by_id[pid]
            if p.setup_ch == 0:
                p.setup_ch = update.chapter_index
    for pid in update.promise_payoffs_done:
        if pid in promises_by_id:
            p = promises_by_id[pid]
            p.payoff_ch = update.chapter_index
            p.fulfilled = True
            # 若本章同时 setup 未标，补上（LLM 可能漏报）
            if p.setup_ch == 0:
                p.setup_ch = update.chapter_index

    return WorldBible(
        characters=list(chars_by_name.values()),
        facts=list(bible.facts) + deduped_new_facts,
        timeline=list(bible.timeline) + deduped_new_timeline,
        active_foreshadow=new_active,
        paid_foreshadow=new_paid,
        last_updated_ch=max(bible.last_updated_ch, update.chapter_index),
        visual_anchors=list(bible.visual_anchors),                # 只从 L1 来，此处不改
        tracked_objects=list(objs_by_name.values()),
        fulfilled_anchors=new_fulfilled,
        time_markers_used=list(bible.time_markers_used),          # engine 在 L25 apply 时维护
        plot_promises=list(promises_by_id.values()),
    )


def _bible_context_block(bible: WorldBible | None, for_layer: str) -> str:
    """把 bible 渲染成 L2/L3 prompt 里的一段 context 文本。

    for_layer:
    - "L2": 给章节协商用——关注弧光阶段、活跃伏笔、规则
    - "L3": 给写作用——关注角色当前状态、不能违反的规则、需要承接的伏笔
    """
    if bible is None:
        return ""

    lines: list[str] = []
    lines.append("\n\n## 📖 世界观知识库（World Bible · 跨章真相账本）")
    lines.append("以下是前面章节已确立的事实。**本章必须与此一致，不得推翻**。\n")

    if bible.characters:
        lines.append("### 角色当前状态")
        for c in bible.characters:
            extra = ""
            if c.last_appeared_in > 0:
                extra = f"，最后出场第 {c.last_appeared_in} 章"
            events = ""
            if c.notable_events:
                events = "；已发生事件：" + "、".join(c.notable_events[-3:])  # 最近 3 件
            voice = ""
            if c.voice_markers:
                voice = f"；说话方式：{'/'.join(c.voice_markers[:3])}"
            arc = f"弧光：{c.arc_state}" if c.arc_state else ""
            lines.append(f"- **{c.name}**（{'、'.join(c.traits)}）{arc}{extra}{events}{voice}")
        lines.append("")

    if bible.facts:
        # 只列 rule + relationship + item 类 facts（最容易被违反）
        critical = [f for f in bible.facts if f.category in ("rule", "relationship", "item")]
        if critical:
            lines.append("### 已确立的硬设定（不得违反）")
            for f in critical[:12]:  # 防爆炸，最多 12 条
                tag = {"rule": "规则", "relationship": "关系", "item": "物件"}[f.category]
                lines.append(f"- [{tag}] {f.content}（立于第 {f.ch_introduced} 章）")
            lines.append("")

    if bible.active_foreshadow:
        lines.append("### 未兑现的伏笔（active）")
        for fs in bible.active_foreshadow[:8]:
            lines.append(f"- {fs}")
        if for_layer == "L2":
            lines.append("> 本章可以考虑兑现以上某条伏笔，也可以埋新的——但别让 active 列表无限增长。")
        else:
            lines.append("> 若本章涉及其中某条，请让它**在正文里真实发生**，不要只在对白里复述。")
        lines.append("")

    if bible.timeline:
        lines.append("### 事件时间线（最近 5 条）")
        for e in bible.timeline[-5:]:
            lines.append(f"- {e}")
        lines.append("")

    if bible.paid_foreshadow:
        lines.append(f"### 已兑现伏笔（{len(bible.paid_foreshadow)} 条）")
        lines.append(f"（已处理：{'、'.join(bible.paid_foreshadow[:4])}{'...' if len(bible.paid_foreshadow) > 4 else ''}）")
        lines.append("")

    return "\n".join(lines)


# ============ V3: bible_update prompt（LLM 增量抽取） ============
BIBLE_UPDATE_SYSTEM = """你是世界观校对员。你的职责不是审美、不是润色，而是**抽取本章新增事实 + 校对和已有 bible 的一致性**。

你读两份东西：
1. 当前 WorldBible（前 N-1 章已确立的真相）
2. 刚写完的第 N 章正文

你输出一份 BibleUpdate：**只写增量**——新登场的人、新确立的规则、本章发生的大事、本章埋的新伏笔、本章兑现的旧伏笔。

同时你要挑出**和 bible 矛盾的地方**（比如 bible 说某角色死了但本章他复活了；某规则被违反）。矛盾不是你修复——你只是指出来，让下一层决定怎么办。

严禁：
- 编造 bible 里不存在的"既有设定"
- 重复已经在 bible 里的东西（只写增量）
- 用华丽辞藻（bible 是工具不是文学）"""


BIBLE_UPDATE_TASK = """## 任务
从第 {chapter_idx} 章正文中抽取增量 BibleUpdate。

## 当前 WorldBible（前 {prev_ch} 章累积）
```json
{bible_json}
```

## 第 {chapter_idx} 章 · 《{chapter_title}》正文
```
{chapter_content}
```

## 本章 L2 梗概（对照用）
{l2_summary}
伏笔埋：{foreshadow_planted}
伏笔兑：{foreshadow_paid}

## 要求
1. `chapter_index` 填 {chapter_idx}
2. `new_characters`：本章**首次登场**的角色。每个给 name/traits/arc_state/last_appeared_in={chapter_idx}
3. `character_updates`：已在 bible 的角色，本章有新动向的。只填有变化的字段
   - arc_state 更新（从"怀疑期"→"对峙期"之类）
   - notable_events 追加本章发生的事（≤20 字/条）
   - last_appeared_in = {chapter_idx}
4. `new_facts`：本章确立的硬设定（规则 / 新场所 / 关键物件 / 关系）
5. `timeline_additions`：本章大事年表一句话描述（≤25 字，如"主角在城隍庙对质父亲"）
6. `new_foreshadow`：本章埋的、后面需要兑现的钩子（≤30 字/条）
7. `paid_foreshadow`：本章兑现的、来自前面章节的伏笔——**字面要尽量匹配 bible.active_foreshadow 里的原文**，让下一层能精确移除
8. `consistency_issues`：本章和 bible 矛盾的具体地方（如"bible 说 X 不会飞，本章 X 飞了"）。没有就空数组

### V5 必填增量字段（**若本章有相关变化，不得留空**）

9. `object_state_changes`：本章对 bible.tracked_objects 里任一物件**状态的变化**
   - 格式 `[{{"name": "三碗酒", "current_state": "左碗裂开", "last_changed_ch": {chapter_idx}}}]`
   - 只写本章真正发生状态变化的物件；没变化的不填
   - **name 必须与 bible.tracked_objects 里的 name 完全一致**

10. `character_status_changes`：本章对已有角色的**存续状态变化**
    - 角色消失 / 变模糊 / 只存在于回忆时：status=fading 或 gone，reliability 相应降
    - 格式 `[{{"name": "沈父", "status": "gone", "reliability": 0.5, ...}}]`
    - name 必须匹配 bible.characters 里的 name

11. `visual_anchors_fulfilled`：本章实现了哪些 visual_anchors
    - 从 bible.visual_anchors 列表里**逐字抄出**已兑现的条目（必须字面匹配）
    - 判断标准：正文里**真的写出了这个视觉画面**（不是暗示、不是概括）
    - 例：bible 有"父亲化作泥塑上的裂纹"；正文正好写了这个画面 → 填进这里
    - 若本章未兑现任何 anchor，留空数组

### V6 必填增量字段（**若本章有 plot_promise 进展，不得留空**）

12. `promise_setups_done`：本章**真正埋设**了哪些 plot_promise（用 bible 里的 id）
    - 判断标准：正文里具体写出了**埋设动作**（不是暗示、不是概括）
    - 围棋例：若正文出现"白72手在左下角留下看似失误的死子"→ 填 ["fs_1"]
    - 若本章没埋任何承诺，留空数组

13. `promise_payoffs_done`：本章**真正引爆**了哪些 plot_promise（用 bible 里的 id）
    - 判断标准：正文里具体写出了**引爆动作**并回指 setup（不是模糊的"一击毙命"）
    - 围棋例：若正文出现"沈清激活第 1 章的死子，破了顾衍之的劫"→ 填 ["fs_1"]
    - 若本章没兑现任何承诺，留空数组

## 输出
严格 JSON：

```json
{schema}
```

只写本章**真的新增/有变化的**条目。没变化的字段留空数组。
"""


def bible_update_prompt(state: NovelState, chapter_idx: int) -> str:
    l3 = next(d for d in state.l3 if d.index == chapter_idx)
    l2 = next(c for c in state.l2 if c.index == chapter_idx)
    bible = state.world_bible or WorldBible()
    planted = "；".join(l2.foreshadow_planted) if l2.foreshadow_planted else "（L2 未标注）"
    paid = "；".join(l2.foreshadow_paid) if l2.foreshadow_paid else "（L2 未标注）"
    return BIBLE_UPDATE_SYSTEM + "\n\n" + BIBLE_UPDATE_TASK.format(
        chapter_idx=chapter_idx,
        prev_ch=bible.last_updated_ch,
        bible_json=bible.model_dump_json(indent=2),
        chapter_title=l2.title,
        chapter_content=l3.content,
        l2_summary=l2.summary,
        foreshadow_planted=planted,
        foreshadow_paid=paid,
        schema=schema_of(BibleUpdate),
    )


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

    # V5: 视觉锚点强制检查块
    v5_block = ""
    if _v5_active(state) and state.world_bible is not None:
        wb = state.world_bible
        if wb.visual_anchors:
            fulfilled = set(wb.fulfilled_anchors)
            unfulfilled = [a for a in wb.visual_anchors if a not in fulfilled]
            v5_block = (
                "\n\n## 🎯 V5 · Visual Anchors 强制检查（premise 必保视觉）\n\n"
                f"**premise 要求的 visual_anchors**（来自 L1 抽取）：\n"
                + "\n".join(f"- {a}" for a in wb.visual_anchors)
                + "\n\n**bible 已标记 fulfilled**：\n"
                + ("\n".join(f"- {a}" for a in wb.fulfilled_anchors) if wb.fulfilled_anchors else "- （无）")
                + "\n\n**尚未兑现 · 按理应出现在正文里**：\n"
                + ("\n".join(f"- {a}" for a in unfulfilled) if unfulfilled else "- （全部已兑现）")
                + "\n\n### 检查法\n"
                "1. 通读 full_markdown，对每条 anchor 做搜索 / 识别：正文**真的写出了这个视觉画面**吗？\n"
                "2. 只看「概括 / 暗示」不算兑现——必须是**具体可视化的段落**\n"
                "3. 把真正**未兑现**（bible 标了已 fulfilled 但正文找不到 / bible 没标但你判断缺失）的 anchors 填进 `unfulfilled_anchors` 字段\n"
                "4. **若 `unfulfilled_anchors` 非空**，必须 `usable=False, suspect_layer=L3, retry_hint` 里明确提到缺失项\n"
                "5. 若全部兑现，`unfulfilled_anchors=[]`，按原有逻辑判整体 usable\n"
            )

    # V6: 叙事承诺强制检查块
    v6_block = ""
    if _v6_active(state) and state.world_bible is not None:
        wb = state.world_bible
        if wb.plot_promises:
            unfulfilled_p = [p for p in wb.plot_promises if not p.fulfilled]
            v6_block = (
                "\n\n## 📜 V6 · Plot Promises 强制检查（premise 叙事承诺 / 情节机巧）\n\n"
                "**L1 产出的 plot_promises 清单**：\n"
                + "\n".join(f"- {p.id}: {p.content}" for p in wb.plot_promises)
                + "\n\n**bible 已标记 fulfilled**：\n"
                + ("\n".join(f"- {p.id}: {p.content}" for p in wb.plot_promises if p.fulfilled)
                   if any(p.fulfilled for p in wb.plot_promises) else "- （无）")
                + "\n\n**尚未兑现的承诺**（按理应该在正文里具体引爆）：\n"
                + ("\n".join(f"- {p.id}: {p.content}" for p in unfulfilled_p) if unfulfilled_p else "- （全部已兑现）")
                + "\n\n### 检查法\n"
                "1. 通读 full_markdown，对每条 promise 做识别：正文是否**具体用体裁专业术语**写出了"
                "承诺的机巧？（如围棋的「死子/官子/手筋」、悬疑的「不在场证明/物证链」）\n"
                "2. 只写「一招毙命」「突然醒悟」「真气喷血」式的模糊动作**不算兑现**——必须是**具体技术性描写**\n"
                "3. 把未兑现的 promise（bible 标了 fulfilled 但正文是模糊描写 / bible 标了未兑现）"
                "填进 `unfulfilled_promises` 字段\n"
                "4. **若 `unfulfilled_promises` 非空**，必须 `usable=False, suspect_layer=L3, retry_hint` 里"
                "明确提到缺失的 promise id 和内容\n"
                "5. 若全部兑现，`unfulfilled_promises=[]`\n"
            )

    return FINAL_AUDIT_SYSTEM + v5_block + v6_block + "\n\n" + FINAL_AUDIT_TASK.format(
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


# ============ V4: 场景分解层 + 多尺度连续性 ============

# ---- V4 helpers：多尺度 context 渲染（CNN 式） ----

def _find_prev_scene(state: NovelState, chapter_idx: int, scene_idx: int) -> L3SceneDraft | None:
    """找"上一场景"：同章的 scene_idx-1；如果 scene_idx=1，找上一章最后一个场景。"""
    if scene_idx > 1:
        prev = [s for s in state.l3_scenes
                if s.chapter_index == chapter_idx and s.scene_index == scene_idx - 1]
        return prev[0] if prev else None
    if chapter_idx > 1:
        prev_ch_scenes = sorted(
            [s for s in state.l3_scenes if s.chapter_index == chapter_idx - 1],
            key=lambda s: s.scene_index,
        )
        return prev_ch_scenes[-1] if prev_ch_scenes else None
    return None


def _prior_chapter_last_scenes(state: NovelState, chapter_idx: int, n: int = 3) -> list[SceneCard]:
    """取 chapter_idx 之前最近 n 章的最后一条 SceneCard。"""
    out: list[SceneCard] = []
    for ci in range(chapter_idx - 1, 0, -1):
        cards = sorted(
            [c for c in state.scene_cards if c.chapter_index == ci],
            key=lambda c: c.scene_index,
        )
        if cards:
            out.append(cards[-1])
        if len(out) >= n:
            break
    return list(reversed(out))   # 正序（老的在前）


def _scene_multi_scale_context(state: NovelState, chapter_idx: int, scene_idx: int) -> str:
    """CNN 式多尺度上下文：
    - 场景层（最高分辨率）：上一场景全文尾段 400 字
    - 章节层（中分辨率）：本章已写场景的 opening+closing 摘要
    - 全书层（低分辨率）：前 N 章的最后一个场景 closing 100 字
    让 LLM 在不同时间尺度上都能锚定节奏。
    """
    blocks: list[str] = []

    # —— 场景层：上一场景结尾 400 字（跨场景或跨章节）——
    prev_scene = _find_prev_scene(state, chapter_idx, scene_idx)
    if prev_scene:
        scope = "同章上一场景" if scene_idx > 1 else "上一章最后一场景"
        blocks.append(
            f"### 🔍 上一场景结尾（紧接这里开始写，**不得重启人物动作**）· {scope}\n"
            f"```\n{prev_scene.content[-400:]}\n```"
        )

    # —— 章节层：本章已写场景的节奏汇总 ——
    within_chapter = sorted(
        [s for s in state.l3_scenes
         if s.chapter_index == chapter_idx and s.scene_index < scene_idx],
        key=lambda s: s.scene_index,
    )
    if within_chapter:
        lines = []
        for s in within_chapter:
            head_snip = s.content[:60].replace("\n", " ")
            tail_snip = s.content[-60:].replace("\n", " ")
            lines.append(f"- 场景 {s.scene_index}：开『{head_snip}...』落『...{tail_snip}』")
        blocks.append("### 📊 本章前面场景节奏（避免重复同样的开场/落点套路）\n" + "\n".join(lines))

    # —— 全书层：前几章的收束 ——
    prior = _prior_chapter_last_scenes(state, chapter_idx, n=3)
    if prior:
        lines = [
            f"- 第 {sc.chapter_index} 章收束：「{sc.actual_closing[:100]}」"
            for sc in prior if sc.actual_closing
        ]
        if lines:
            blocks.append("### 🌐 前面章节的收束画面（全书尺度参考，不要复制）\n" + "\n".join(lines))

    if not blocks:
        return ""
    return "\n\n## 🎞️ 多尺度历史窗口\n\n" + "\n\n".join(blocks)


def _scene_card_block(state: NovelState, chapter_idx: int, scene_idx: int) -> str:
    """渲染本场景的 SceneOutline 设计。"""
    sl = next(x for x in state.scene_lists if x.chapter_index == chapter_idx)
    outline = next(s for s in sl.scenes if s.index == scene_idx)
    motifs = "、".join(outline.dominant_motifs) if outline.dominant_motifs else "（由你挑）"
    total_scenes = len(sl.scenes)
    return (
        f"\n\n## 🎬 本场景设计（L2.5 产出 · 场景 {scene_idx}/{total_scenes}）\n"
        f"- **目的**：{outline.purpose}\n"
        f"- **开场落点**：{outline.opening_beat}\n"
        f"- **结尾落点**：{outline.closing_beat}\n"
        f"- **核心意象/物件**：{motifs}\n"
        f"- **视角**：{outline.pov or '继承上一场景'}\n"
        f"- **目标字数**：{outline.approximate_words} 字（±20%）"
    )


# ---- V4 真实 prompt 模板 ----

L25_SYSTEM = """你是章节节奏设计师。你的任务不是写正文，是把 L2 梗概**拆成 3-5 个场景**，
让每个场景都有清晰的**开场落点**和**结尾落点**，场景之间有**明确转场逻辑**。

关键原则：
- 每个场景只承担一件事：一次对话 / 一次回忆 / 一次行动 / 一次转折。别把两件事塞一个场景。
- 第 1 场景必须**承接上一章末的画面或动作**（如果有上一章）。
- 最后一场景的 closing_beat 必须**落到本章 hook**（L2 已给）。
- 场景之间的过渡用**物件 / 时间 / 动作**（而不是抽象情绪 / 意识流）承接。
- 目标总字数 ≈ target_words_per_chapter；均分给场景但允许 ±20%。

**不要把场景写成正文**。你只产出设计（outline），L3 会按你的设计逐场景去写。"""


L25_TASK = """## 任务
为第 {chapter_idx} / {total_chapters} 章设计 {scenes_hint} 个场景（可在 3-5 范围浮动）。

## 本章 L2 梗概
```json
{l2_json}
```

## 上一章收束画面（L3 实际写的）
{prev_chapter_closing}

## 参考：前面章节的场景摘要（保证节奏不重复）
{prior_scene_summaries}

{v5_time_markers_block}

{v6_technical_block}

## 要求
- `chapter_index` = {chapter_idx}
- `scenes`：列表长度 3-5；每个 SceneOutline 含 index（1-based）/ purpose / opening_beat / closing_beat / dominant_motifs / pov / approximate_words{v5_scene_marker_req}{v6_scene_tech_req}
- `transition_notes`：列出 N-1 条跨场景转场逻辑（每条 ≤30 字，如"场景 2→3 用三枚铜钱的触感过渡"）
- 字数分配：所有场景 approximate_words 之和 ≈ {target_words}，最后一场景略长可放结尾

## 输出
严格 JSON：

```json
{schema}
```

**只输出 JSON**，不要 markdown 包裹，不要解释。
"""


V5_L25_TIME_MARKERS_BLOCK = """## ⏳ V5 · 全局时间轴锚点（跨章单调递进）

**本书前面章节已用过的 time_markers**（按出现顺序）：
{prior_markers}

**本章规划约束**：
- 本章每个 scene **必须**填 `time_marker`（短文本，如"鸡鸣前"/"第一声鸡鸣"/"第二声鸡鸣"/"天光微白"/"朝阳出山"）
- 本章所有 time_markers **单调递进不倒退**、**不与前面章节已用过的冲突**（不能"倒带"）
- 整本书的时间进度条只能**向前推进**，不得重启（例如已经用过"第三声鸡鸣"之后就不能再出"第一声鸡鸣"）
- 若本章剧情与时间无直接关系（如插叙 / 回忆），用 `time_marker="【静止：{{本章主事件}}】"` 格式
"""


V5_SCENE_MARKER_REQ = "；**必填 V5: `time_marker`**（从上面的全局时间轴选一个未用过的、且严格晚于前面章节已用的）"


V6_L25_TECHNICAL_BLOCK = """## 🔧 V6 · 技术性伏笔（Technical Setup / Payoff）

本书的叙事承诺账本（从 L1 产出，L2 已分配到章节）：
{plot_promises_rendered}

本章被 L2 分配到的 promise：
- setup: {chapter_setups}
- payoff: {chapter_payoffs}

## 场景级技术性伏笔约束
- 若本章被分配了某个 promise setup，**必须**把它落到某个具体 scene 的 `technical_setup` 字段（用体裁专业术语写清楚具体铺垫动作）
  - 围棋例：`technical_setup="白72手在左下角留下看似失误的死子（fs_1）"`
  - 悬疑例：`technical_setup="警局档案柜顶层留下贴着红点的文件夹（fs_2）"`
- 若本章被分配了 payoff，**必须**落到某个 scene 的 `technical_payoff` 字段，并**回指**对应的 setup 场景
  - 围棋例：`technical_payoff="激活第 1 章埋下的白72手死子，拔掉顾衍之的中盘"`
- `technical_setup/payoff` 均允许空字符串，但整章至少要满足 L2 的分配要求
"""


V6_SCENE_TECH_REQ = "；**V6 可选**: `technical_setup` / `technical_payoff`（若本章被分配了 promise 则必填）"


L3_SCENE_ANTI_COLD_OPEN = """
## ❄️ 禁止冷开场（硬约束）

**严禁**以下作为场景第一句：
1. 重新定位主角动作：如「X 指节攥得发白」「X 喉结滚动」「X 深吸一口气」
2. 天气/时间重述：如「天光微亮」「又是一个雨天」（除非上一场景没有这个信息）
3. 心理综述：如「他想了很多」「种种思绪涌上来」
4. 模板化景物：如「月光如水」「远处传来钟声」

**正确做法**：
- 从上一场景的最后一个**具体动作/物件/对白**直接往下演
- 第一句就要产生**新信息**（新场景 / 新人物 / 新对话 / 新发现）
- 如果场景切换需要转场，用**物件或动作**承接（如"他把三枚铜钱揣进衣襟时，庙门被推开了"）
"""


L3_SCENE_TASK = """## 任务
写第 {chapter_idx} 章 · 场景 {scene_idx}/{total_scenes} 的正文。

## L1 骨架（精简）
- 标题：{l1_title}
- 主角：{protagonist_name}（{protagonist_trait}）— 想要 {want}；需要 {need}
- 世界规则：{world_rules}

{multi_scale}

{scene_card}

## 要求
1. 严格按场景设计的 opening_beat / closing_beat / purpose 写
2. 目标字数 {target_words} ± 20%
3. 场景 + 动作 + 对话 + 内心独白四要素齐全，但不平均分配
4. **不要写章节标题 / 场景标号**，只写正文
5. **不要解决本场景之外的问题**（下个场景有下个场景的事）
6. 保持和上一场景的物件 / 时间 / 情绪**自然承接**（细节看上面"多尺度历史窗口"）

{anti_cold_open}

{retry_hint}

## 输出
严格 JSON：

```json
{schema}
```

字段填：
- `chapter_index` = {chapter_idx}；`scene_index` = {scene_idx}；`revision` = {revision}
- `content`：纯正文文本
- `word_count`：中文字符数（不含标点和空白）

## 🔴 JSON 格式铁律（违反会导致本场景作废）
- **第一个字符必须是 `{{`**；不要 markdown 包裹
- **`content` 里的换行用 `\\n`**（literal 两字符：反斜杠+n），不要真实换行
- **`content` 里的引号用中文 `「」`**，不要英文 `"` 或 `'`
- **不要出现孤立反斜杠 `\\ `**（反斜杠+空格）——会炸 JSON 解析器
"""


CONTINUITY_AUDIT_SYSTEM = """你是连续性审稿员。你只管一件事：**承接**。

你不评审单场景写得好不好（那是 logic / pace 的活），你只看：
1. 本章开场是否**承接上一章末段**的动作/意象（不是重述，不是重启，是延续）
2. 场景之间转场是否有**明确物理/时间连接**（不是硬切换话题）
3. 同一**物件**（如三枚铜钱、斗笠、泥胎）在场景之间的描述是否一致
4. **角色情绪**推进是否连续（不能场景切换时情绪重置）
5. 是否有**过度重复**的套路（如「指节攥得发白」在同一章出现 ≥ 3 次 → 扣分）

Score ≥ 0.7 视为通过。指出的 issues 要**具体到场景编号和引文**，否则没用。"""


CONTINUITY_AUDIT_TASK = """## 任务
审第 {chapter_idx} 章的跨场景/跨章节连续性。

## 上一章收束（对照承接）
{prev_chapter_closing}

## 本章完整正文（按场景顺序拼接）
```
{chapter_content}
```

## 本章场景设计（L2.5 规划的 opening_beat / closing_beat）
{scene_outlines_text}

## 检查清单（每条都给判断）
1. 本章第一句是否承接上一章最后动作/意象（而不是重启人物）？
2. 各场景之间转场是否自然（物件 / 时间 / 动作过渡）？有无"硬切"？
3. 共同物件（开元通宝 / 斗笠 / 陶碗 等）在场景间描述是否一致？有无矛盾？
4. 主角情绪是否连续推进？有无"场景切换后情绪归零"的情况？
5. 有无过度重复套路（如"指节攥得发白"出现 > 2 次）？

### V5 新增检查项（基于 bible 状态对照）

6. **time_marker 单调递进**：本章场景的 time_markers 是否一步步向前（不倒退、不复读）？
   是否与**上一章末的 time_marker** 衔接（上一章已到"第三声鸡鸣"，本章不得回到"鸡鸣前"）？
   正文里对时间/环境的描述是否与各场景的 time_marker 一致？
   有无"三声鸡鸣跑两轮"之类的回放式错误？

7. **tracked_objects 状态一致**：正文对 bible.tracked_objects 中每个物件的描写，
   是否与其 `current_state` 一致？（如 bible 记"三碗酒：左碗已裂"，正文**不得**说"三碗皆完好"）
   若本章对某物件产生了**新状态**，本审稿可通过 —— 但需留意是否矛盾于前面章节。

8. **visual_anchors 兑现时机**：bible.visual_anchors 里若有"本章剧情应当兑现"的
   （如 bible 列着"父亲化作泥塑裂纹"而本章正好是父亲离场章），**正文是否真写出来**？
   若合适兑现而未兑现 → 直接扣分。

### V6 新增检查项（叙事承诺 + 阵营 + 技术性伏笔）

9. **plot_promises 兑现**：bible.plot_promises 里若本章被分配了 setup 或 payoff（见 L2 的 promise_setups/payoffs），
   正文**是否具体实现**？（不是概括，是用体裁术语写出具体动作）
   - 围棋题材：setup"白72手死子"是否真出现了具体**棋位**和**招法**？
   - 悬疑题材：payoff"指认凶手"是否真给了具体**物证**和**逻辑链**？
   - 若 L2 分配的 promise 在正文中找不到具体落地 → 直接扣分。

10. **角色阵营一致**：bible.characters 里每个有 `faction` 的角色，本章正文描写时
    **是否与其阵营一致**？（同名角色不得改阵营；多股势力的模糊称谓如"暗桩""内线"必须通过服色/标记显式区分）
    - 反例：第 1 章"灰衣暗桩"是顾府，第 3 章又出现"灰衣暗桩"帮助主角 → 阵营矛盾。

11. **technical_setup/payoff 具体性**：本章 scene_outline 里若有 technical_setup/payoff，
    正文是否**真用体裁专业术语**写出来？（禁止用"真气/金光/喷血/气浪"等武侠万能模糊词替代
    本应是"棋理/死活/官子/手筋"等围棋专业术语）。

## 输出
严格 JSON：

```json
{schema}
```

`head` 固定 "continuity"；`passed` = (score >= 0.7)；
`issues` 每条 ≤40 字，**必须指明场景编号 + 引原文短片段**（如"场景 2 开头『沈砚指节攥得发白』与场景 1 末的『雨丝飘落』无物理承接"）；
`suggestions` 每条 ≤30 字，告诉下次重写该怎么承接。
"""


def l25_prompt(state: NovelState, chapter_idx: int) -> str:
    """V4 L2.5：把 L2 梗概拆成 3-5 个场景。"""
    ui = state.user_input
    l2 = next(c for c in state.l2 if c.index == chapter_idx)

    # 上一章的最后场景 closing（如果有）
    prev_closing = "（本章是第 1 章，无上一章收束）"
    prior = _prior_chapter_last_scenes(state, chapter_idx, n=1)
    if prior and prior[-1].actual_closing:
        pc = prior[-1]
        prev_closing = f"第 {pc.chapter_index} 章末场景收束：「{pc.actual_closing}」"

    # 前面 2 章的场景摘要（让 L2.5 避免重复节奏套路）
    prior_all = _prior_chapter_last_scenes(state, chapter_idx, n=2)
    if prior_all:
        scene_lines = []
        for sc in prior_all:
            o = sc.outline
            scene_lines.append(f"- 第 {sc.chapter_index} 章最后场景：目的={o.purpose}；开场={o.opening_beat}；结尾={o.closing_beat}")
        prior_summaries = "\n".join(scene_lines)
    else:
        prior_summaries = "（无）"

    retry = _retry_hint(state, f"L25_{chapter_idx}")

    # V5: 时间轴锚点块（单调递进约束）
    if _v5_active(state):
        v5_markers_block = V5_L25_TIME_MARKERS_BLOCK.format(
            prior_markers=_prior_time_markers_block(state),
        )
        v5_scene_marker_req = V5_SCENE_MARKER_REQ
    else:
        v5_markers_block = ""
        v5_scene_marker_req = ""

    # V6: 技术性伏笔要求块
    if _v6_active(state):
        wb = state.world_bible
        if wb and wb.plot_promises:
            promises_lines = "\n".join(
                f"- {p.id}: {p.content}" for p in wb.plot_promises
            )
        else:
            promises_lines = "（无 plot_promises）"
        chapter_setups = "、".join(getattr(l2, "promise_setups", [])) or "（无）"
        chapter_payoffs = "、".join(getattr(l2, "promise_payoffs", [])) or "（无）"
        v6_technical_block = V6_L25_TECHNICAL_BLOCK.format(
            plot_promises_rendered=promises_lines,
            chapter_setups=chapter_setups,
            chapter_payoffs=chapter_payoffs,
        )
        v6_scene_tech_req = V6_SCENE_TECH_REQ
    else:
        v6_technical_block = ""
        v6_scene_tech_req = ""

    return (
        _CREATIVITY_HEADER.get(ui.creativity, "")
        + _lang_meta_header(ui.language)
        + L25_SYSTEM
        + _bible_context_block(state.world_bible, "L2")
        + "\n\n"
        + L25_TASK.format(
            chapter_idx=chapter_idx,
            total_chapters=ui.chapter_count,
            scenes_hint=ui.scenes_per_chapter_hint,
            l2_json=l2.model_dump_json(indent=2),
            prev_chapter_closing=prev_closing,
            prior_scene_summaries=prior_summaries,
            target_words=ui.target_words_per_chapter,
            schema=schema_of(ChapterSceneList),
            v5_time_markers_block=v5_markers_block,
            v5_scene_marker_req=v5_scene_marker_req,
            v6_technical_block=v6_technical_block,
            v6_scene_tech_req=v6_scene_tech_req,
        )
        + (f"\n\n## 上轮审稿反馈（请针对性修正）\n{retry}" if retry else "")
        + _final_audit_retry_block(state)
    )


def l3_scene_prompt(state: NovelState, chapter_idx: int, scene_idx: int) -> str:
    """V4 L3 单场景写作 · 多尺度上下文注入。V5 再加 time_marker / tracked_objects / status 注入。"""
    sl = next(x for x in state.scene_lists if x.chapter_index == chapter_idx)
    outline = next(s for s in sl.scenes if s.index == scene_idx)
    total_scenes = len(sl.scenes)
    l1 = state.l1
    cur_scene_draft = next((d for d in state.l3_scenes
                             if d.chapter_index == chapter_idx and d.scene_index == scene_idx), None)
    revision = cur_scene_draft.revision if cur_scene_draft else 0

    # 灵感库 RAG：用场景 purpose + opening_beat 做 query
    mini_l2 = type("MiniL2", (), {"summary": outline.purpose, "hook": outline.closing_beat})()
    inspiration_block = _inspiration_few_shot(state, mini_l2)

    multi_scale = _scene_multi_scale_context(state, chapter_idx, scene_idx)
    scene_card = _scene_card_block(state, chapter_idx, scene_idx)
    pv = state.user_input.pipeline_version
    bible_block = _bible_context_block(state.world_bible, "L3") if pv in ("v3", "v4", "v5", "v6") else ""
    retry = _retry_hint(state, f"L3_{chapter_idx}_{scene_idx}")
    final_audit_block = _final_audit_retry_block(state)

    # V5: 时间轴硬约束 + 物件状态 + 角色存续 + 未兑现锚点
    v5_blocks = ""
    if _v5_active(state):
        v5_time_block = (
            f"\n\n## 🎯 V5 本场 time_marker（硬约束）\n"
            f"- **本场必须落在**：`{outline.time_marker or '（L2.5 未指定——按本章当前时段写）'}`\n"
            f"- **禁止推进超过此 marker**：不得写出比该锚点更晚的环境变化（如当前是「第一声鸡鸣」，禁止出现「天已大亮」）\n"
            f"- **禁止回退早于此 marker**：不得描写比该锚点更早的环境状态\n"
            f"- 全书时间轴已用过的 marker（这些是前面章节的，本场不得重启任何一个）：\n"
            f"  {_prior_time_markers_block(state)}"
        )
        v5_blocks = (
            v5_time_block
            + _render_tracked_objects_block(state)
            + _character_status_hints_block(state)
            + _unfulfilled_anchors_block(state)
        )

    # V6: 叙事承诺账本 + 本章分配 + 阵营图谱 + 本场技术性伏笔
    v6_blocks = ""
    if _v6_active(state):
        v6_blocks = (
            _render_plot_promises_block(state)
            + _chapter_promise_assignments_block(state, chapter_idx)
            + _character_faction_block(state)
            + _scene_technical_block(outline)
        )

    return (
        l3_system_for(state.user_input.genre, state.user_input.language, state.user_input.creativity)
        + inspiration_block
        + bible_block
        + v5_blocks
        + v6_blocks
        + final_audit_block
        + "\n\n"
        + L3_SCENE_TASK.format(
            chapter_idx=chapter_idx,
            scene_idx=scene_idx,
            total_scenes=total_scenes,
            l1_title=l1.title,
            protagonist_name=l1.protagonist.name,
            protagonist_trait="、".join(l1.protagonist.traits),
            want=l1.protagonist.want,
            need=l1.protagonist.need,
            world_rules="；".join(l1.world_rules),
            multi_scale=multi_scale,
            scene_card=scene_card,
            target_words=outline.approximate_words,
            anti_cold_open=L3_SCENE_ANTI_COLD_OPEN,
            retry_hint=(f"## 上轮审稿反馈\n{retry}" if retry else ""),
            schema=schema_of(L3SceneDraft),
            revision=revision,
        )
    )


def continuity_audit_prompt(state: NovelState, target_idx: int) -> str:
    """V4 continuity 审头：跨场景/跨章节承接质量。"""
    from .state import AuditReport
    # 拼接本章全部场景
    chapter_scenes = sorted(
        [s for s in state.l3_scenes if s.chapter_index == target_idx],
        key=lambda s: s.scene_index,
    )
    chapter_content = "\n\n--- 场景分隔 ---\n\n".join(
        f"【场景 {s.scene_index}】\n{s.content}" for s in chapter_scenes
    )

    # 上一章收束
    prior = _prior_chapter_last_scenes(state, target_idx, n=1)
    if prior and prior[-1].actual_closing:
        prev_closing = f"第 {prior[-1].chapter_index} 章收束：「{prior[-1].actual_closing}」"
    else:
        prev_closing = "（本章是第 1 章，无上一章可对照）"

    # 本章 L2.5 场景设计（V5 后：包含 time_marker）
    sl = next((x for x in state.scene_lists if x.chapter_index == target_idx), None)
    if sl:
        outlines_text = "\n".join(
            f"- 场景 {o.index}：开场「{o.opening_beat}」→ 结尾「{o.closing_beat}」（{o.purpose}）"
            + (f"  [V5 time_marker: {o.time_marker}]" if o.time_marker else "")
            for o in sl.scenes
        )
    else:
        outlines_text = "（无场景设计）"

    # V5: 注入 bible 状态供审稿对照
    v5_bible_context = ""
    if _v5_active(state) and state.world_bible is not None:
        wb = state.world_bible
        parts = []
        if wb.time_markers_used:
            parts.append(f"- **全书时间轴已用 markers**（按顺序）：{'、'.join(wb.time_markers_used)}")
        if wb.tracked_objects:
            obj_lines = [f"  - {o.name}：{o.current_state}" for o in wb.tracked_objects]
            parts.append("- **被追踪物件当前状态**（正文描写必须对齐）：\n" + "\n".join(obj_lines))
        if wb.visual_anchors:
            fulfilled = set(wb.fulfilled_anchors)
            unfulfilled = [a for a in wb.visual_anchors if a not in fulfilled]
            if unfulfilled:
                parts.append(f"- **尚未兑现的 visual_anchors**（本章若合适应兑现）：{'、'.join(unfulfilled)}")
        if parts:
            v5_bible_context = "\n\n## 📊 V5 · Bible 状态对照组\n" + "\n".join(parts)

    return CONTINUITY_AUDIT_SYSTEM + v5_bible_context + "\n\n" + CONTINUITY_AUDIT_TASK.format(
        chapter_idx=target_idx,
        prev_chapter_closing=prev_closing,
        chapter_content=chapter_content,
        scene_outlines_text=outlines_text,
        schema=schema_of(AuditReport),
    )
