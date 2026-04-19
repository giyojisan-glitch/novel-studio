# NOVEL-Studio · Claude 协作指南

## 这是什么项目

一个「一句话 → 完整小说」的工具。核心架构：
- **多层状态机**（L1 骨架 → L2 章节梗概 → L3 段落写作 → L4 润色）
- **Multi-Head Audit**（多视角并行审稿）
- **验收门 + 重试**（每层最多 2 次重写，超限强制放行）

## MVP 模式：方案 B（Human-in-the-loop）

当前 MVP 采用 **HumanQueueProvider**：Python 引擎跑到 LLM 调用点时，把 prompt dump 到文件 → 等当前对话里的 Claude 响应 → 读响应继续推进。

**对话里的 Claude 必须遵守的工作流**：

1. 用户运行 `novel-studio init "一句话"` 后，会在 `projects/{slug}/queue/` 出现 prompt 文件（如 `01_l1_skeleton.prompt.md`）。
2. 你读 prompt 文件 → 思考 → 生成严格符合 schema 的 JSON → 写到 `projects/{slug}/responses/{step_id}.response.json`。
3. 用户（或你自己用 Bash）运行 `novel-studio step projects/{slug}/`，引擎会读响应、更新 state、dump 下一步 prompt。
4. 重复直到 `final.md` 生成。

## 你扮演的角色

按 step 类型扮演不同角色：
- `l1_skeleton` → 资深类型小说策划
- `l2_chapter_{i}` → 结构编辑
- `l3_paragraph_{i}` → 网文写手（中文文风，自然，有画面感）
- `audit_logic_{layer}_{i}` → 严格的逻辑编辑
- `audit_pace_{layer}_{i}` → 节奏感编辑

prompt 文件里会写明你当前的角色和任务，请严格按指示输出。

## 严格输出 JSON

所有响应必须是**严格的 JSON**，写到 `responses/{step_id}.response.json`。
- 不要用 markdown 代码块包裹
- 不要写解释文字
- 必须能被 `json.loads()` 解析
- 必须符合 prompt 文件里给出的 schema

## 详细架构

见 `docs/ARCHITECTURE.md` 和 `docs/ROADMAP.md`。

## 启动命令

```bash
cd /Users/punpun/NOVEL-Studio
uv sync                                                   # 装依赖
uv run novel-studio init "一句话" --genre 科幻 --chapters 3
# Claude 响应 queue 里的 prompt → 写到 responses
uv run novel-studio step projects/<最新>/                 # 推进
# 循环直到完成
uv run novel-studio status projects/<最新>/               # 查进度
```
