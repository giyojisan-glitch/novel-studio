# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **V2 pipeline scaffolding** (opt-in via `--v2` flag on `init`):
  - `FinalVerdict` schema — whole-book audit against premise
  - Final-stage audit step that catches cross-layer bugs (timeline, foreshadow, character collapse)
  - Suspect-layer bounce-back: failed final audit returns to L1/L2/L3 as suspect_layer dictates
  - `L4_adversarial_N` step — model cuts ~15% of chapter, categorizes cuts (FAT/TELL/GENERIC/...)
  - `L4_scrubber_N` step — applies cuts + anti-slop cleanup, produces publication-grade text
  - `L4PolishedChapter` now has real fields (`adversarial_cuts`, `polish_notes`), not just pass-through
- **LLM Provider abstraction** (`src/novel_studio/llm/`):
  - `BaseProvider` with request/query interface
  - `HumanQueueProvider` — current MVP behavior (file-based human-in-loop)
  - `StubProvider` — deterministic responses for tests (no LLM calls)
  - `AnthropicProvider` — skeleton for V2.1 real-API implementation
  - `get_provider()` factory with `NOVEL_STUDIO_PROVIDER` env var support
- **Wound/Want/Need/Lie framework** on `CharacterCard` (optional fields, from autonovel/CRAFT.md)
- **Foreshadow ledger** on `L2ChapterOutline`: `foreshadow_planted` and `foreshadow_paid`
- Engineering hygiene: `.pre-commit-config.yaml`, `CHANGELOG.md`, `CONTRIBUTING.md`, issue templates
- **Mechanical slop detector** (`slop_check.py`) inspired by autonovel/ANTI-SLOP.md
  - 7-category rule-based detection: Tier 1/2/3 vocab, scene/rhetoric/dialogue cliches
  - 6 structural metrics (em-dash density, sentence variance, transition ratio, etc.)
  - Runtime-loaded rules from `styles/_anti_slop.md` — editable without code changes
  - `novel-studio slop <file>` CLI subcommand
  - Per-chapter slop report auto-generated in `artifacts/.../05_slop_report.md`

### Changed
- `NovelState.user_input` gains `pipeline_version` field (`"v1"` default, `"v2"` opt-in)
- `decide_next()` and `advance()` extended to route between v1 and v2 pipelines
- `finalize` step in v2 uses L4 content (real polish); v1 still passes L3 through

### Documentation
- `docs/INSPIRATION_MAP.md` — systematic borrowing plan from 5 reference projects
- README updated with explicit architecture diagram and limitation map

---

## [0.1.0] — 2026-04-19

### Added
- Initial MVP with layered state machine (L1/L2/L3, L4 pass-through)
- Multi-head audit (logic + pace) with vote-based aggregation and retry gates
- Cross-attention-style layered prompting
- Six genre style packs (`styles/*.md`) loaded at runtime
- Artifact export pipeline (`artifacts/{timestamp}/`)
- CLI: `init` / `step` / `status` / `artifacts`
- Human-in-the-loop provider (Claude Code session mode)
- Three end-to-end demo generations validating architecture

### Documentation
- Core README, ARCHITECTURE.md, ROADMAP.md
- CLAUDE.md with collaboration protocol for AI agents
