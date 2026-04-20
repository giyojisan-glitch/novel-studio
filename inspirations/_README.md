# Inspirations · Lora-Style Library for Fiction

这是 NOVEL-Studio 的**灵感库**——用来实现"Lora 式风格迁移"的数据源。

## 目录约定

```
inspirations/
├── {作家名}/           ← 按作家分组
│   ├── {作品名}.txt    ← 原文（纯文本，UTF-8）
│   └── ...
├── ...
└── _README.md
```

示例：
```
inspirations/
├── 汪曾祺/
│   ├── 受戒.txt
│   ├── 大淖记事.txt
│   └── 陈小手.txt
├── 蒲松龄/
│   └── 聂小倩.txt
└── 沈从文/
    └── 边城_节选.txt
```

## 消化流程

```bash
# 1. 把想参考的作品放进 inspirations/{作家}/{作品}.txt
cp ~/Downloads/受戒.txt inspirations/汪曾祺/

# 2. 消化（读 → chunk → embedding → 存 Chroma）
uv run novel-studio inspire ingest

# 3. 查询测试
uv run novel-studio inspire query "雪夜、钟楼、哑僧" --top 3
```

## 运行时行为

生成小说时，L3 prompt 会：
1. 用 L2 章节 summary + hook 做 query
2. 从灵感库检索 top-3 相似片段
3. 把片段作为**风格参考 few-shot** 注入 prompt
4. AI 看到片段后能更准地复刻语感（比静态 style pack 强一个数量级）

## 为什么这个比 styles/*.md 好

| 维度 | styles/*.md 静态 | Inspirations RAG |
|---|---|---|
| 源 | 我手写的 500 字指令 | 真实作家原作 |
| 风格保真 | 靠指令描述（"白描"）| 靠**具体段落**示范 |
| 适应性 | 所有章节同一指令 | 每段检索最相似的片段 |
| 扩展 | 改代码加 style pack | 丢文件进 inspirations/ |
| 衰减 | 长篇会遗忘 | 每段重新检索，不衰减 |

## 版权

`inspirations/` 目录**已在 `.gitignore`**（私人素材不上 GitHub）。你可以塞任何自己有权使用的作品，不用担心上游推送。
