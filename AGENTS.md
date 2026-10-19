# AI 开发入口

修改、调试或审查本仓库前，必须完整阅读并遵循根目录的 [`CONTRIBUTING.md`](CONTRIBUTING.md)。其中定义了项目架构、数据契约、数据源插件化、缓存与性能要求、测试矩阵以及 PR 复审和合并标准。

涉及代码二次开发、前端插槽、后端可替换策略、扩展注册或上游升级兼容时，还必须阅读 [`docs/secondary-development.md`](docs/secondary-development.md)。该文档区分当前已实现能力与目标扩展契约；不得根据设计示例虚构尚不存在的 API。

同时遵守以下规则：

- 先理解调用链和现有测试，再进行修改。
- 保持实现简单、改动范围最小，不处理无关问题。
- 不覆盖工作区已有修改，不虚构测试或审查结果。
- 以实际验证结果作为完成标准。

## Fork 仓库功能迁移续作指令

当用户发出 **“继续 fork 仓库功能迁移”** 时，必须先阅读并遵循以下文档：

- [`docs/integration/fork-feature-migration-plan.md`](docs/integration/fork-feature-migration-plan.md)：迁移范围、阶段顺序、源码溯源约束和验收门禁；
- [`docs/integration/migration-registry.md`](docs/integration/migration-registry.md)：源仓库、迁移轮次和计划入口；
- [`docs/integration/migration-task-tracker.md`](docs/integration/migration-task-tracker.md)：当前任务状态、断点、下一步和验证记录。

然后按任务台账的 `status`、`last_completed_step` 和 `next_step` 继续执行，不重复已完成步骤，不覆盖工作区已有修改。迁移前必须将相关源仓库源码下载到本仓库之外的临时目录，并固定分支和完整 commit SHA；不得把源仓库源码下载到当前项目的 `analysis/`、`docs/` 或其他目录；所有源码变动以功能关联仓库的固定源码、原始 diff 和配套测试为准，禁止无证据自行重写平行实现。每完成一个可验证步骤，立即更新任务台账。每个功能开始迁移前，必须先向用户说明迁移意义、用户将获得的能力、变更范围、风险和验证方案，并等待用户明确确认；未确认前不得修改目标代码。每个功能迁移完成自动验证后，必须暂停并进入人工确认；只有人工确认结论为“通过”且已写入任务台账，才能开始下一功能迁移。涉及代码修改、系统状态变化、提交或推送时，仍须遵循本文件及用户确认和 Git 提交策略。

## 非 Docker 本机启动

本仓库可直接使用本机依赖启动，无需 Docker。适用于本机开发或受控的单机部署：

```bash
# 后端：在仓库根目录执行，读取根目录 .env
cd backend
uv run --no-sync python -m uvicorn app.main:app --env-file ../.env --reload --host 0.0.0.0 --port 3018

# 前端开发服务器：另开终端，在仓库根目录执行；仅绑定本机
cd frontend
BACKEND_HOST=127.0.0.1 BACKEND_PORT=3018 pnpm dev --host 127.0.0.1 --port 3011
```

- 后端 `3018` 可托管已构建的 `frontend/dist` 静态页面，因此在该目录存在有效构建产物时，可通过 `http://<服务器地址>:3018/` 同时访问前端和 API。
- `3011` 是 Vite 前端开发服务器；公网部署时应保持只监听 `127.0.0.1`，无需向公网开放该端口。
- 后端绑定 `0.0.0.0` 时会监听所有网卡。公网访问还要求云安全组和系统防火墙放行 TCP `3018`；应优先将入站来源限制为可信 IP，并确认访问密码等保护已配置。不要在未评估访问控制的情况下直接暴露服务。
- 若只需本机访问，将后端 `--host` 改为 `127.0.0.1`。启动两个进程的终端按 `Ctrl-C` 分别停止对应服务。
- `./dev.sh` 会让前后端共用配置的 host；需要后端公网可达、但前端仍限本机时，不要直接用 `HOST=0.0.0.0 ./dev.sh`，应按上面方式分别启动。
