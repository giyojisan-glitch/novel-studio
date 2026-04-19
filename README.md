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

- ✅ L1/L2/L3 layered state machine with checkpointing
- ✅ Multi-head audit (2 heads: logic + pace) with parallel dispatch and vote-based aggregation
- ✅ Retry gates with force-pass after 2 rounds
- ✅ Cross-attention-style layered prompting
- ✅ Style packs as editable markdown files (not hardcoded)
- ✅ Artifacts export (every layer's output is human-readable)
- ✅ CLI with init/step/status/artifacts commands

### What's missing (and why it's hard)

1. **API-based LLM provider.** The current `HumanQueue` provider requires a human (or Claude session) to write responses. Adding an `AnthropicProvider` / `OpenAIProvider` is maybe 100 lines — but making the prompts reliable against unsupervised LLMs is harder.
2. **Only 2 audit heads.** The original design has 4 (add character consistency + style). More critically: **no final-stage audit** reviews the whole book against the original premise, so cross-layer bugs (timeline contradictions, dropped foreshadows, minor characters collapsing) slip through. My three demos all had these.
3. **Chapters are still sequential.** True parallel generation with shared blackboard state + a negotiation round is V3.
4. **No inspiration library.** The "Lora-style" style packs are static descriptions. True RAG-based style transfer — feed the AI your favorite novels and generate in that voice — is unbuilt.
5. **Chinese-first.** Prompts and style packs are in Mandarin. Architecture is language-agnostic; porting is a translation task.

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
├── src/novel_studio/          # MVP Python implementation (~900 LoC)
│   ├── state.py               # Pydantic schemas for all layers
│   ├── prompts.py             # Prompt templates (L1/L2/L3/audit)
│   ├── engine.py              # Step engine: advance / retry / apply
│   ├── audit.py               # Multi-head audit aggregator
│   ├── cli.py                 # novel-studio init/step/status/artifacts
│   └── utils.py               # File IO, artifact rendering
├── styles/                    # Genre style packs (editable .md)
│   ├── 科幻.md / 仙侠.md / ...  # Chinese; each defines voice for a genre
│   └── _README.md
├── inputs/                    # Premise input files
│   ├── _TEMPLATE.md           # How to write a good premise
│   └── _EXAMPLE_xianxia.md    # Worked example
├── docs/
│   ├── ARCHITECTURE.md        # Detailed design reasoning
│   └── ROADMAP.md             # V2/V3/V4 planned improvements
└── README.md (this file)
```

Runtime directories (`projects/`, `outputs/`, `artifacts/`) are gitignored — they contain generated content.

---

## Trying the MVP

> **Caveat:** Currently only runs inside a Claude Code session, because the LLM provider is human-in-the-loop.

```bash
# Install
uv sync

# Write a premise file in inputs/ (150+ chars, see _TEMPLATE.md)
# Then initialize a project
uv run novel-studio init --file inputs/my_premise.md --genre 仙侠 --chapters 3

# The engine dumps prompts to projects/{timestamp}/queue/
# In a Claude Code session, respond to each prompt by writing JSON to responses/
# Then advance:
uv run novel-studio step projects/{timestamp}/

# Loop until DONE. Final novel appears in outputs/
```

If you want a truly autonomous version: implement a real `LLMProvider` in `src/novel_studio/llm/` (replace `HumanQueue` with `AnthropicProvider` / `OpenAIProvider` / `LocalLlamaProvider`) and send a PR. That's roughly the V2 milestone.

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
