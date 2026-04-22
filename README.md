# NOVEL-Studio

> **An architectural experiment: generate a structurally-complete novel from one sentence, in minutes, not hours.**

---

## The Vision

If this architecture is fully implemented, it should be able to:

- Turn a **one-paragraph premise** into a **complete, logically-coherent novel** in **2–10 minutes** (depending on length)
- Maintain **structural integrity** across chapters: three-act arcs, planted foreshadows all paid off, character consistency from chapter 1 to chapter N
- Accept **style transfer inputs** ("write this in the voice of [favorite author]") via an inspiration library
- Be controllable at every layer — a human can inspect and edit the skeleton, chapter outlines, or individual scenes without regenerating everything else

This is not *"smoother AI text."* This is **structured fiction** — a machine that actually plans before it writes.

## Why This is Possible

Every existing open-source novel generator I've studied shares the same flaw: **they are sequential prompt chains**. They write chapter 1 → chapter 2 → chapter 3, and each chapter is generated with little more than a summary of what came before. This is why they collapse after ~5 chapters: characters drift, foreshadows get forgotten, timelines contradict.

Two insights from deep learning suggest a fundamentally better architecture:

### Insight 1 — Diffusion models don't generate left-to-right

Image diffusion models iteratively **denoise**: a blurry global draft becomes progressively sharper through N refinement passes. The full composition is present from step 1; each pass clarifies, it doesn't *extend*.

**Applied to fiction:** instead of writing chapter 1 before knowing what chapter 3 looks like, we start with a **skeleton of the entire novel** and progressively refine: skeleton → chapter outlines → paragraph drafts → polished prose. Every layer sees the whole book.

### Insight 2 — Transformer attention is parallel and multi-headed

Multi-head attention lets multiple "heads" inspect the same text along **different semantic axes** simultaneously (syntax, co-reference, semantic roles, etc.), then combines their views.

**Applied to review:** logic, pace, character consistency, and style are **independent semantic axes**. They should be reviewed **in parallel by separate heads**, not by a single "general editor" prompt. Each head catches what others miss.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ L1  Skeleton     (world + characters + three acts)       │
│    ↓ Multi-Head Audit  [logic | pace | character | style]│
│ L2  Chapter Outlines    (N chapters, each with hooks,    │
│                          key events, foreshadow ledger)  │
│    ↓ Multi-Head Audit                                    │
│ L3  Paragraph Writing   (parallel per chapter)           │
│    ↓ Multi-Head Audit                                    │
│ L4  Polish              (style unification, motif tying) │
│    ↓ Final-Stage Audit  (whole-book review vs premise)   │
│ →  Final Markdown                                        │
└─────────────────────────────────────────────────────────┘
```

### Key architectural commitments

- **Layered state machine.** Each layer's output is frozen before the next layer reads it. No chapter is written until the skeleton is approved.
- **Parallel chapter generation.** Because the skeleton is frozen, chapter 3 and chapter 7 can be written simultaneously — they see the same world state.
- **Multi-head audit gates.** Between layers, multiple independent review heads evaluate the output along different axes in parallel. Aggregated verdict decides pass/retry.
- **Retry with feedback.** Failed layers are sent back with specific `retry_hint` from the audit — not a generic "try again."
- **Cross-attention-style context.** Each layer's prompt receives layered context: L3 (paragraph writing) sees the skeleton summary + its chapter outline + the last 150 characters of the previous chapter. Just enough to maintain coherence, not enough to blow the context window.

### Why this is faster than sequential approaches

| Approach | Time for 10-chapter novel (rough estimate) |
|---|---|
| Sequential chain (typical open-source) | 15–40 min (each chapter waits for the previous) |
| This architecture (fully implemented) | **2–8 min** (L1 and L2 sequential, L3 and audits parallel) |

The speed isn't from a faster model — it's from **not waiting for chapter N-1 to finish** when chapter N has enough context from the frozen skeleton.

---

## Current Status: Rough MVP

The core mechanics are proven: I ran three end-to-end demos (sci-fi, xianxia, urban drama) and the layered state machine + multi-head audit work as designed.

But there's a catch — **the MVP currently runs only inside a Claude Code session**, because the LLM provider is *human-in-the-loop* (the engine dumps prompts to files, a human writes JSON responses). This was a deliberate choice for architecture validation without burning API credits.

### What's working

- ✅ L1/L2/L3/**L4** layered state machine with checkpointing (V2: adversarial edit + scrubber)
- ✅ Multi-head audit (2 heads: logic + pace) with parallel dispatch and vote-based aggregation
- ✅ **Final-stage audit**: whole-book review against premise (catches timeline contradictions, dropped foreshadows, collapsed characters)
- ✅ Retry gates with force-pass after 2 rounds
- ✅ Cross-attention-style layered prompting
- ✅ Style packs as editable markdown files (not hardcoded)
- ✅ **Creativity parameter** (`strict` / `balanced` / `creative`) — routes both temperature (0.3/0.7/1.0) and prompt constraints per run
- ✅ **Three LLM providers**: `human_queue` (Claude session responds), `anthropic` (Claude API), `doubao` (volcengine Coding Plan)
- ✅ **Lora-style Inspiration RAG**: `inspirations/{author}/*.txt` → BAAI/bge-large-zh-v1.5 embeddings → Chroma → auto-injected into L3 prompts as style references. See [`docs/TRAINING_METHODOLOGY.md`](docs/TRAINING_METHODOLOGY.md) for A/B validation results.
- ✅ **V3 long-form pipeline** (`--pipeline v3`): interleaved L2/L3 (each chapter outline sees the real prose of prior chapters) + **WorldBible** (per-chapter `bible_update` extracts new characters, facts, timeline events, and foreshadow state; bible is injected as context into subsequent L2/L3 prompts). Supports up to 30 chapters.
- ✅ **V4 scene decomposition + multi-scale continuity** (`--pipeline v4`): adds an L2.5 layer that breaks each chapter into 3-5 scenes with explicit opening/closing beats. L3 writes scene-by-scene; each scene prompt sees a CNN-style **multi-scale context** (last 400 chars of prev scene at high resolution, prior scene beats at mid resolution, last 3 chapters' closing at low resolution) + an **anti-cold-open hard constraint** that forbids chapter-restart templates ("指节攥得发白"). New **continuity audit head** (alongside logic + pace) scores cross-scene/cross-chapter handoff. L2 prompts now actually see the 800-char tail of the previous chapter's last scene (V3's half-kept interleaved promise finally fulfilled).
- ✅ **V5 premise fidelity** (`--pipeline v5`): four state-tracking mechanisms address the drift bugs surfaced by an external review of V4's first novel (missing visual anchors, repeated cockcrow timeline, partial object states, missing character-obsolescence mechanic). L1 extracts **`visual_anchors`** from premise (concrete must-keep images like 「父亲化作泥塑裂纹」); L2.5 assigns a **`time_marker`** per scene with monotonic progression enforced across chapters; **`tracked_objects`** maintain current state cross-chapter and L3 must not contradict; **character `status`** (active/fading/gone) + `reliability` drive writing-style shifts for disappearing characters. Continuity audit reads bible state as ground truth. `final_audit` explicitly checks anchor fulfillment and engine force-bounces if `unfulfilled_anchors` non-empty.
- ✅ **V6 叙事承诺 + 阵营图谱** (`--pipeline v6`): three orthogonal schema additions plug V5's blind spots surfaced by the second external review of V5's first novel (missing specialized plot mechanics like "死子", faction-ambiguous 暗桩/内线, technical depth replaced by 真气/喷血 武侠 tropes). L1 extracts **`plot_promises`** (non-visual narrative contracts: "埋下跨越十年的死子" / "揭露构陷证据"—things visual_anchors can't catch because they're *plot mechanics*, not *pictures*); L2 assigns setup/payoff chapters; L2.5 scene gets **`technical_setup / technical_payoff`** fields forcing genre-specific terminology (围棋的死活/手筋, 悬疑的物证链). **CharacterState.faction** disambiguates multi-faction characters (顾府 vs 沈家 暗桩). Continuity audit adds 3 V6 checks (promise fulfillment, faction consistency, technical specificity). Final audit + engine force-bounces on `unfulfilled_promises`. **Recommended for long-form**.
- ✅ Artifacts export (every layer's output is human-readable)
- ✅ CLI with init/step/status/artifacts/inspire commands
- ✅ 241 unit tests green

### A/B validated signals (see `docs/TRAINING_METHODOLOGY.md`)

- **Inspiration RAG produces real style transfer**: when seeded with 蒲松龄 (Liaozhai ghost stories) and run on a 志怪 premise, the output exhibits Liaozhai-specific motifs that are absent in both the no-RAG control and the mis-routed (温瑞安 武侠) variant — e.g. faceless figures under a hat, hollow-sleeved ghosts, terse fact-based narration ("owed three bowls of wine at West Gate, promised to repay within thirty years"), and the Liaozhai-characteristic fact-as-ending closing line. **60-70% offset from control**.
- **Pure semantic retrieval alone is insufficient**: under 10:1 corpus imbalance, modern-Chinese L2 queries get embedded closer to modern-prose corpus than to classical-Chinese text, regardless of theme. Solved by `styles/inspiration_routing.json` — a user-editable **genre → author whitelist** that applies deterministic metadata filtering at retrieval time.
- Single-author style shift (温瑞安 武侠 RAG on a 武侠 premise): **15-40% offset** from control (stronger on models with more default bias — larger correction room).

### What's missing (and why it's hard)

1. **3 audit heads (logic + pace + continuity).** Still missing character-consistency and style heads that the original design called for.
2. **V6 not yet battle-tested on real LLM.** V5 5-chapter Doubao demo ran cleanly (all visual_anchors fulfilled, monotonic time markers). V6 addresses the narrative-contract / faction / technical-specificity blind spots surfaced by the subsequent external review, but the combined effect on shipping output is unverified until a fresh 5-chapter Doubao run completes.
3. **Chapters are still sequential within L3.** True parallel chapter generation with shared blackboard state is orthogonal to V3/V4/V5/V6 work.
4. **Chinese-first.** Prompts and style packs are in Mandarin. Architecture is language-agnostic; porting is a translation task.
5. **UX rough edges**: `step` doesn't remember the `--provider` chosen at `init`, must be passed each call.

---

## Why I'm Publishing This — and Looking for Collaborators

Honest answer: **the implementation feels harder than I can finish alone.**

The architectural insight is clear and, I believe, correct. But each remaining piece (autonomous provider that doesn't break on LLM misbehavior; truly parallel chapters with consistent state; a final-stage audit that catches what single-layer audits miss; a proper RAG inspiration library) is non-trivial.

So rather than half-build it and shelve it, I'm publishing the idea, the partial implementation, and the honest map of what's left — and I'm actively looking for collaborators.

**If any of this interests you — whether you want to contribute code, rewrite parts in a different language, argue about the architecture, or just try to break it — please reach out.**

### Contact

- **Email:** giyojisan@gmail.com
- **LINE ID:** `eggpunchman`

I'll respond to every message. If you want to fork and go solo, that's fine too — MIT means MIT. But if you want to build this together, the door is open.

---

## Repository Contents

```
novel-studio/
├── src/novel_studio/
│   ├── state.py               # Pydantic schemas for all layers
│   ├── prompts.py             # Prompt templates (L1/L2/L3/L4/audit) + RAG injection
│   ├── engine.py              # Step engine: advance / retry / apply
│   ├── audit.py               # Multi-head audit aggregator
│   ├── cli.py                 # novel-studio init/step/status/artifacts/inspire
│   ├── llm/                   # LLM Provider abstractions
│   │   ├── human_queue.py     # Claude session as LLM (free, slow)
│   │   ├── anthropic.py       # Claude API (paid, fast)
│   │   ├── doubao.py          # Volcengine Coding Plan (subscription)
│   │   └── stub.py            # Deterministic fixtures for tests
│   ├── inspiration/           # Lora-style RAG
│   │   ├── ingester.py        # .txt → chunks → BAAI embeddings → Chroma
│   │   └── retriever.py       # Query-time top-k retrieval with filters
│   └── utils.py
├── styles/                    # Genre style packs (editable .md)
│   └── 科幻.md / 武侠.md / 志怪.md / 日轻.md / ...
├── inspirations/              # Lora training data (gitignored — copyright)
│   └── {作家}/*.txt           # Seed author's works here
├── inputs/                    # Premise input files
│   └── _TEMPLATE.md
├── chroma_db/                 # Vector store (gitignored)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TRAINING_METHODOLOGY.md  # ★ Experiment design + A/B validation results
│   ├── INSPIRATION_MAP.md
│   └── ROADMAP.md
└── tests/                     # 126 tests
```

Runtime directories (`projects/`, `outputs/`, `artifacts/`, `chroma_db/`, `inspirations/{author}/`) are gitignored — they contain generated content or copyrighted source material.

---

## Trying the MVP

```bash
# Install
uv sync

# Put your API key in .env (gitignored):
#   ANTHROPIC_API_KEY=sk-ant-...          OR
#   DOUBAO_API_KEY=...                    (volcengine Coding Plan subscription)

# Write a premise file in inputs/ (150+ chars, see _TEMPLATE.md)

# Run with Claude API (auto, fast)
uv run novel-studio init --file inputs/my_premise.md \
    --genre 武侠 --chapters 3 --words 2000 \
    --creativity balanced --provider anthropic --v2

# Or run with Doubao (volcengine subscription)
uv run novel-studio init --file inputs/my_premise.md \
    --genre 武侠 --chapters 3 --words 2000 \
    --creativity balanced --provider doubao --v2

# Or run free via Claude Code session (human_queue: you respond to prompts)
uv run novel-studio init --file inputs/my_premise.md --genre 武侠 --chapters 3

# V3 long-form (interleaved L2/L3 + WorldBible)
uv run novel-studio init --file inputs/my_novel.md \
    --genre 志怪 --chapters 10 --words 1500 \
    --provider doubao --pipeline v3

# V4 for long-form (L2.5 scene decomposition + multi-scale context
# + continuity audit). --scenes-per-chapter is a soft target (LLM
# picks final count in 3-5 range).
uv run novel-studio init --file inputs/my_novel.md \
    --genre 志怪 --chapters 10 --words 1500 \
    --provider doubao --pipeline v4 --scenes-per-chapter 4

# V5 premise fidelity (visual_anchors, time_markers, tracked_objects, 
# character status tracking).
uv run novel-studio init --file inputs/my_novel.md \
    --genre 志怪 --chapters 10 --words 1500 \
    --provider doubao --pipeline v5 --scenes-per-chapter 4

# V6 recommended for shippable long-form (V5 + narrative contracts
# + faction map + technical setup/payoff).
uv run novel-studio init --file inputs/my_novel.md \
    --genre 武侠 --chapters 10 --words 1500 \
    --provider doubao --pipeline v6 --scenes-per-chapter 4

# Advance (for auto providers one call per stage):
uv run novel-studio step projects/{timestamp}/ --provider doubao
# Loop until 🎉 完成. Final novel in outputs/
```

### V3 long-form pipeline

`--pipeline v3` switches the engine from "write all outlines up front" to **interleaved per-chapter processing**:

```
L1  skeleton
 └─ audit
 └─ bible_init           (seed WorldBible from L1: characters, world rules, theme→foreshadow)
 └─ for chapter i in 1..N:
     L2_i outline        (gets full bible context: active characters, unpaid foreshadow, hard rules)
      └─ audit
     L3_i prose          (gets bible + last chapter's actual tail, not just outline)
      └─ audit
     bible_update_i      (LLM extracts: new characters, new facts, timeline events,
                          paid foreshadow, new foreshadow, consistency issues)
 └─ final_audit → L4_adversarial → L4_scrubber → finalize
```

**Why this beats sequential chains:** at chapter 7, L3 prompt sees a structured account of every character's current arc state, which rules are in force, which foreshadows still need paying off, and what actually happened in each prior chapter (not just outlines). Characters stop drifting; rules stop contradicting; foreshadow paying becomes explicit rather than accidental.

The bible is append-only: each `bible_update_i` emits increments (`new_characters`, `character_updates`, `paid_foreshadow`, etc.), merged deterministically into state. You can inspect it at any time in `projects/{slug}/state.json` → `world_bible`.

### V4 scene decomposition + multi-scale continuity

V3 got characters and rules under control but couldn't stop each chapter from starting as if it were Chapter 1 again — every opening resorted to "沈砚指节攥得发白" / "沈砚喉结滚动" because L3's only window into prior prose was 150 chars. V4 addresses this directly:

```
L2_i outline (now sees 800 chars of ch_{i-1}'s final scene, not just summary)
 └── L2_i audit
L2.5_i scene list        (new layer: 3-5 SceneOutlines with opening_beat /
 └── L2.5_i audit          closing_beat / dominant_motifs / target_words)
for scene s in 1..M_i:
  L3_{i,s} prose         (sees multi-scale context:
                            · scene level: prev scene's tail 400 chars
                            · chapter level: all prior scene beats this chapter
                            · book level: last 3 chapters' closing 100 chars
                          + hard anti-cold-open constraint:
                          "严禁以『X 指节攥得发白』作为场景第一句")
L3_i chapter audit       (3 heads: logic + pace + **continuity**)
                          continuity head specifically checks:
                          - chapter opening actually continues prev chapter closing
                          - scene transitions use concrete objects/time/action
                          - shared motifs stay consistent across scenes
                          - no 2+ recurring "template" openers per chapter
bible_update_i           (same as V3)
```

**CNN analogy**: V3 was effectively a 1-layer conv with a tiny kernel (150-char receptive field), so long-range info couldn't flow. V4 stacks receptive fields at three resolutions — the scene-level window gives rhythm carry-over, the chapter-level beats prevent rhythm repetition, and the book-level closings keep the whole arc in view. Like U-Net skip-connections: each layer gets info at the resolution where it's useful.

**Interleaved promise now actually kept**: V3 scheduled `L2_i → L3_i → L2_{i+1}` but L2_{i+1}'s prompt still only read summaries, not prose. V4's l2_prompt injects 800 chars of the previous chapter's last scene content directly.

Artifacts produced (all human-readable):
- `artifacts/{slug}/07_scene_lists.md` — the L2.5 per-chapter scene design
- `artifacts/{slug}/08_scene_cards.md` — design vs actual comparison (what LLM wrote vs what was planned)

### V5 premise fidelity

After V4's first 5-chapter production run, an external review identified four architectural-level drift bugs that V4's 3-head audit couldn't catch:

1. **Missing visual anchor**: premise said "父亲走进庙里，成了泥塑上的一道裂纹" (father becoming a crack in the clay idol — the key ending image). V4 rendered father as "淡了" (faded). The visual was never extracted into bible as a hard commitment.
2. **Timeline restart**: premise involved "三声鸡鸣" as time progression. V4's chapters ran *two and a half complete cockcrow cycles* because each L3 scene locally rebuilt pacing without seeing global progress.
3. **Object state collapse**: premise emphasized "三碗酒" symbolizing three debts. V4 resolved only the leftmost bowl; the other two were never given symmetric state transitions.
4. **No character obsolescence**: V4's ending had the protagonist forgetting his father (poetic), but bible still recorded 左眉旧疤 firmly. Ch6+ would mismatch.

V5 adds four orthogonal state-tracking fields:

```
L1 骨架                          + visual_anchors: list[str]      ← L1 extracts from premise
                                 + tracked_object_names: list[str]
 └── bible_init                  copies visual_anchors + seeds tracked_objects

for chapter i:
  L2_i outline
   └── L2_audit

  L2.5_i scene list              each scene gets time_marker
   └── L25_audit                  (monotonic across book · bible tracks全局列表)

  for scene s in 1..M_i:
    L3_{i,s}                     + time_marker 硬约束 (禁推进 / 禁回退)
                                 + tracked_objects 当前状态注入 (不得矛盾)
                                 + character status hints (fading/gone 影响笔法)
                                 + unfulfilled_anchors 提示 (合适时兑现)

  L3 chapter_audit (continuity head extended with 3 V5 checks:
                    time_marker monotonic; tracked_objects consistent;
                    anchor fulfillment appropriate)

  bible_update_i                 + object_state_changes
                                 + character_status_changes
                                 + visual_anchors_fulfilled

final_audit                      + 强制检查 unfulfilled_anchors
  engine (not LLM!) force-bounces if non-empty → suspect=L3 → L4 waits
```

**Why state-tracking not prompt-tweaking**: the external review showed that V4's prose instincts are already good — the failures were *structural*. The LLM didn't know 泥塑裂纹 was non-negotiable, didn't know time was monotonic across chapters, didn't know two bowls remained intact. V5 moves these from "hopefully LLM remembers" to "bible records + prompt injects at every relevant step + audit checks + final enforcement".

New artifact:
- `artifacts/{slug}/09_visual_anchors.md` — each anchor's ✅/⏳ status. If anything's ⏳ when final_audit runs, engine bounces.

### V6 叙事承诺 + 阵营图谱 + 技术性伏笔

After V5's first Doubao production run (5 chapters, all visual_anchors fulfilled, monotonic time markers), a second external review found three structural gaps that V5's visual-only anchoring couldn't catch:

1. **Narrative-contract blind spot**: premise said "埋下数颗跨越十年的死子" (literally "lay down dead stones spanning ten years" — the central plot mechanic of the go-chess premise). The novel used the word "死子" exactly zero times in 7097 characters. `visual_anchors` only extracts *pictures*, not *plot promises* — so this contract had no schema slot to bind to.
2. **Faction ambiguity**: the story had two opposed factions (顾府 / 沈家) both employing "暗桩" (secret agents). Readers had to infer allegiance from clothing color alone; CharacterState had no `faction` field to enforce consistency.
3. **Specialized-vocab bleed**: specifically because `--genre 武侠` was chosen, L3 fell back on generic wuxia tropes ("真气 19 次", 喷血-style climaxes) instead of the go-specific mechanics the premise demanded ("劫/死活/手筋/官子" — all zero occurrences). SceneOutline had no technical-specificity slot.

V6 adds three orthogonal schema additions:

```
L1 骨架                  + plot_promises: list[PlotPromise]   ← 非视觉的叙事承诺
                         + CharacterCard.faction              ← 主角/反派阵营
 └── bible_init           copies plot_promises, propagates faction → CharacterState

L2_i outline             + promise_setups: list[str]          ← 本章要埋设的 promise.id
                         + promise_payoffs: list[str]         ← 本章要引爆的 promise.id

L2.5_i scene list        each SceneOutline gets:
                         + technical_setup: str               ← 体裁术语写的具体铺垫
                         + technical_payoff: str              ← 体裁术语写的具体引爆

L3_{i,s} prose           + 叙事承诺账本 block
                         + 本章 promise 分配 block
                         + 角色阵营图谱 block（暗桩/内线必须用服色+阵营显式区分）
                         + 本场 technical_setup/payoff block

continuity_audit         + 3 项 V6 检查:
                           · promise 兑现（具体术语 vs 模糊"真气喷血"）
                           · 角色阵营一致（同名不改阵营）
                           · technical specificity（禁用万能模糊词替代专业术语）

bible_update_i           + promise_setups_done / promise_payoffs_done（匹配 bible.id）

final_audit              + 强制检查 unfulfilled_promises
  engine force-bounces if non-empty（镜像 V5 unfulfilled_anchors 机制）
```

**Why plot_promises ≠ visual_anchors**: Premise 里同时存在"画面必保项"（visual）和"情节必守约"（plot promise）。"父亲化作泥塑上的裂纹"是视觉——能被 visual_anchors 捕获，也能通过看正文识别兑现。"埋下跨越十年的死子"是专业机巧——是"在章节 X 做 A 动作以便章节 Y 的 B 动作能 payoff"这种 *跨章节的叙事合约*，visual_anchors 逻辑上无法承载。V6 用 PlotPromise.setup_ch/payoff_ch 的显式 ledger 兜住这类承诺。

**Why faction ≠ character traits**: V5 的 `CharacterState.traits` 存的是稳定特质（"左眉旧疤"/"棋艺高超"），不含"立场"。当 premise 涉及多股势力，同一个泛称（"暗桩"/"内线"）会被不同阵营同时使用。没有 `faction` 字段时，LLM 只能靠服色/物件/场景间接传达，读者要推；有了 `faction` 字段，continuity 审头可以直接做"同名不改阵营"校验。

New artifact:
- `artifacts/{slug}/10_plot_promises.md` — each promise's ⏸/⏳/✅ status（未埋/已埋未引爆/已引爆）+ setup_ch / payoff_ch 章节追踪。镜像 09_visual_anchors 的人眼审阅范式。

### Seed the inspiration library (Lora-style style transfer)

```bash
# Put your favorite author's short stories here
mkdir -p inspirations/温瑞安
cp path/to/*.txt inspirations/温瑞安/

# Ingest (first time downloads BAAI/bge-large-zh-v1.5 ~1GB)
uv run novel-studio inspire ingest

# Verify library contents + test retrieval
uv run novel-studio inspire list
uv run novel-studio inspire query "剑光寒如雪" --top 3

# Now any L3 generation auto-injects style references.
# To verify with A/B: run once with RAG on, once with NOVEL_STUDIO_NO_RAG=1 to compare.
```

See [`docs/TRAINING_METHODOLOGY.md`](docs/TRAINING_METHODOLOGY.md) for the full A/B validation methodology and results.

---

## Related Work

- [YILING0013/AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator) — sequential chain, Chinese, has vector-based long-term memory
- [NousResearch/autonovel](https://github.com/NousResearch/autonovel) — closest in spirit; multi-agent pipeline with polish + ePub output
- [datacrystals/AIStoryWriter](https://github.com/datacrystals/AIStoryWriter) — Ollama-friendly, long-output focused
- [mshumer/gpt-author](https://github.com/mshumer/gpt-author) — the progenitor; fantasy-only, EPUB export
- [raestrada/storycraftr](https://github.com/raestrada/storycraftr) — CLI-driven, step-by-step outline → chapters

What differentiates NOVEL-Studio's *intended* architecture from all of these: **none of them do multi-head parallel audit, and none of them are designed for chapter-level parallel generation with shared frozen state.** The closest in philosophy is autonovel, but it's still a sequential pipeline.

---

## License

MIT — fork it, rewrite it, push the idea forward.

---

## Acknowledgments

Built iteratively in a Claude Code session over one afternoon. The architectural insights are mine; the code is a rough first pass exploring whether the insights actually work at the file-and-prompt level. They do. The remaining work is turning "works" into "ships."
