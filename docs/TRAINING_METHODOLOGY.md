# 训练方法与验证思路（2026-04 更新）

> 这不是一个"训练模型"的项目 —— 我们不调 LLM 参数。这是一个**推理时的风格+结构调优系统**。
> 本文记录这套系统的设计思路 + 我们如何验证每个组件真的在起作用。

---

## 1. 核心思路（三层创新）

对标 gpt-author / AI_NovelGenerator / autonovel 等开源项目，NOVEL-Studio 的差异点在三个维度：

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

### 3.3 多作者路由验证

**问题**：如果灵感库里同时放多个作家，RAG 能按查询语境自动路由到对应作家吗？如果不能，Lora-style 的"换库即换风格"就是伪命题。

**设计**：库里放 100 chunks 温瑞安（武侠）+ 10 chunks 蒲松龄（聊斋志怪）。10:1 悬殊基数，对蒲松龄极不利。

- 5 个武侠 query → top-3 里温瑞安占几次？
- 5 个志怪 query → top-3 里蒲松龄占几次？

**结果**：
- 武侠 → 温瑞安：**15/15 = 100%**
- 志怪 → 蒲松龄：**12/15 = 80%**（唯一翻车 case："书生遇狐"里"狐"词在温瑞安文本里也有出现）
- 总路由命中率：**27/30 = 90%**

**结论**：**语义路由工作**，且在 10:1 不利基数下仍能压住。可以放心往库里扩其他作家，不会污染已有 genre 的 query。

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
- 灵感库 RAG 单作者注入（温瑞安武侠）：信号强度 15-40%
- 灵感库多作者语义路由：90% 命中率
- DoubaoProvider（火山 Coding Plan 订阅）
- AnthropicProvider（真调 Claude API）
- HumanQueueProvider（当前会话 Claude 扮 LLM，零 API 成本）
- 126 单元测试全绿

### 🚧 进行中
- 志怪 A/B 实验（豆包 writer + 蒲松龄库 vs RAG OFF）
- 跨作者风格迁移验证

### 📋 待办
- 统计置信：同条件重复 3-5 次减少单样本噪声
- UX：`step` 不记忆 init 时的 provider（要每次传 `--provider`），体验差
- 长篇支持（chapter negotiation + world bible），V3 架构

---

## 6. 关于 Anti-AI-slop

辅助系统：`styles/_anti_slop.md` 列了常见 AI 腔词汇 + 结构。L4 scrubber 扫全文应用。

自动化指标：`slop_check.py` 纯规则扫描不调 LLM，输出 0-10 分。V2 管道里 final_audit 会把 slop_avg 纳入考量。

实际数据：
- 近期 V2 pipeline 产出的 slop_avg 普遍 < 2.0（干净区间）
- 温瑞安 RAG 下的产出 slop 更低（温瑞安本身是反 AI 腔的写法）
