# 独有提交计算脚本与中间文件

以下为标准操作流程；执行 fetch、生成文件及长任务仍须遵守用户授权、资源限制和 30 分钟时限。这些命令模板不是自动执行授权。

1. **缓存双方完整提交**：使用 `scripts/cache_repo_commits.py`，先源仓库、再 Fork，串行运行，不使用 `--max-commits`（该参数只适合抽样烟测，不能据此制定完整功能迁移计划）。在独立分析批次目录中保存缓存，避免覆盖旧轮次：

   ```bash
   python scripts/cache_repo_commits.py --repo <源OWNER/REPO> --branch <源分支> --cache-dir <本轮目录>/commits/source --git-timeout 180
   python scripts/cache_repo_commits.py --repo <ForkOWNER/REPO> --branch <Fork分支> --cache-dir <本轮目录>/commits/fork --git-timeout 180
   ```

   缓存按源侧/Fork 侧隔离；同一侧若分析多个分支，再使用独立分支目录，因为脚本文件名不包含分支，同仓库共用目录会覆盖。每个仓库输出 `<OWNER>__<REPO>.csv`，包含 `branch_tip_sha`、提交 SHA、作者身份、作者/提交时间、message、stable patch-id、patch 状态、增删行数及 `file_changes`。脚本当前会自行从 GitHub fetch 到临时裸仓库，结束后清理该临时仓库；它没有本地仓库路径或固定 SHA 输入参数，不能假设它会复用当前工作区 Git 对象。移植用的源码 Git 对象须另外保留。核对缓存 `branch_tip_sha` 与已记录快照一致、`scan_scope=full`、提交记录完整；若分支在获取期间变化，应重新对齐快照与缓存，不混用不同版本的结果。

2. **匹配双方缓存**：使用 `scripts/match_cached_commit_patches.py`，不使用 `git log source..fork`、GitHub ahead/behind 或 `scripts/analyze_fork_commits.py` 的 SHA 差集代替 stable patch-id 判定：

   ```bash
   python scripts/match_cached_commit_patches.py \
     --source-csv <本轮目录>/commits/source/<源OWNER>__<源REPO>.csv \
     --fork-csv <本轮目录>/commits/fork/<ForkOWNER>__<ForkREPO>.csv \
     --output-prefix <本轮目录>/comparison
   ```

   当前匹配脚本的源作者别名由 `SOURCE_AUTHOR_NAMES` 和 `SOURCE_AUTHOR_EMAILS` 常量定义，并非命令行参数。每次须核对是否适用于该源仓库；切换源仓库时若不适用，先提出脚本调整方案并获确认，不能直接沿用错误身份配置。匹配先 SHA，再对未匹配提交按 stable patch-id 一对一匹配；重复 patch-id、多次提交、squash、拆分或改写可能导致未匹配，不能把未匹配自动等同于缺失功能。

3. **保留并使用实际输出**（以 `comparison` 为前缀）：

   | 文件 | 后续用途 |
   | --- | --- |
   | `commits/source/<OWNER>__<REPO>.csv` 与 `commits/fork/<OWNER>__<REPO>.csv` | 完整原始计算输入；核对快照、时间、身份和 patch 状态，支持复算与下一轮比较。 |
   | `comparison_commit_matches.csv` | 双方所有提交的分类及匹配对象。用于追溯为何纳入/排除，并筛选 `side=source` 且 `match_type=source_unique_patch` 的源侧未匹配提交，评估 Fork 未吸收的主线更新；单独复核 `merge_skipped`、`empty_patch`、`patch_error` 和 `fork_source_author_commit`。 |
   | `comparison_fork_unique_commits.csv` | `fork_unique_patch` 候选清单，是功能识别的主要输入。读取 message 和 JSON `file_changes`，再通过原 Git 对象查看实际 diff、依赖和后续修复；将提交/路径映射到迁移计划中的功能，不直接逐行机械移植。 |
   | `comparison_summary.json` | 程序可读统计，包含输入路径、分类数量等；用于检查总量、独有数量和异常分类，与实际 CSV 交叉验证。源码 SHA/时间还须从缓存和 Git 记录，不能假设汇总自带全部快照元数据。 |
   | `comparison_summary.csv` | 单行统计归档，供表格查看或批次对比；不能代替提交明细和功能迁移计划。 |

   匹配脚本当前只生成上述 4 个 `comparison_*` 文件，不自动生成 `source_missing_commits.csv` 或 `excluded_or_unresolved_commits.csv`；如确需单独导出，先在计划中明确授权范围、筛选条件和路径，并标明其派生自 `comparison_commit_matches.csv`。CSV 内 message/file_changes 可含换行和超大字段；使用 `csv.DictReader`、`encoding="utf-8-sig"` 及足够的 `csv.field_size_limit` 读取，不用 `wc -l` 计算提交条数。

4. **由结果制定功能迁移计划**：先检查分类总量与输入提交数一致、是否有 patch 错误或部分缓存。对独有候选按功能和依赖归并，检查当前主线是否已有等价能力，列明迁移/无需迁移/阻塞及证据；将本轮目录、双方缓存、4 个结果文件、脚本版本和作者别名配置记录到计划中。用户确认完整功能计划后才按项移植，并把实际原始提交和文件来源写入进度/历史。

5. **保留与再次迁移**：计划放在 `docs/integration/`，计算数据使用独立的 `<本轮目录>`，不散落根目录；每轮使用新目录并记录路径，不覆盖历史证据。是否提交分析数据须单独确认，临时目录可能丢失，不视为永久归档；用户要求删除后按授权删除，后续需要时说明缺失并按新授权复算。下一轮针对最新双方快照重新计算，结合上一轮功能/文件迁移历史核对新增或改动；重写、兼容修改会改变 patch-id，因此不能只靠本轮匹配判断此前功能是否已移植。

6. **批量筛选的边界**：`scripts/batch_fork_unique_commits.py` 用于多个 Fork 的串行筛选和汇总，不替代单仓库迁移审计。默认只保留各 Fork 独有提交 CSV，并清理 Fork 完整缓存和临时匹配结果；迁移任务优先采用上述两阶段流程保留全部证据。批量流程会涉及清理，执行前须确认该范围；读取汇总时按仓库取最新记录，核对缓存快照，不把历史重试或 `--resume` 的旧成功记录当作最新源码结果。

### 续接与源码异常

- 续接优先复用与已确认 SHA 一致的完整缓存，不重新抓取移动分支替换原基准。源码对象/缓存缺失时先报告；恢复须核对原 SHA，无法恢复则暂停并请求新轮次或脚本适配方案，不将新快照冒充旧快照。
- 远端 force-push 后，旧新快照可能无祖先关系；不能仅用提交范围差集做增量。按固定快照重新计算并结合实际文件和历史证据核对，保留旧快照记录。
- 功能识别覆盖新增、修改、删除、重命名、二进制资源和数据/schema 变化；提交 message 只是线索，以实际 diff 为依据。记录下游依赖与迁移/回退风险。
- 整文件复制前检查是否夹带未批准功能、废弃逻辑或无关配置；原依赖、许可证和第三方声明随功能核对。检查安装钩子、构建/启动脚本及敏感配置，不执行未经批准的脚本、不复制凭据；风险无法接受时暂停，不自写替代实现。
