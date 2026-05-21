# 问题追踪：本地 Markdown

本仓库的问题、PRD 和讨论记录都保存在 `.scratch/` 目录下。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`
- PRD 文件：`.scratch/<feature-slug>/PRD.md`
- 实施 issue 文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- 编号从 `01` 开始
- 任务状态写在 issue 文件顶部的 `Status:` 行
- 评论和来回讨论追加到文件底部的 `## Comments` 下面

## 技能说“发布到 issue tracker”时

在 `.scratch/<feature-slug>/` 下新建文件或目录即可，必要时同时创建父目录。

## 技能说“取回相关 ticket”时

直接读取对应路径下的 Markdown 文件。通常用户会直接给出路径或 issue 编号。
