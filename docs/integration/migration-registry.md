# Fork 迁移总索引

| 仓库标识 | 仓库地址 | 移植标识 | 活动/最近轮次 | 状态 | 代码进度 | 验证进度 | 计划文档 | 更新时间 |
|---|---|---|---|---|---|---|---|---|
| `github.com/shy3130/tick-stock-panel` | `https://github.com/shy3130/tick-stock-panel` | `fork-feature-migration` | `r2`（含 r1 历史） | 规划中 | r2 候选分析完成 | 待下一项源码审计确认 | [fork-feature-migration-plan.md](fork-feature-migration-plan.md) / [migration-task-tracker.md](migration-task-tracker.md) | 2026-10-17T15:00:00+08:00 |

## 说明

本轮目标仓库为 `luhuiyuan/tick-stock-panel` 的 `fork-integration` 分支，源仓库为 `shy3130/tick-stock-panel`。详细快照、功能范围、阶段计划、源码下载约束、证据和追加历史以计划文档为准。迁移实施前必须建立本地源码快照，不得仅依据分析 CSV 或提交标题改写功能。


## 轮次记录

| 轮次 | 范围 | 结果 | 证据 | 状态 |
|---|---|---|---|---|
| `r1` | 前 15 个 Fork | 2,770 条独有提交、895 个候选功能；已形成迁移计划并记录 AkShare、eltdx、P2B-003 等后续状态 | `analysis/fork-analysis/` | 历史轮次 |
| `r2` | 按 `fork_change_ranking.csv` 排名前 80 个 Fork | 80 条唯一汇总记录，79 个成功、1 个不可访问；1,643 条候选功能、4,224 个关联提交 | `analysis/fork-analysis/` | 候选分析已合并，待源码审计确认 |

`r2` 仅扩大候选发现范围，不自动扩大业务迁移授权。所有新数据源候选必须经过仓库外固定源码、原始 diff、配套测试和用户确认门禁。
