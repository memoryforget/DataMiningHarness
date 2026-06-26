# DataMiningHarness

`DataMiningHarness` 用来批量跑 benchmark，并对生成的报告执行评测流水线。

支持 3 套本地批跑入口：

- `run_all_benchmarks_opencode.sh`
- `run_all_benchmarks.sh`
- `run_all_benchmarks_claude.sh`

对应单 benchmark / 单批任务入口：

- `run_opencode_locally.sh`
- `run_codex_locally.sh`
- `run_claude_code_locally.sh`

安装和环境准备请先看 `INSTALL.md`。

## 1. 目录约定

所有路径都按相对路径理解。

- 仓库根目录：`./`
- 工作区根目录：`../`
- benchmark 根目录：`../InifiteEDA_data_lake/`
- Codex 配置目录：`../.codex/`
- OpenCode 配置目录：`../.opencode/`
- Claude Code 配置目录：`../.claude/`
- MinerU API 目录：`./mineru-api/`

## 2. 先启动 MinerU API

批跑前必须先启动本地 `mineru-api` 服务，因为 PDF 解析依赖它。

仓库里已经提供了辅助脚本：

```text
./mineru-api/
├── README.md
├── mineru_api.env.example
├── start_mineru_api.sh
├── start_mineru_api_bg.sh
├── stop_mineru_api.sh
└── healthcheck_mineru_api.sh
```

先复制配置模板：

```bash
cp ./mineru-api/mineru_api.env.example ./mineru-api/mineru_api.env
```

然后启动：

```bash
bash ./mineru-api/start_mineru_api_bg.sh
bash ./mineru-api/healthcheck_mineru_api.sh
```

默认传给 harness 的地址：

```text
http://127.0.0.1:18000
```

## 3. 配置文件放在哪里

### 3.1 Codex

- `../.codex/config.toml`
- `../.codex/auth.json`

### 3.2 OpenCode

- `../.opencode/opencode.json`

### 3.3 Claude Code

- `../.claude/settings.json`

详细模板见 `INSTALL.md`。

## 4. Benchmark 输入位置

默认 benchmark 根目录：

```text
../InifiteEDA_data_lake/
```

脚本会自动扫描：

```text
benchmark_*.json
```

## 5. 推荐输出目录约定

建议统一放在工作区外层目录，例如：

```text
../EDA/
├── benchmark_runs_glm5.2/
├── benchmark_evals_glm5.2/
└── benchmark_local_batch_glm5.2/
```

## 6. 怎么跑 OpenCode

先确保 MinerU API 已启动。

### 6.1 GLM-5.2 示例

```bash
./run_all_benchmarks_opencode.sh   --benchmark-root ../InifiteEDA_data_lake   --limit-benchmarks 9   --jobs 10   --model myprovider/glm-5.2   --mineru-local-api-url http://127.0.0.1:18000   --run-output-root ../EDA/benchmark_runs_glm5.2   --eval-output-root ../EDA/benchmark_evals_glm5.2   --tmp-root ../EDA/benchmark_local_batch_glm5.2   --skip-existing
```

### 6.2 其他 OpenCode 模型

只需要改：

- `../.opencode/opencode.json` 中的模型配置
- `--model`
- 输出目录名

例如：

```bash
./run_all_benchmarks_opencode.sh   --benchmark-root ../InifiteEDA_data_lake   --limit-benchmarks 9   --jobs 10   --model myprovider/qwen3.7-max   --mineru-local-api-url http://127.0.0.1:18000   --run-output-root ../EDA/benchmark_runs_qwen3.7-max   --eval-output-root ../EDA/benchmark_evals_qwen3.7-max   --tmp-root ../EDA/benchmark_local_batch_qwen3.7-max   --skip-existing
```

## 7. 怎么跑 Codex

```bash
./run_all_benchmarks.sh   --benchmark-root ../InifiteEDA_data_lake   --limit-benchmarks 9   --jobs 10   --model gpt-5.4   --codex-home ../.codex   --mineru-local-api-url http://127.0.0.1:18000   --run-output-root ../EDA/benchmark_runs_codex_gpt5.4   --eval-output-root ../EDA/benchmark_evals_codex_gpt5.4   --tmp-root ../EDA/benchmark_local_batch_codex_gpt5.4   --skip-existing
```

## 8. 怎么跑 Claude Code

```bash
./run_all_benchmarks_claude.sh   --benchmark-root ../InifiteEDA_data_lake   --limit-benchmarks 9   --jobs 10   --model claude-opus-4-6   --claude-config-dir ../.claude   --mineru-local-api-url http://127.0.0.1:18000   --run-output-root ../EDA/benchmark_runs_claude_opus_4_6   --eval-output-root ../EDA/benchmark_evals_claude_opus_4_6   --tmp-root ../EDA/benchmark_local_batch_claude_opus_4_6   --skip-existing
```

## 9. 单 benchmark / 单任务调试

OpenCode：

```bash
./run_opencode_locally.sh   --queries-json ../InifiteEDA_data_lake/benchmark_archeology.json   --output-dir ../tmp/opencode_debug_archeology   --limit 3   --jobs 3   --model myprovider/glm-5.2   --opencode-config-dir ../.opencode   --mineru-local-api-url http://127.0.0.1:18000   --debug
```

Codex：

```bash
./run_codex_locally.sh   --queries-json ../InifiteEDA_data_lake/benchmark_archeology.json   --output-dir ../tmp/codex_debug_archeology   --limit 3   --jobs 3   --model gpt-5.4   --codex-home ../.codex   --mineru-local-api-url http://127.0.0.1:18000   --debug
```

Claude Code：

```bash
./run_claude_code_locally.sh   --queries-json ../InifiteEDA_data_lake/benchmark_archeology.json   --output-dir ../tmp/claude_debug_archeology   --limit 3   --jobs 3   --model claude-opus-4-6   --claude-config-dir ../.claude   --mineru-local-api-url http://127.0.0.1:18000   --debug
```

## 10. 常见报错排查

### 10.1 MinerU API 没启动

```bash
bash ./mineru-api/healthcheck_mineru_api.sh
```

### 10.2 配置文件不存在

检查：

- `../.codex/config.toml`
- `../.codex/auth.json`
- `../.opencode/opencode.json`
- `../.claude/settings.json`
- `./mineru-api/mineru_api.env`

### 10.3 CLI 找不到

检查：

```bash
command -v codex
command -v opencode
command -v claude
command -v mineru-api
command -v mineru
```
