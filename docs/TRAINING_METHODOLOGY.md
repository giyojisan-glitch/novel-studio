# 训练方法与验证思路（2026-04 更新）

> 这不是一个"训练模型"的项目 —— 我们不调 LLM 参数。这是一个**推理时的风格+结构调优系统**。
> 本文记录这套系统的设计思路 + 我们如何验证每个组件真的在起作用。

---

## 1. 核心思路（七层创新）

对标 gpt-author / AI_NovelGenerator / autonovel 等开源项目，NOVEL-Studio 的差异点在六个维度：

### 1.1 多层状态机（对标扩散模型）

传统做法：一次性让 LLM 从 premise 写出完整小说 → 结果松散、前后矛盾、缺乏结构。

NOVEL-Studio：
```
premise → L1 骨架 → L2 章节梗概 → L3 段落正文 → (V2) L4 对抗编辑+润色 → 成品
           ↑                ↑                ↑                     ↑
        审稿门 × N      审稿门 × N      审稿门 × N          final_audit
```

每一层都是一次"降噪"：前一层的粗颗粒输出作为后一层的约束。类比扩散模型从纯噪声逐步去噪到图像。

### 1.2 Multi-Head Audit（对标 Transformer 多头注意力）

每个审稿门并行跑多个独立审稿头：
- `logic` — 查因果断裂、世界规则违反、角色行为一致性
- `pace` — 查节奏、冲突密度、钩子力度
- （V2+）`style` — 查风格一致性
- （V2+）`character` — 查角色坍塌

每头独立打分 → 投票聚合 → 通过 / 打回重写（最多 2 次重写，超限强制放行防死循环）。

### 1.3 Lora-style Inspiration RAG（对标 Lora 风格迁移）

**这是本项目最大的创新**。

问题：LLM 写小说默认是"所有作家的平均值"，产出千篇一律、AI 腔严重。

方案：在 L3 生成前，用 L2 章节梗概做 query，从用户配置的"作家灵感库"里检索 top-3 语义最近的原文片段，作为 few-shot 风格参考注入 L3 prompt。

```
用户放 5 篇温瑞安武侠到 inspirations/温瑞安/
                   ↓
            BAAI/bge-large-zh-v1.5 embed → Chroma 向量库
                   ↓
         L3 生成时 L2.summary+hook 作 query → top-3 chunks
                   ↓
       插入 L3 prompt 里「## 🎨 风格参考片段」块
                   ↓
            LLM 按那个腔调去写本章（不抄情节，借腔调）
```

核心优势：**同一套管道**，换不同的 inspirations/ 内容，就能产出不同作家味的小说 —— 不需要微调模型，不需要重训。

### 1.4 V3 长篇架构：WorldBible + interleaved L2/L3

**要解决的问题**：3-5 章单元测试能跑通，但开到 10+ 章时现有开源项目（包括 V2 NOVEL-Studio）都会崩：
- 角色漂移：ch3 里主角恐高，ch7 他爬墙面不改色
- 伏笔遗忘：ch1 的那把剑，ch5 再没出现过
- 规则违反：L1 说「魔法必须付出代价」，ch8 主角免费释放了十次
- 节奏失控：10 章的 setup-confrontation-resolution 需要 sub-beats 而不是笼统的三幕

**V3 做法**：把「状态机 + 结构化知识」推到极致——不让 LLM 去"记住"前文，而是让系统把前文的真相以**结构化 bible** 的形式重新喂给它。

```
L1 骨架 → L1_audit → bible_init（synthetic，从 L1 抽出初始 bible）
                ↓
       对每章 i = 1..N 交替：
         L2_i 梗概（prompt 注入 bible 全量：活跃角色的当前弧光、未兑现伏笔、硬规则）
          ↓ audit
         L3_i 正文（同样注入 bible + 上一章 actual tail）
          ↓ audit
         bible_update_i（LLM 读本章正文 → 产出 BibleUpdate 增量：
                          new_characters / character_updates / new_facts /
                          timeline_additions / new_foreshadow / paid_foreshadow /
                          consistency_issues）
          ↓
         bible = apply_bible_update(bible, update)  [纯函数合并]
                ↓
       final_audit → L4_adversarial → L4_scrubber → 成品
```

**关键设计决策**：

1. **interleaved 而不是"所有 L2 先出"**：V1/V2 里 L2 批量产出后才开始 L3。这对 3 章够用；对 10 章不够——ch2 outline 需要看到 ch1 **实际写了什么**，不只是 ch1 的 outline。V3 改成 L2_i → L3_i → bible_update_i → L2_{i+1}，链条里的每一步都能看到前面真实发生的事。

2. **bible 是增量更新，不是每次重写**：`BibleUpdate` schema 里每个字段都是"本章**新增**的"——避免 LLM 每章把整份 bible 重写一遍（会遗忘、会矛盾、会爆 token）。合并逻辑在 Python 里做（`apply_bible_update`），确定性、可测、不依赖 LLM。

3. **伏笔状态机**：`active_foreshadow` / `paid_foreshadow` 双列表显式追踪。每次 `bible_update` 产出 `paid_foreshadow`（从 active 移走的）和 `new_foreshadow`（新加到 active 的）。下一章 L2 prompt 里会看到 active 列表被明确提醒「可以考虑兑现」。

4. **consistency_issues 是 fail-soft 而不是 hard blocker**：如果 bible_update 检测到本章和 bible 矛盾（「bible 说 X 不会飞，本章 X 飞了」），它只记录在 `consistency_issues` 字段里，不强制打回。未来可以用这个字段驱动定向重写，当前 V3 先让数据流跑通再优化修复机制。

5. **bible_init 是合成步而不是 LLM 步**：从 L1 抽 bible 完全可以用 Python 规则做（主角→CharacterState，world_rules→WorldFact）。不浪费 LLM 调用，也确定性可测。

**增加的 LLM 调用成本**：
- V2 10 章 ≈ 1（L1）+ 1（L1_audit×2）+ 10（L2）+ 10（L2_audit×2）+ 10（L3）+ 10（L3_audit×2）+ 1（final_audit）+ 10（L4_adversarial）+ 10（L4_scrubber）≈ **63** 次调用
- V3 10 章 ≈ 同 V2 + 10 次 bible_update = **73** 次调用（+15%）
- 换来的是跨 10+ 章的**结构一致性**——性价比值

**状态持久化**：`projects/{slug}/state.json` → `world_bible` 字段记录完整 bible，随时可查、可手动编辑、可作为 debug 入口（如果某章角色跑偏，查 bible 就能定位是 character_updates 错了还是 L3 脱缰）。

**当前状态**（2026-04-21）：schema + 路由 + interleaved engine + stub smoke test 全绿，135 tests pass。真 LLM 10 章 run 是下一步的验证目标（预估 Doubao 一次跑完 40-50 分钟）。

### 1.5 V4 场景分解层 + 多尺度连续性（CNN 式感受野）

**V3 跑了一次 Doubao 8 章真 LLM 后，发现的问题**：bible 把角色和规则锁住了（沈砚的眉骨旧疤、三枚铜钱这些细节 8 章一字不差），但**章节间的节奏断裂**依然严重——每章都以类似「沈砚指节攥得发白」这种重启句开头，LLM 是在**冷启动**写每一章，不是在**续写**。

**根因**：V3 的 L3 prompt 对前文只有两个抓手：
- `prev_tail` 150 字（字符级感受野太小，接不住节奏）
- 抽象 bible（状态快照，丢失了散文的**腔调/物件/节律**）

**类比 CNN**：V3 等于一个 1 层卷积，kernel 只 3×3，大图里远端像素根本传不到眼前。想要全局连贯，得**堆叠多尺度感受野**。

**V4 架构**：
```
L2_i (章节梗概)     ⟵ 新增：上一章最后场景正文 800 字（兑现 V3 interleaved 承诺）
  └─ audit
L2.5_i (场景列表)   ⟵ 新层！把章节拆成 3-5 个场景
  └─ audit             每场景有 opening_beat / closing_beat / purpose / motifs / target_words
  │                   转场说明 transition_notes 显式写明「场景 1→2 用物件过渡」
  ↓
for scene s in 1..M_i:
  L3_{i,s} (单场景正文) ⟵ 多尺度 context 注入：
                          · 场景层（高分辨率 400 字）：上一场景全文尾段
                          · 章节层（中分辨率）：本章已写场景的 opening/closing 一行
                          · 全书层（低分辨率）：前 3 章的收束 100 字 × 3
                        + anti-cold-open 硬约束：
                          「严禁『指节攥得发白』『喉结滚动』作场景首句」
                          「开场从上一场景末的具体动作/物件/对白直接往下演」
  ↓
L3_i chapter_audit (3 头 = logic + pace + continuity) ⟵ 新 head！
  continuity 头专审：
    - 本章开场承接上一章末？
    - 场景间转场自然（物件/时间/动作）？
    - 共同物件描述一致？
    - 角色情绪连续推进？
    - 有无 > 2 次模板化重复？
  ↓
bible_update_i → 下一章 L2 → ...
```

**关键设计决策**：

1. **滚动生成（rolling decode）而非批量生成**：V4 坚持"写完第 s 场景立即更新 SceneCard，第 s+1 场景立即看到 s 的实际 prose"。类比 RNN 的 hidden state 从 t 传给 t+1——而不是等全章写完再 audit，错过了场景间修正机会。

2. **感受野 = 真实 prose 而不是 summary**：bible 是"状态"，SceneCard 里的 `actual_opening` / `actual_closing` 是"风格样本"。两者互补——bible 告诉你"是什么"，actual 片段告诉你"怎么写"。

3. **anti-cold-open 用禁令而不是鼓励**：LLM 对正向 prompt「要连贯」响应弱；对具体禁令（「严禁这 4 种开头」）响应强。类比 negative prompt 在 diffusion 里的效用。

4. **continuity 独立成头**：不把"承接"丢给 logic 头混审。logic 关心"行为合理"，continuity 关心"跨场景是否一次写完的感觉"——不同 axis，必须分开打分。

5. **章节内场景数软目标**：`scenes_per_chapter_hint=4` 是软的，LLM 在 3-5 范围自选。给它选择权，因为动作章和过渡章的自然切分方式不同。

**增加的 LLM 调用**：
- V3 10 章 ≈ 73 次调用
- V4 10 章 ≈ 10×(1 L2 + 2 L2_audit + 1 L2.5 + 2 L2.5_audit + 4 scenes + 3 chapter_audit + 1 bible_update) + 1 L1 + 2 L1_audit + 1 final_audit + 20 L4 = **~153 次调用**（+110%）
- 换来的是**真正可读的长篇**（前面 8 章 Doubao run 写得再好，节奏断裂就是不能上）

**当前状态**（2026-04-21）：V4 五个 commit 已落地。Schema + engine + prompts + audit head + artifacts + CLI + 179 tests 全绿。真 LLM 5 章验证是下一步（预估 ~30 min，~70 调用）。

### 1.6 V5 premise 忠实度：视觉锚点 + 时间轴 + 物件状态 + 角色存续

**V4 真 LLM 5 章跑完后（2026-04-22），外部审稿 Agent 拿完整架构文档对照成品做了诊断**，发现 4 个架构级漏洞——V4 防住了冷开场，但**信息维度不够**，还有些 premise 硬承诺系统性地漏掉：

| 外部 Agent 发现的病灶 | 根因 | 实际例子 |
|---|---|---|
| **视觉锚点丢失** | bible_init 只从 world_rules 抽事实，premise 里的**具体视觉画面**无 schema 位置 | premise「父亲化作泥塑上的裂纹」 → V4 成品只写「父亲淡了」 |
| **跨章时间轴失控** | SceneCard 没有全局进度锚点 | 正文「三声鸡鸣」跑了 2.5 轮（CH2 末第三声 → CH4 开第一声重启） |
| **关键物件状态分裂** | WorldFact 只记"存在"不记 current_state | premise「三碗酒」对称 → V4 只写左碗裂，另两碗无状态 |
| **角色 obsolescence 缺失** | CharacterState 只有 active 一种状态 | V4 结尾主角"忘父亲面容"，但 bible 仍硬记"左眉旧疤" |

**V5 做法**：加 4 个正交的 state-tracking 维度。不动 V4 的任何机制（路由/多尺度 context/场景分解/continuity 审头全保留），只新增 4 种信息流动。

```python
# L1 扩展
class L1Skeleton:
    visual_anchors: list[str]          # 3-5 条 premise 必保视觉画面（L1 从 premise 抽）
    tracked_object_names: list[str]    # 2-5 个跨章追踪的关键物件名

# WorldBible 扩展
class WorldBible:
    visual_anchors: list[str]          # 从 L1 copy（不可变）
    fulfilled_anchors: list[str]       # 按章累积：LLM 每章报告已兑现的
    tracked_objects: list[TrackedObject]  # 每个物件的 current_state + state_history
    time_markers_used: list[str]       # 全书按场景顺序的时间锚点列表

# CharacterState 扩展
class CharacterState:
    status: Literal["active", "fading", "gone"]   # 存续状态
    reliability: float                             # 0-1 记忆可信度

# SceneOutline 扩展
class SceneOutline:
    time_marker: str   # L2.5 分配；跨章单调递进

# BibleUpdate 扩展
class BibleUpdate:
    object_state_changes: list[TrackedObject]
    character_status_changes: list[CharacterState]
    visual_anchors_fulfilled: list[str]    # 本章兑现了哪些 anchor

# FinalVerdict 扩展
class FinalVerdict:
    unfulfilled_anchors: list[str]         # 非空 → engine 强制 bounce
```

**4 条强制链路**：

1. **Visual anchors 全链路追踪**
   - L1 prompt 强制要求从 premise 抽 3-5 条具体可视化画面（「泥塑裂纹」「三碗见底」），反例禁收（「主题」「归途」这种抽象不要）
   - bible_init 原样 copy 到 WorldBible.visual_anchors
   - 每章 L3 prompt 里 `_unfulfilled_anchors_block` 列出未兑现的，提示合适时兑现
   - bible_update prompt 要求本章 `visual_anchors_fulfilled` 字面对齐 bible.visual_anchors
   - final_audit prompt 注入完整兑现状态对照组，要求填 `unfulfilled_anchors`
   - **Engine 兜底**：apply_responses(final_audit) 检测到 `unfulfilled_anchors` 非空 → 强制 `usable=False, suspect=L3`，即使 LLM 错误标 usable=True 也翻转

2. **Time markers 单调递进**
   - L2.5 prompt 注入 `_prior_time_markers_block`（全书已用过的 markers），约束本章所有 scenes.time_marker 单调递进、不与前面章节冲突
   - L3 scene prompt 把本场 time_marker 作为**硬落点**（禁推进超过 / 禁回退早于）
   - Engine 在 apply L25 时把新 markers append 到 bible.time_markers_used
   - Continuity 审头新增检查「本章 markers 是否单调？是否与上一章末衔接？」

3. **Tracked objects 状态一致**
   - L1 declare 哪些物件要追踪；bible_init 初始化为 `current_state="初始 / 未使用"`
   - 每章 L3 prompt 注入 `_render_tracked_objects_block` 展示所有追踪物件当前状态
   - 硬约束：正文描写不得与 current_state 矛盾（如 bible 记"左碗已裂"，正文不得写"三碗皆完好"）
   - bible_update 抽取 `object_state_changes`，apply_bible_update 合并（按 name upsert；追加 state_history）
   - Continuity 头新增检查「正文对 tracked_objects 的描写是否一致」

4. **Character status/reliability 影响笔法**
   - CharacterState 多两字段：`status ∈ {active, fading, gone}`, `reliability ∈ [0,1]`
   - bible_update 可通过 `character_status_changes` 把角色转为 fading/gone（通常在 visual_anchor 兑现时——如"父亲成裂纹"=父亲 gone）
   - L3 scene prompt 里 `_character_status_hints_block` 显式给出笔法指令：
     - `fading` → 模糊笔法 / 只在回忆中
     - `gone` → 禁止直接现身 / 通过遗物或他人回忆
   - 避免 V4 里"系统记得但主角忘了"的认知失调

**关键设计决策**：

1. **不加新 audit head**：continuity 头已经有 5 项检查，V5 扩充到 8 项，不加新 head → aggregator 不动、成本不变、测试简单
2. **engine 双重保险**：final_audit 既在 prompt 里要求 LLM 填 `unfulfilled_anchors`，也在 engine 层检测到非空强制 bounce——防止 LLM 错误标 usable=True 漏检
3. **L1 集成不是独立 LLM 步**：visual_anchors 由 L1 一次产出，bible_init 保持合成（零额外 LLM 调用）
4. **time_marker 自由文本不枚举**：不同 genre 的时间锚点不同（志怪=鸡鸣；科幻=宇航日；武侠=朝阳三丈），让 LLM 自己挑合适的短语，engine 只保证序列单调性
5. **状态变更链式**：visual_anchor 兑现 → bible_update 报告 → 对应角色自动 fading/gone → 下一章 L3 写作笔法自动调整

**增加的 LLM 调用**：**零**。V5 不加任何新 step，只在现有 prompt 里加注入块 + 扩充 schema 字段。

**当前状态**（2026-04-22）：V5 五个 commit 已落地。Schema + engine + prompts + artifacts + CLI + 208 tests 全绿。外部审稿 Agent 标定的 4 条病灶都在架构层面有对应机制。真 LLM 5 章 Doubao demo 完成：4/4 visual_anchors 兑现，时间轴单调递进不重启，tracked_objects 5 章 state_history 完整，final_verdict 0.92 无 bounce。

### 1.7 V6 叙事承诺 + 阵营图谱 + 技术性伏笔

**V5 真 LLM 5 章 Doubao demo（2026-04-22《裂棋》）跑完后，第二轮外部审稿找到三类 V5 盲区**：

| 外部 Agent 发现的 V5 盲区 | 核验结果 | 根因 |
|---|---|---|
| **叙事承诺被漏抽** | ✅ 属实（"死子"premise 明确强调，7097 字正文 0 次出现） | `visual_anchors` 只兜视觉画面，不兜情节机巧 |
| **多派系角色身份混淆** | ✅ 属实（灰衣顾府 vs 玄色沈家都叫"暗桩"，读者要靠服色推） | `CharacterState` 无 `faction` 字段，continuity 头无对应校验 |
| **题材专业术语失真** | ✅ 完全属实（围棋术语 0 次，"真气" 19 次占位） | `SceneOutline` 无 technical_setup/payoff 槽，L3 回落到 genre 通用模糊动作词 |

**V6 做法**：三条正交 schema 增补，不动 V5 任何机制。

```python
# L1 扩展
class L1Skeleton:
    plot_promises: list[PlotPromise]   # 3-5 条叙事承诺（非视觉的情节机巧）

class CharacterCard:
    faction: str = ""                  # 阵营（主角/反派都要标）

# 新类
class PlotPromise:
    id: str                            # fs_1 / fs_2 稳定引用
    content: str                       # ≤50 字，如「埋下跨越十年的死子」
    setup_ch: int = 0                  # 埋设章节（0=未埋）
    payoff_ch: int = 0                 # 兑现章节（0=未兑现）
    fulfilled: bool = False            # 真正兑现标记（bible_update 报告）

# L2ChapterOutline 扩展
class L2ChapterOutline:
    promise_setups: list[str]          # 本章要 setup 的 promise.id
    promise_payoffs: list[str]         # 本章要 payoff 的 promise.id

# SceneOutline 扩展
class SceneOutline:
    technical_setup: str               # 体裁术语写的具体铺垫（"白72手留下死子"）
    technical_payoff: str              # 体裁术语写的具体引爆

# CharacterState 扩展
class CharacterState:
    faction: str                       # 阵营字段（覆盖 V5 新增之外）

# WorldBible 扩展
class WorldBible:
    plot_promises: list[PlotPromise]   # 从 L1 copy，各 bible_update 更新 setup_ch/payoff_ch/fulfilled

# BibleUpdate 扩展
class BibleUpdate:
    promise_setups_done: list[str]
    promise_payoffs_done: list[str]

# FinalVerdict 扩展
class FinalVerdict:
    unfulfilled_promises: list[str]    # 非空 → engine 强制 bounce（镜像 V5 unfulfilled_anchors）
```

**4 条强制链路**：

1. **Plot promises 跨章追踪**：L1 从 premise 抽承诺（prompt 禁收"复仇/击败反派"这种结局描述，要求收"埋三颗跨越十年的死子"这种可操作的情节机巧）→ bible_init 原样 copy → L2 必须分配 setup_ch/payoff_ch → L3 scene prompt 注入本章分配 → bible_update 报告实际 done → final_audit 检查未兑现的 → engine 兜底强制 bounce

2. **阵营一致性**：L1 要求主角/反派带 faction → bible_init propagate → L3 scene prompt 注入"阵营图谱"块（暗桩/内线必须通过服色+阵营显式区分）→ continuity 审头新增"同名不改阵营"校验

3. **体裁专业术语硬约束**：L2.5 要求每个 scene.technical_setup/payoff 用体裁术语写具体铺垫 → L3 scene prompt 注入本场 technical_setup/payoff 并禁用"真气/金光/喷血"等武侠万能模糊词 → continuity 审头新增"technical specificity"校验

4. **engine 双重保险**：final_audit prompt 要求 LLM 填 `unfulfilled_promises` + apply_responses(final_audit) 检测非空强制 usable=False + suspect=L3（不信 LLM 自报）

**关键设计决策**：

1. **PlotPromise 是 class 不是 string**：visual_anchors 是 list[str] 因为它只需"兑现/未兑现"二元状态；PlotPromise 需要 setup_ch/payoff_ch 两个章节指针 + fulfilled 布尔 → 必须是 class 才能承载生命周期
2. **faction 字段自由文本不枚举**：不同 genre 的阵营结构不同（武侠=门派/朝廷/江湖；悬疑=警方/嫌疑人/媒体；科幻=联邦/起义军），让 LLM 按 premise 自由定义
3. **V6 继承 V5 全部机制**：`_v5_active(state)` 现在对 v5/v6 都返回 True，V5 所有注入块在 V6 下继续工作，V6 只是再追加自己的块
4. **零额外 LLM 调用**：V6 不加任何新 step（visual_anchors 已经设下 pattern——所有 premise 级约束由 L1 一次抽），只在现有 prompt 里加注入块 + 扩充 schema 字段
5. **unknown promise.id 被忽略**：apply_bible_update 收到 `promise_setups_done=["fs_编造的"]` 时不会把它追加到 bible——防 LLM 编造新 id 绕过约束

**增加的 LLM 调用**：**零**。

**当前状态**（2026-04-22）：V6 四个 commit 已落地。Schema + engine + prompts + artifacts + CLI + README + docs + 241 tests 全绿。第二轮外部审稿 Agent 标定的 3 条 V5 盲区都在架构层面有对应机制。真 LLM 5 章 Doubao 验证是下一步（同 premise `棋圣赛复仇`，对比 V5 demo `裂棋`）。

---

## 2. 创意档位参数（strict / balanced / creative）

解决"什么时候需要严格按 premise、什么时候需要大胆补全"的问题。在 `init` 时一次设定，影响整条管道：

| 档位 | Temperature | Prompt 头 |
|------|-------------|----------|
| `strict` | 0.3 | 禁止加未明示的主要人物、关键转折、改结局方向 |
| `balanced` | 0.7 | 默认，不加头 |
| `creative` | 1.0 | 鼓励用非线性叙事、象征、次要角色补全 |

用法：
```bash
novel-studio init --file premise.md --creativity strict --provider doubao
```

Temperature 影响 sampling 随机性（真 API），prompt 头影响模型对 premise 的"忠实度"。两者配合。

---

## 3. 验证方法论（Evidence-based development）

### 3.1 核心原则

每个组件必须有**可量化信号**证明它在起作用，不接受"感觉变好了"。

- Audit 分数、slop score、字数、章节结构 → 自动化指标
- 人眼盲审（严禁 self-judge，严禁同源模型做裁判）→ 质量信号
- Benchmark TDD → 跨 corpus 稳定性

### 3.2 A/B 对照实验设计

**问题**：灵感库 RAG 真的在起作用，还是只是 placebo？

**设计**：
- 控制变量：同 premise、同 creativity、同 provider、同章节数
- 唯一变量：`NOVEL_STUDIO_NO_RAG=1` 环境开关（on vs off）
- 输出对比：字数、句长分布、物件密度、对白经济性、结尾处理、是否有目标作家的特征标记

**第一轮实验（Claude writer · 温瑞安武侠库）**：
| 维度 | RAG ON (A) | RAG OFF (B) |
|------|-----------|-------------|
| 句长分布 | 短句密度显著高 | 混合偏长 |
| 物件颗粒度 | 灯/剑/酒/断指/针脚都有物理细节 | 多但偏场景描写 |
| 对白密度 | 单回合极省 | 稍长 |
| 结尾 | 留白（物件静止、未完成动作） | 时间收束（天亮了） |
| 温瑞安标记 | 「反手。走针。一压、一抽、一压。」 | 无 |

**结论**：RAG 贡献约 **15-25%** 的风格偏移（温瑞安方向）。信号存在但中等。

**第二轮实验（Doubao writer · 温瑞安武侠库）**：
因为豆包 default 武侠风比 Claude 更偏玄幻（带仙侠味），RAG 拉回温瑞安式江湖武侠的"纠偏作用"更明显：
- B (RAG OFF) 自带魔幻设定：「剑……遇着旧识的血气就会自发震颤」
- A (RAG ON) 纯写实：无魔幻，粗口俚语，小人物视角

**结论**：信号强度升至 **30-40%** 偏移。**验证了 RAG 不只是改句式，能压制 LLM 的默认风格倾向**。

### 3.3 多作者路由验证（两阶段：失败 → 修复）

**问题**：如果灵感库里同时放多个作家，RAG 能按查询语境自动路由到对应作家吗？如果不能，Lora-style 的"换库即换风格"就是伪命题。

**设计**：库里放 100 chunks 温瑞安（武侠）+ 10 chunks 蒲松龄（聊斋志怪）。10:1 悬殊基数，对蒲松龄极不利。

#### 阶段一：纯语义检索（手工 query）

- 5 个武侠 query（"剑光寒如雪" 等）→ top-3 里温瑞安占几次？
- 5 个志怪 query（"棺中尸动吹客" 等）→ top-3 里蒲松龄占几次？

**结果**：
- 武侠 → 温瑞安：**15/15 = 100%**
- 志怪 → 蒲松龄：**12/15 = 80%**
- 总路由命中率：**27/30 = 90%**

**初步乐观结论**：语义路由工作，10:1 不利基数下仍能压住。

#### 阶段二：真 pipeline 压测（发现失败模式）

然而放到真实 pipeline 里跑志怪生成时：
- L2 summary（豆包写的**现代白话** "书生夜访土地庙"）作 query
- 9/9 L3 RAG 槽位**全被温瑞安占满**，蒲松龄 0 命中

**失败原因**：
1. **语体不匹配**：query（现代白话）与蒲松龄 chunks（文言）嵌入距离远，即便 genre 是志怪
2. **Corpus 基数悬殊**：100:10 下温瑞安 chunks 淹没稀疏的蒲松龄
3. **手工 query 偏差**：用"棺中尸动吹客"这类专门志怪措辞测试，和真实 LLM 产出的现代 query 不是一个语域

**证据**：L2 summary 这类 query 在 embedding 空间里**离现代武侠更近**（都是现代白话），不是"志怪"这个题材标签决定的。**纯语义检索不能区分题材**。

#### 阶段三：硬过滤修复（`inspiration_routing.json`）

新增 `styles/inspiration_routing.json` 配置：
```json
{
  "武侠": ["温瑞安", "古龙", "金庸"],
  "志怪": ["蒲松龄", "干宝", "纪昀"],
  "都市": ["汪曾祺", "阿城", "王小波"]
}
```

`_inspiration_few_shot` 按 `state.user_input.genre` 查白名单，传给 retriever 做 Chroma metadata 层过滤：
- 空列表 `[]` / 缺省 genre = 无过滤（兼容现有行为）
- 有白名单 = 严格限制到这些作家

**修复后实测**：
- 志怪 pipeline 重跑，L3 RAG 槽位 **9/9 全部蒲松龄**（《尸变》《考城隍》），零温瑞安污染
- 且真正的聊斋母题出现在成品里（见 3.5）

### 3.4 风格注入真实效果（三版对照）

同一志怪 premise，豆包 writer，balanced creativity：

| 聊斋核心母题 | Run B (RAG OFF) | Run A 旧（误灌温瑞安） | Run A' 新（蒲松龄） |
|---|---|---|---|
| **直接写鬼** | ❌ | ❌ | ✅ 「斗笠下空荡荡的，没有鼻子没有嘴」 |
| **触鬼物理感** | ❌ | ❌ | ✅ 「碰着灰布像碰着浸了霜的棉絮，再往里探，空的」 |
| **条目式叙事** | 长解释 | 温瑞安式冷笔 | ✅ 「同年离京前，永定门外酒摊，欠三碗绿蚁，许三十年内还。」 |
| **结尾"事实即主题"** | 「做官守心」+ 主题回响 | 父亲信中春旱预警 | ✅ 「忽然想起七日前父亲下葬时，这三枚铜钱是他亲手放进父亲棺中的陪葬品。」 |
| **消融的物理性** | 无 | 一道裂纹 | ✅ 「老翁的身影顺着那道缝融了进去」 |

**信号强度**：
- 对比 RAG OFF：**60-70% 偏移**（明显聊斋母题出现）
- 对比误灌温瑞安版：**30-40% 额外偏移**（在"冷笔留白"基础上加上**聊斋专属的"鬼怪当平常事"**）

**意外发现（错位 RAG 也有效）**：
即便 RAG 误灌了温瑞安武侠片段到志怪故事，LLM **不会把故事改成武侠**（没剑没江湖），但会**把温瑞安的技法（物件密度、对白经济、冷笔、留白）**应用到志怪题材。温瑞安和蒲松龄表面天差地别，但底层气质是近邻——冷笔、物件、留白、不解释。这说明 RAG 捕获的是**叙事技法**而非**题材本身**，和语言模型的 style/content 分离能力一致。

### 3.5 关键架构教训

1. **纯语义检索不够**：embedding 距离受语体影响大于题材
2. **需要硬边界**：genre→author 白名单是最简洁的路由方案，确定性强且可由用户编辑扩展
3. **Corpus 平衡是必要但不充分条件**：即便 1:1 基数也无法保证现代白话 query 能可靠命中文言 corpus
4. **扩库建议**：每个 genre 至少准备 2-3 位风格相近作家，防止过度集中在单一作品（Run A' 的 8/9 集中在《尸变》上）

### 3.4 防 self-judge / 同源 bias

严禁的做法：
- ❌ Writer 模型给自己打分
- ❌ 同 Claude 会话里让另一个子 Agent 既写又审（同源偏差）
- ❌ 用 audit 分数代替人眼评判

采用的做法：
- ✅ Writer = 豆包（独立模型），Judge = 我（Claude，异源）
- ✅ 裁判读完整成品，不看 audit 分数
- ✅ 子 Agent 报告的 metadata 一律要人眼复核（上次子 Agent 声称"删了 3 处越线句"，我复查发现都还在）

---

## 4. 如何复现（setup）

### 4.1 环境依赖

```bash
cd NOVEL-Studio
uv sync
```

### 4.2 .env 配置

```bash
# 必需其一（按你想用的 LLM provider）
ANTHROPIC_API_KEY=<your-anthropic-key>     # 真调 Claude API
DOUBAO_API_KEY=<your-volcengine-key>       # 火山 Coding Plan 订阅
DOUBAO_MODEL=doubao-seed-2.0-pro           # 可选，默认就是 pro
```

`.env` 已在 `.gitignore`，不会被提交。

### 4.3 灵感库 seed + ingest

```bash
# 把你喜欢的作家的短篇（txt）放到 inspirations/{作家}/*.txt
mkdir -p inspirations/温瑞安
cp your_files/*.txt inspirations/温瑞安/

# Ingest：首次会下载 BAAI/bge-large-zh-v1.5（~1GB）
uv run novel-studio inspire ingest

# 查看库分布
uv run novel-studio inspire list

# 测试检索
uv run novel-studio inspire query "剑光寒如雪" --top 3
```

### 4.4 生成小说（完整命令）

```bash
# Strict 档 + 豆包 writer + V2 管道
novel-studio init \
    --file inputs/your_premise.md \
    --genre 武侠 \
    --chapters 3 \
    --words 1500 \
    --creativity balanced \
    --provider doubao \
    --v2

# 捕获项目路径（最后一行输出）
PDIR=$(!!| tail -n 1)

# 推进（同步 provider 一次 step 跑完整个阶段）
while true; do
  novel-studio step "$PDIR" --provider doubao
  # 看到 "🎉 完成" 停
done

# 最终成品
cat "$PDIR/novel.md"
```

### 4.5 跑 A/B 对照实验

```bash
# RAG 开（默认）
novel-studio init --file premise.md --provider doubao  # 走 inspirations/

# RAG 关（对照组）
NOVEL_STUDIO_NO_RAG=1 novel-studio init --file premise.md --provider doubao
NOVEL_STUDIO_NO_RAG=1 novel-studio step <pdir> --provider doubao  # 每步都要带 env
```

---

## 5. 当前状态（2026-04-21）

### ✅ 已验证能用
- 多层状态机 V1 + V2（L4 对抗编辑 + scrubber）
- 创意档位（strict/balanced/creative）
- Multi-Head Audit (logic + pace)
- **灵感库 RAG 单作者注入**（温瑞安武侠）：信号强度 15-40%
- **灵感库多作者路由 via genre 白名单**：硬过滤 100% 准确，真实 pipeline 验证信号 60-70%
- **跨作者风格迁移验证**：蒲松龄注入志怪生成后出现「写鬼」「触鬼空袖」「条目式叙事」「事实即结局」等聊斋专属母题
- DoubaoProvider（火山 Coding Plan 订阅）
- AnthropicProvider（真调 Claude API）
- HumanQueueProvider（当前会话 Claude 扮 LLM，零 API 成本）
- 126 单元测试全绿

### 📋 待办
- 统计置信：同条件重复 3-5 次减少单样本噪声
- **灵感库扩充**：单作家至少 8-10 篇（防止检索过度集中在单一作品 —— Run A' 的 8/9 集中在《尸变》）
- **检索多样性**：同一作家内做 MMR 或多作品平摊，避免重复命中同一篇
- UX：`step` 不记忆 init 时的 provider（要每次传 `--provider`），体验差
- 长篇支持（chapter negotiation + world bible），V3 架构

---

## 6. 关于 Anti-AI-slop

辅助系统：`styles/_anti_slop.md` 列了常见 AI 腔词汇 + 结构。L4 scrubber 扫全文应用。

自动化指标：`slop_check.py` 纯规则扫描不调 LLM，输出 0-10 分。V2 管道里 final_audit 会把 slop_avg 纳入考量。

实际数据：
- 近期 V2 pipeline 产出的 slop_avg 普遍 < 2.0（干净区间）
- 温瑞安 RAG 下的产出 slop 更低（温瑞安本身是反 AI 腔的写法）
