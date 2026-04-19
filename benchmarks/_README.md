# Benchmarks · TDD-style evaluation

## What this is

Put your real human-written short stories into `corpus/*.md`. Then run:

```bash
uv run novel-studio benchmark benchmarks/corpus/ --provider anthropic
# or single file:
uv run novel-studio benchmark-one benchmarks/corpus/my_story.md --provider anthropic
```

The framework will:
1. **Extract a ~150-word premise** from each story (LLM逆向提取)
2. **Run NOVEL-Studio** on that premise (same as any other generation)
3. **LLM-as-judge** scores the generated novel against the original across 6 dimensions
4. **Report** pass/fail (≥0.70) per case + batch summary

## Directory structure (most are gitignored)

```
benchmarks/
├── corpus/           # ← YOU put your short stories here (.md)
├── premises/         # ← auto: extracted premises
├── generated/        # ← auto: NOVEL-Studio output
├── projects/         # ← auto: intermediate state/queue/responses
└── reports/          # ← auto: per-case + _SUMMARY.md
```

## Dimensions (weighted)

| Dimension | Weight | What it measures |
|---|---|---|
| `plot_structure` | 25% | 三幕节奏/关键事件序列 |
| `character_core` | 20% | 主角 want/need/wound |
| `world_anchors` | 15% | 核心硬规则保留 |
| `tone` | 15% | 基调一致性 |
| `ending_vector` | 15% | 结局方向 |
| `key_scenes` | 10% | 关键画面/物件 |

**Pass threshold: overall score ≥ 0.70**

## Why this matters

This is the TDD of novel generation: real human-written stories are the
"expected output", pass rate is the only credible measure of architectural
progress. Any prompt tweak / new audit head / model change should show up
as a measurable shift here.
