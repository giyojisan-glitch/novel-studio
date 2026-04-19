# Inspiration Map — What to Borrow from Related Projects

After reading the 5 major open-source novel generators, here's a priority-ordered list of what's worth borrowing for NOVEL-Studio and how to integrate each piece.

**Sources studied**: AI_NovelGenerator (4.4k⭐), autonovel (680⭐), AIStoryWriter (231⭐), gpt-author (2.5k⭐), storycraftr (126⭐).

---

## 🔥 Tier 1 — Must Borrow

### 1. ANTI-SLOP word lists + mechanical slop detection
**Source**: `autonovel/ANTI-SLOP.md` + `autonovel/evaluate.py` (lines 123–239)

The killer feature: **a rule-based slop detector that runs without calling any LLM**. It uses three tiers of banned words, 7 structural metrics (em-dash density, sentence length variance, transition word ratio, etc.), and outputs a 0–10 "slop score" that can gate or penalize chapters before the LLM audit even runs.

Tier 1 banned words (directly copyable): `delve, utilize, leverage, facilitate, elucidate, embark, endeavor, encompass, multifaceted, tapestry, paradigm, synergy, holistic, catalyze, juxtapose, myriad, plethora, testament`.

Tier 3 filler phrases (LLM signatures): `"It's worth noting that", "Let's dive into", "Furthermore", "Moreover", "Additionally", "In today's world", "In conclusion", "Not just X, but Y"` — three of these in the same paragraph = auto-reject.

**How to integrate**: create `src/novel_studio/slop_check.py`. Run **before** L3 audit as a cheap pre-filter. Low score → force retry without burning audit heads.

**Effort**: ~150 LoC + regex tables. Half a day.

### 2. Adversarial Edit as the real L4 polish
**Source**: `autonovel/adversarial_edit.py` (lines 90–131)

Brilliant idea: instead of asking "what should I polish?", ask **"cut 500 words from this chapter"** — and watch what the model cuts first. The cuts are labeled (FAT / REDUNDANT / OVER-EXPLAIN / GENERIC / TELL / STRUCTURAL), which gives you a free anti-pattern detector.

**How to integrate**: this becomes the real implementation of L4 polish (currently pass-through). After L3 passes, run adversarial edit, get the cut list, use it as the retry_hint to rewrite the chapter one more time.

**Effort**: ~200 LoC + prompt translation to Chinese. 1 day.

### 3. CRAFT.md structural frameworks
**Source**: `autonovel/CRAFT.md` (lines 10–346)

Five ready-to-use frameworks, each maps to a specific layer in our architecture:

| Framework | Where it goes |
|---|---|
| **Save the Cat beat sheet** (lines 10–41) | L2 chapter outlines — each chapter must match a beat position |
| **Wound / Want / Need / Lie** (lines 110–132) | Add `lie` field to `CharacterCard` schema |
| **MICE quotient** (Card, lines 78–87) | Maps to L1→L4: Milieu→Idea→Character→Event, with reverse-order closure rule |
| **Sanderson's three sliders** (lines 93–105) | Proactivity / Likability / Competence — scoring axes for a new `character` audit head |
| **Stability trap countermeasures** (lines 322–346) | NEW audit checklist: forced transformation, moral ambiguity, information asymmetry, emotional volatility |

**How to integrate**: prompt updates + schema additions. Drip-feed across V2.

**Effort**: ~200 LoC total spread over several PRs.

---

## 🟠 Tier 2 — Strong Recommend

### 4. Scrubber (anti-AI-smell final cleanup)
**Source**: `AIStoryWriter/Writer/Scrubber.py` + `Prompts.py` (lines 493–502)

8-line prompt but crushingly effective. Last pass through the whole book to remove outline residue, editorial asides, and AI tells.

**How to integrate**: second stage of L4 polish (after adversarial edit). Works on the full assembled book, not per-chapter.

**Effort**: ~100 LoC. Half a day.

### 5. LLM Provider factory pattern
**Source**: `AI_NovelGenerator/llm_adapters.py` (lines 36–400+)

9 providers supported via `BaseLLMAdapter` + subclasses (OpenAI / DeepSeek / Gemini / Azure / Ollama / Grok / Anthropic). Smart `check_base_url()` for custom endpoints. Clean factory via config.

**How to integrate**: replaces our current `HumanQueue`-only setup. The blueprint for V2.1 Anthropic Provider — instead of writing one from scratch, port their pattern.

**Effort**: ~300 LoC. 1–2 days.

### 6. Reverse-summary check before audit
**Source**: `AIStoryWriter/LLMEditor.py` (lines 73–95)

Clever: after writing a chapter, summarize it → compare summary to the original outline → generate specific feedback. This is far more precise than "is this chapter good? give a score."

**How to integrate**: insert as a sub-step before L3 audit. The logic head then sees `(outline, rewritten_summary, diff)` instead of `(outline, full_chapter_text)`. Token-efficient too.

**Effort**: ~80 LoC + prompt. Half a day.

### 7. JSON parsing with retry fallback
**Source**: `AIStoryWriter/LLMEditor.py` (lines 47–70)

If JSON parse fails, retry up to 4 times with progressively simplified prompts, then gracefully degrade. Critical for autonomous operation (V2).

**How to integrate**: wrap into the Provider layer from #5.

**Effort**: ~50 LoC. Bundled with #5.

---

## 🟡 Tier 3 — Nice to Have

### 8. Engineering hygiene upgrade
**Source**: `storycraftr/` (pyproject.toml, .pre-commit-config.yaml, CONTRIBUTING.md, CHANGELOG.md, .github/ISSUE_TEMPLATE/)

Turns "MVP hacked together" into "an open-source project someone can actually contribute to":
- `.pre-commit-config.yaml` with Black + ruff + detect-secrets
- `CONTRIBUTING.md` mandating Conventional Commits (`feat(...)`, `fix(...)`, `docs(...)`)
- `CHANGELOG.md` with versioned Added/Fixed/Changed sections
- `.github/ISSUE_TEMPLATE/bug_report.md` and `feature_request.md`
- `CODE_OF_CONDUCT.md`

**Effort**: 2–3 hours total. Zero code changes, pure infrastructure.

### 9. Central `llm/` abstraction module
**Source**: `storycraftr/llm/__init__.py` — exposes 2 factory functions, hides LangChain complexity

Prevents LLM-related dependencies from sprawling across files.

**How to integrate**: bundle with #5 (Provider factory). Create `src/novel_studio/llm/` with `build_chat_model()` and `build_embedding_model()` as the only public API.

**Effort**: folded into #5.

### 10. Checkpoint / resume persistence
**Source**: `AI_NovelGenerator/novel_generator/architecture.py` (lines 28–54) — saves `partial_architecture.json` per step

We already have this via `state.json`, but their `compute_chunk_size()` dynamic token-aware chunking is worth studying when L3 prompts get long.

**Effort**: study only, integrate if/when we hit token ceiling issues.

---

## ❌ What NOT to Borrow

| Rejected | Reason |
|---|---|
| **AI_NovelGenerator's Tkinter GUI** | We're CLI-first; GUI is V5+ if at all |
| **gpt-author's notebook architecture** | It's a 2023 novelty demo, not a design pattern |
| **AIStoryWriter's serial-only chapter generation** | We explicitly want parallel (V3 roadmap) |
| **autonovel's multimedia pipeline** (audiobook, cover art) | Out of scope; we care about text quality |
| **storycraftr's SubAgent + LangGraph complexity** | Over-engineered for our use case — their architecture is for multi-role parallel agents we don't need yet |

---

## 🗺️ Integration Plan (Mapped to Roadmap)

### V2 — Autonomy & Better Review (next 2–4 weeks)

| Order | Item | Source | Effort |
|---|---|---|---|
| V2.0 | Engineering hygiene (#8) | storycraftr | 3h |
| V2.1 | Provider factory (#5 + #9 + #7) | AI_NovelGenerator + storycraftr + AIStoryWriter | 2d |
| V2.2 | Slop mechanical detector (#1) | autonovel | 0.5d |
| V2.3 | Reverse-summary audit (#6) | AIStoryWriter | 0.5d |
| V2.4 | Final-stage audit | (original design) | 1d |
| V2.5 | Foreshadow ledger | (original design) | 1d |

### V3 — Parallel Generation (2–3 weeks after V2)

Original roadmap plus:
- Integrate **Save the Cat beat sheet** (#3) into L2 generation as chapter-position constraint
- Add **Sanderson three sliders** (#3) as new `character` audit head

### V4 — Style Transfer + Polish (larger effort)

| Item | Source | Effort |
|---|---|---|
| L4 adversarial edit (#2) | autonovel | 1d |
| L4 scrubber (#4) | AIStoryWriter | 0.5d |
| Inspiration library RAG | (original design) | 1w+ |
| CRAFT.md Wound/Want/Need/Lie schema (#3) | autonovel | 0.5d |

---

## 🎯 Single Most Impactful Move

If we can only do ONE thing this week, it should be **#1 (slop detector)**. Here's why:

- Zero LLM calls → free & instant
- Catches the most annoying issue (AI-smell) before burning audit credits
- Validated against real data (autonovel's word lists come from actual correction loops)
- ~150 LoC, half a day
- Immediately visible quality uplift

Second-most impactful: **#2 (adversarial edit)** — gives us a real L4 polish for the first time.

Third: **#8 (engineering hygiene)** — signals to potential contributors that this is a real project.

---

## Open Questions Before Implementing

1. **Do we port `prompt_definitions.py` wholesale or keep our own Chinese prompts?** AI_NovelGenerator's prompts are battle-tested but opinionated; ours are custom-fit to the 4-layer architecture. Probably: keep ours, cherry-pick their best sentences.

2. **Does the adversarial edit make sense for short fiction (3 chapters × 800 words)?** Their design targets longer work. May need to scale down (cut 150 words instead of 500) for our short-form defaults.

3. **Should slop detection block audit, or just feed forward as a score?** Blocking is safer but slower (more retries). Feeding forward keeps flow but may let slop through if the LLM head is too lenient. **Recommendation: feed forward initially, upgrade to blocking once we calibrate thresholds.**

4. **How much of CRAFT.md translates to Chinese fiction?** Beat sheets and MICE are Western structuralist. Some may need adaptation for 网文 conventions.
