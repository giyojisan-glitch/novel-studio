# Roadmap

Priority-ordered list of what's missing and what it would take to build.

## V1 — MVP (current)

**Status:** ✅ Complete.

- Layered state machine (L1 → L2 → L3, L4 pass-through)
- 2-head audit (logic + pace) with aggregation and retry gates
- Cross-attention-style layered prompting
- Style packs as runtime-loaded markdown files
- Artifacts export for debugging
- Human-in-the-loop provider (runs only in Claude Code sessions)
- CLI: init / step / status / artifacts

**Validated with 3 end-to-end demos** (sci-fi, xianxia, urban drama). Core mechanics work. Cross-layer bugs exist but are identified in the README.

---

## V2 — Autonomy & better review (highest priority)

These are the minimum additions to make the system *useful to someone who isn't the author*.

### V2.1 — API-based LLM provider

**Goal:** replace `HumanQueue` with a provider that calls a real API.

**Scope:**
- `AnthropicProvider` — calls Claude API, handles schema validation, retries on malformed JSON, structured output via `response_format`
- `OpenAIProvider` — same, for GPT-4 family
- (Optional) `OllamaProvider` for local models

**Hard parts:**
- LLMs sometimes return truncated JSON. Need robust parsing with fallback to prompt-repair ("your previous response had an error: X; please regenerate").
- Schema violations need explicit retries, not silent pass-through.
- Rate limiting, cost tracking, graceful degradation.

**Estimated effort:** 200–400 LoC per provider.

### V2.2 — Final-stage audit

**Goal:** catch cross-layer bugs that currently slip through.

**Scope:**
- New schema: `FinalVerdict(usable: bool, symptoms: list, suspect_layer: Literal["premise", "L1", "L2", "L3"], retry_hint)`
- New step in `engine.advance()`: after L3_audit for the last chapter, before `finalize`
- Prompt takes `(user_input.premise, full_assembled_markdown)` and asks a strict reviewer:
  1. Is this a usable novel? (binary)
  2. If not, specific symptoms (timeline, foreshadow, character, theme)
  3. Which upstream layer is the suspected source
- If not usable: reset `next_step` to suspect layer, force that layer's revision counter to reset (or keep incrementing; configurable)

**Hard parts:**
- Prompt calibration. A reviewer that's too strict never passes; too lenient is useless.
- Deciding the retry strategy when `suspect_layer` is upstream (e.g., rejecting all L2 outlines because L1 was flawed requires cascade invalidation).

**Estimated effort:** ~150 LoC + significant prompt iteration.

### V2.3 — Two more audit heads

**Goal:** complete the 4-head design.

**Scope:**
- `character` head: consistency of voice, motivation, arc progression across chapters
- `style` head: match to style pack, lexical variety, sentence-length variance

**Hard parts:**
- Character head needs cross-chapter context; it can't judge chapter 3 without seeing chapter 1 and 2.
- Style head needs reference — what does "cold noir" actually look like in prose? Probably needs the inspiration library to be informative.

**Estimated effort:** 50 LoC per head + 200 LoC if style head needs RAG.

### V2.4 — L2 foreshadow ledger

**Goal:** prevent dropped foreshadows (one of the 3 real bugs found in the urban drama demo).

**Scope:**
- Extend `L2ChapterOutline` schema with `foreshadow_planted: list[str]` and `foreshadow_paid: list[str]` (referring to ledger IDs from earlier chapters)
- L2 prompt becomes cross-chapter aware: when outlining chapter K, it's shown the cumulative ledger from chapters 1..K-1 and must decide what to plant / pay
- L2 audit gains a new check: by the last chapter, the ledger should be balanced (every planted foreshadow is paid, unless explicitly marked `carry_to_sequel`)

**Estimated effort:** ~100 LoC + prompt changes.

---

## V3 — Parallel generation (architectural evolution)

### V3.1 — Parallel L2 generation with negotiation

**Goal:** generate all chapter outlines simultaneously, then reconcile.

**Scope:**
- Two-pass L2:
  1. **Pass 1 (parallel):** N chapter outlines generated concurrently, each seeing only L1
  2. **Pass 2 (negotiation):** all N outlines shown to each chapter agent; each agent updates its outline to ensure smooth handoffs and consistent character state
- Requires a shared `BlackboardState` read by all agents in pass 2

**Hard parts:**
- Naive pass 2 can cause oscillation (chapter K-1 changes to match chapter K, chapter K+1 updates to match new chapter K-1, etc.). Need a convergence criterion or fixed 2–3 rounds.
- Parallel calls must be rate-limit aware.

**Estimated effort:** ~300 LoC, mostly in engine.py and a new blackboard module.

### V3.2 — Parallel L3 generation

**Goal:** write all chapters simultaneously once L2 is frozen.

**Scope:**
- L2 must be fully approved before L3 starts (hard gate)
- All N L3 chapters dispatched as parallel `asyncio` tasks
- Each chapter's L3 prompt uses its L2 outline + shared L1 skeleton; last-150-chars previous-chapter is replaced with `prev_connection` only (since no chapter is "previous" in parallel dispatch)
- A post-pass "continuity smoother" reads the full draft and adjusts transitions at chapter boundaries

**Hard parts:**
- Without the "last 150 chars of previous chapter" hint, style drift between chapters is likely. The continuity smoother is the answer but nontrivial.

**Estimated effort:** ~400 LoC including continuity smoother.

### V3.3 — LangGraph migration

**Goal:** replace hand-written step engine with LangGraph.

**Scope:**
- Port `engine.advance()` to a LangGraph `StateGraph`
- Retain `NovelState` as the graph state
- Use LangGraph's built-in interrupt/resume for human-in-the-loop
- Benefits: native parallelism via `Send`, easier to extend with conditional branches

**Hard parts:**
- Mostly mechanical; the main question is whether LangGraph's abstractions are worth the dependency weight.

**Estimated effort:** ~200 LoC refactor, no new features.

---

## V4 — Style transfer (the "Lora for text" idea)

### V4.1 — Inspiration library

**Goal:** replace static style packs with RAG-retrieved style exemplars.

**Scope:**
- Users drop favorite novels/short stories into `inspirations/{author_or_label}/*.txt`
- On ingestion: chunk by paragraph or scene, embed with `BAAI/bge-large-zh-v1.5` (for Chinese) or similar, store in Chroma/LanceDB
- At L3 generation: retrieve top-K chunks matching the current chapter's tone/topic, include as "style reference excerpts" in the system prompt
- At L4 polish (when implemented): retrieve for final pass

**Hard parts:**
- Chunking strategy matters a lot for fiction (paragraph vs scene vs page).
- Retrieval quality: matching by topic is easy; matching by *style* is hard. Probably need a style-focused embedding model or dual retrieval.
- Copyright implications for including excerpts in generated work; probably the inspiration library should only be used as style conditioning, not for direct quotation.

**Estimated effort:** ~500 LoC + a meaningful evaluation framework.

### V4.2 — Actual L4 polish

**Goal:** the currently pass-through L4 actually does something.

**Scope:**
- Read all L3 drafts
- Retrieve style references (V4.1)
- Pass each chapter through a polish prompt that: unifies voice, tightens sentences, inserts motif callbacks, deepens imagery
- Respect the user's style preferences (terse vs ornate, dialogue-heavy vs introspective)

**Hard parts:**
- Easy to overpolish ("AI-slopify"). Need strict restraint in the prompt.
- Balancing polish with original voice.

**Estimated effort:** ~200 LoC + heavy prompt iteration.

---

## V5+ — Speculative / research-level

Not for the first PR wave, but interesting directions:

### Chapter-level self-attention (generalization of V3.1 negotiation)
Instead of a 2-pass negotiation, make the L2 generation itself iterative: all chapters updated in each pass until global consistency converges. This is the closest analog to Transformer self-attention among all proposed features.

### Learned retry policies
Rather than fixed `MAX_REVISION = 2`, learn per-layer and per-genre how many retries typically improve quality vs waste budget.

### Audit head ensembles with disagreement routing
If two heads give wildly different scores, escalate to a tiebreaker head (or a human). Treat disagreement as signal.

### Fiction-specific evaluation metrics
Beyond audit scores: readability, sentence variance, dialogue ratio, protagonist screen time. Not LLM-judged but computed from the text.

### Multi-lingual generation with style preservation
Write the skeleton in English, the novel in Chinese — or vice versa. Useful for authors working across languages.

---

## Priority summary

If you want to contribute, here's the rough priority order:

1. **V2.1 AnthropicProvider** — unblocks everyone who doesn't have a Claude Code session
2. **V2.2 Final-stage audit** — the single biggest quality improvement available
3. **V2.4 Foreshadow ledger** — addresses a concrete bug seen in demos
4. **V2.3 Character + style heads** — complete the 4-head design
5. **V3.1 Parallel L2 with negotiation** — starts unlocking the speed claim
6. **V4.1 Inspiration library** — the signature "Lora-for-text" feature

V2 as a whole is maybe 2–4 weeks of focused work. V3 is another 2–3 weeks. V4 is a larger commitment — think of it as a separate research project that shares the architecture.
