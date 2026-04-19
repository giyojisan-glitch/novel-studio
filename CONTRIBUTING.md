# Contributing to NOVEL-Studio

Thanks for considering a contribution. This project is an architectural experiment — the most useful contributions are the ones that push the architecture forward, not just polish the surface.

## Before you start

1. Read `README.md` for the vision and `docs/ARCHITECTURE.md` for why decisions were made this way
2. Read `docs/ROADMAP.md` for the prioritized list of what's missing
3. Read `docs/INSPIRATION_MAP.md` for what's worth borrowing from similar projects

If the thing you want to build isn't on the roadmap, open an issue first to discuss.

## Development setup

```bash
git clone https://github.com/giyojisan-glitch/novel-studio
cd novel-studio
uv sync
uv run pytest          # expect all tests green
```

Optional: install pre-commit hooks (format + lint on every commit):

```bash
uv run pre-commit install
```

## Running the MVP

The default provider is `HumanQueueProvider`, which only works inside a Claude Code session. For development without an LLM:

```bash
NOVEL_STUDIO_PROVIDER=stub uv run novel-studio init --file inputs/_EXAMPLE_xianxia --genre 仙侠 --chapters 3
```

(Note: full Stub-driven end-to-end flow is still scaffolding — see V2.1 roadmap.)

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>
```

Types:
- `feat` — new feature
- `fix` — bug fix
- `docs` — docs only
- `refactor` — code change that neither fixes a bug nor adds a feature
- `test` — adding or fixing tests
- `chore` — tooling, dependencies, etc.

Examples:
- `feat(slop): add mechanical slop detector inspired by autonovel`
- `fix(engine): prevent infinite retry loop when audit always fails`
- `docs(readme): clarify v2 pipeline opt-in`

## Pull request checklist

- [ ] Tests pass: `uv run pytest`
- [ ] New features have tests (per `CLAUDE.md` "代价真实" rule)
- [ ] If you added/changed a user-editable config (styles/, inputs/), document it in `CHANGELOG.md`
- [ ] If you added/changed architecture, update `docs/ARCHITECTURE.md`
- [ ] If you completed a roadmap item, mark it in `docs/ROADMAP.md`
- [ ] Commit messages follow Conventional Commits

## Architecture invariants (don't break these)

1. **Configuration/data lives outside code.** Prompts, style packs, word lists belong in `styles/` and `inputs/`, not hardcoded in `.py` files. If you find yourself adding a dict of user-facing strings to code, stop and move it to a file.

2. **Every layer's output is frozen before the next layer reads it.** L3 writing does NOT modify L2 outlines. This is what makes parallel chapter generation possible (V3) without introducing race conditions.

3. **Audit heads are independent.** Don't make one head depend on another's output. Parallel dispatch + aggregation is the design.

4. **Backward compatibility for `state.json`.** Adding fields is fine (use `default=`); removing or renaming fields is a breaking change. The v1 pipeline must keep working for existing projects.

5. **Provider abstraction must stay transport-agnostic.** A `BaseProvider` that works for `HumanQueue` (file-based async) must also work for `Anthropic` (sync API). Don't leak one transport's assumptions into the base class.

## Questions

Open an issue, or reach out via `giyojisan@gmail.com` / LINE `eggpunchman`.
