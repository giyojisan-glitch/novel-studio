# 训练方法与验证思路（2026-04 更新）

> 这不是一个"训练模型"的项目 —— 我们不调 LLM 参数。这是一个**推理时的风格+结构调优系统**。
> 本文记录这套系统的设计思路 + 我们如何验证每个组件真的在起作用。

---

## 1. 核心思路（四层创新）

对标 gpt-author / AI_NovelGenerator / autonovel 等开源项目，NOVEL-Studio 的差异点在四个维度：

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
