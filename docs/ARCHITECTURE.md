# Architecture

Detailed reasoning for the design choices. If you're thinking about forking or contributing, read this first.

## Mental model

This system treats novel generation as an **iterative denoising process** (from diffusion models) structured as a **layered state machine** (from software architecture) where each state transition is guarded by **parallel multi-axis review** (from Transformer multi-head attention).

The three analogies aren't decorative — they map to specific implementation choices:

| Analogy | Implementation |
|---|---|
| Diffusion denoising | Layers L1→L4, each refining a global but blurry representation of the whole book |
| State machine | `NovelState` (Pydantic) + explicit `next_step` field + retry gates |
| Multi-head attention | `AuditHead` protocol; heads run in parallel via `asyncio.gather`; `aggregator.py` combines verdicts |

## The four layers

### L1 — Skeleton
**Input:** user premise (100+ chars recommended), genre, chapter count, target word count per chapter.

**Output:** `L1Skeleton` containing:
- Title, logline (≤25 chars), theme
- Protagonist and optional antagonist cards (`name`, `traits`, `want`, `need`)
- Three-act structure (`setup`, `confrontation`, `resolution`, each ≤60 chars)
- 3–5 world rules (hard constraints that shape later chapters)

**Design choice:** everything in L1 is *short and dense*. It's the prior that will condition every downstream layer. Long text here wastes context and makes audit harder.

### L2 — Chapter Outlines
**Input:** L1 skeleton + index of the current chapter (for sequential generation; the parallel version is V3).

**Output:** one `L2ChapterOutline` per chapter, containing:
- Title, summary (≤200 chars), pov
- 3–5 `key_events` (causal, not sequential)
- `prev_connection` (how this chapter continues from the previous)
- `hook` (end-of-chapter suspense)

**Design choice:** `key_events` are framed as causal chains, not just timestamps. This forces the outline to commit to *why* things happen, not just *what* happens.

### L3 — Paragraph Writing
**Input:** L1 skeleton (compressed) + current L2 chapter outline + last 150 chars of the previous L3 chapter (for style continuity).

**Output:** `L3ChapterDraft` with full chapter text.

**Design choice:** L3 sees only the tail of the previous chapter, not the full chapter. This is a deliberate context-window limit — it forces the writer to trust the outline, not to re-reference earlier prose.

### L4 — Polish (currently pass-through)
**Planned input:** all L3 drafts + inspiration library references.

**Planned output:** style-unified full text with motifs and callbacks.

**Current status:** L4 is a pass-through (just copies L3 content). This is honest MVP behavior; we chose not to fake a polish layer that doesn't actually polish.

## Multi-head audit

Between every layer, the output is reviewed by multiple parallel "heads" — each head is an LLM call with a different system prompt focused on one axis of quality.

### Current heads (MVP: 2 heads)

| Head | What it checks |
|---|---|
| `logic` | Causal consistency, world-rule compliance, character action vs `want`/`need`, timeline coherence |
| `pace` | Act-position fit, tension density, hook strength, word-count rhythm |

### Planned heads (V2: 4 heads)

| Head | What it checks |
|---|---|
| `character` | Character voice consistency, motivation stability, arc progression |
| `style` | Voice match to style pack, sentence rhythm, lexical variety |

### Aggregation

`aggregate()` in `src/novel_studio/audit.py`:

- Each head returns `AuditReport(passed, score, issues, suggestions)`
- Aggregator rule (MVP, permissive): **at least 1 head passes AND average score ≥ 0.7** → overall pass
- Rejected verdicts produce a `retry_hint` concatenated from each failing head's specific issues
- Retry cap: 2 rounds per layer, then force-pass with a trace warning

This is deliberately lenient — MVP prioritizes flow completion over perfection. A production version should likely tighten thresholds and increase the retry cap.

## Cross-attention-style context injection

Instead of passing the entire accumulated state to every prompt (which would explode the context window), each layer receives a **carefully selected slice** of prior layers:

```
L1 prompt  ← premise (raw user input) + schema
L2 prompt  ← L1 (full) + previous L2 outlines (compressed to title+summary) + schema
L3 prompt  ← L1 (summary only: title + protagonist card + world rules) +
             current L2 (full) +
             last 150 chars of previous L3 +
             schema
audit prompt ← subject layer output + minimal context (L1 summary or relevant L2)
```

This is analogous to how Transformer cross-attention lets decoder layers query encoder outputs — each target layer pulls only the encoder features it needs.

## Retry gate mechanics

When an audit verdict returns `passed=False`:

1. Increment `revision` counter on the rejected layer's output
2. If `revision >= MAX_REVISION` (default 2): force-pass, log warning to trace, continue
3. Otherwise: delete the rejected output + its pending audit files, regenerate the prompt (which now includes `retry_hint` in the system message)

The design assumption is that **specific feedback is more useful than a generic "try again."** Retry hints from each failing head are concatenated verbatim.

## Why no LangGraph (yet)

An earlier draft used LangGraph for orchestration. I removed it for MVP because:

1. The Human-in-the-loop provider (current MVP mode) needs the engine to **pause and wait for files to appear**, which maps awkwardly onto LangGraph's interrupt/resume primitives
2. A hand-written step engine (`engine.py`, ~270 LoC) is easier to debug when you're also debugging prompts
3. LangGraph shines for complex branching DAGs; our state machine is mostly linear with retry loops

When an autonomous API provider lands (V2), refactoring to LangGraph makes sense. The `advance()` function is designed to be replaced without touching schemas, prompts, or audit logic.

## File layout and artifacts

Runtime writes to three parallel directories, each with a clear role:

```
projects/{timestamp}/         — Internal execution state
  state.json                    Serialized NovelState
  queue/*.prompt.md             Prompts awaiting LLM response
  responses/*.response.json     LLM outputs (human-written in MVP mode)
  novel.md                      Final assembled output (also copied to outputs/)
  trace.json                    Step-by-step execution log

artifacts/{timestamp}/        — Human-readable intermediate products
  00_premise.md                 Original input
  01_L1_骨架.md                 Rendered skeleton
  02_L2_章节梗概.md             Chapter outlines
  03_L3_正文草稿.md             Full drafts
  04_audit_历程.md              All audit verdicts with scores and feedback

outputs/                      — Final polished novels
  {Title}_{timestamp}.md        Named by the L1-generated title
```

`artifacts/` exists specifically to make the generation process **debuggable and inspectable**. If the final novel has a problem, you can open the artifacts directory and see exactly which layer introduced it.

## Style packs

Style packs live in `styles/{genre}.md` as editable markdown. They are loaded at runtime (no restart needed) and concatenated onto the L3 system prompt.

**Rationale:** prose style is not a code concern. Users should be able to add new genres or tune existing ones by editing a markdown file. An earlier version hardcoded style strings in `prompts.py`; it was pointed out (correctly) that this violated configuration/code separation.

Planned evolution: replace static style packs with a RAG-retrieved inspiration library. Users would dump favorite novels into a directory; the system would vector-embed them and retrieve relevant snippets at L3 generation time. This is the "Lora-for-text" analogy.

## Language-agnostic but Chinese-first

All prompts, style packs, and example inputs are in Mandarin Chinese. This reflects the original author's use case. The architecture has no language dependency — translating prompts.py and the style packs to English would produce an English-output system.

Schema field names are in English (for code ergonomics); user-facing content (summaries, style guidelines) is in whatever language the prompts are written in.

## Known architectural gaps

These are flagged in the README but worth restating with more technical specificity:

### No final-stage audit
Each audit compares layer N's output against layer N-1. None compare the **final assembled novel** against the **original premise**. This means certain bugs are structurally invisible to the current audit:

- Timeline contradictions that span multiple chapters
- Foreshadows planted in chapter 1 and dropped by chapter 3
- Characters who appear in L1 but vanish by L3
- Main theme drift

The fix is one more layer: `FinalAudit` that takes `(user_input.premise, final_markdown)` and produces a `FinalVerdict(usable: bool, symptoms: list, suspect_layer: str)`. If not usable, the retry hint identifies which upstream layer to bounce back to. This is ~150 LoC and priority for V2.

### Sequential chapter generation
Currently `current_l2_idx` / `current_l3_idx` step through chapters one at a time. The architecture is designed for parallel — chapters share the frozen skeleton, so they *can* be generated simultaneously. The blocker is a consistency step: after parallel drafting, a "negotiation" round where chapters read each other's drafts and adjust for smooth handoffs.

This is analogous to Transformer self-attention (chapter tokens attending to each other) and is on the V3 roadmap.

### Single provider
`HumanQueue` is the only LLM provider. An `AnthropicProvider` that calls the Claude API is straightforward — the challenge is robustness. LLMs return malformed JSON, cut off mid-response, hallucinate schema fields, or refuse to generate. The provider needs retries, JSON repair, schema validation with fallback. This is V2 priority.
