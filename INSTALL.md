# Installation

本文档只描述安装和准备步骤，不包含机器相关绝对路径。所有路径都以仓库根目录 `DataMiningHarness/` 为基准，必要时用 `../` 指向工作区根目录。

## 1. 目录约定

- 仓库根目录：`DataMiningHarness/`
- 工作区根目录：`DataMiningHarness/..`
- benchmark 默认目录：`../InifiteEDA_data_lake/`
- Codex 配置目录：`../.codex/`
- OpenCode 配置目录：`../.opencode/`
- Claude Code 配置目录：`../.claude/`
- MinerU API 配置目录：`./mineru-api/`

## 2. 系统依赖

至少需要：

- `bash`
- `python3`，建议 `3.10+`
- `conda` 或兼容的环境管理器
- `node` 与 `npm`，建议 `node 20+`
- 可用 GPU 驱动与 CUDA 运行环境
- `curl`

## 3. Python 环境

当前仓库中的 Python 脚本只依赖标准库；但真正运行 benchmark 时，agent 和 MinerU 相关命令需要安装在某个 conda 环境里。

建议新建一个环境，例如：

```bash
conda create -n data-harness python=3.11 -y
conda activate data-harness
```

至少确认以下命令可用：

```bash
python --version
python -c "import json, sqlite3, urllib.request"
```

## 4. Node 环境

建议使用 `node 20+`。如果你使用 `nvm`：

```bash
nvm install 20
nvm use 20
node -v
npm -v
```

## 5. 安装三套 agent CLI

### 5.1 Codex CLI

要求最终满足：

```bash
command -v codex
codex --help
```

安装方式按你的发布来源处理，但安装完成后需要能在 shell 中找到 `codex`。

### 5.2 OpenCode CLI

要求最终满足：

```bash
command -v opencode
opencode --help
```

如果 OpenCode provider 依赖额外 npm 包，建议在 `../.opencode/` 下维护其依赖。

### 5.3 Claude Code CLI

要求最终满足：

```bash
command -v claude
claude --help
```

## 6. 准备三套配置目录

这些目录不在仓库中，而是在仓库外的工作区根目录下：

```text
../.codex/
../.opencode/
../.claude/
```

### 6.1 Codex

创建：

```bash
mkdir -p ../.codex
```

最少需要：

- `../.codex/config.toml`
- `../.codex/auth.json`

模板：

`../.codex/config.toml`

```toml
model = "gpt-5.4"
model_provider = "openai-custom"
model_reasoning_effort = "medium"
approvals_reviewer = "user"

[projects."/path/to/workspace-root"]
trust_level = "trusted"

[projects."/path/to/workspace-root/DataMiningHarness"]
trust_level = "trusted"

[model_providers.openai-custom]
name = "openai-custom"
provider = "openai"
base_url = "https://your-openai-compatible-endpoint/v1"
wire_api = "responses"
```

`../.codex/auth.json`

```json
{
  "auth_mode": "apikey",
  "OPENAI_API_KEY": "your_api_key_here"
}
```

### 6.2 OpenCode

创建：

```bash
mkdir -p ../.opencode
```

最少需要：

- `../.opencode/opencode.json`

模板：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "myprovider/glm-5.2",
  "permission": "allow",
  "provider": {
    "myprovider": {
      "npm": "@ai-sdk/openai-compatible",
      "api": "createOpenAICompatible",
      "name": "My Provider",
      "options": {
        "apiKey": "your_api_key_here",
        "baseURL": "https://your-openai-compatible-endpoint/v1"
      },
      "models": {
        "glm-5.2": {
          "name": "glm-5.2"
        }
      }
    }
  }
}
```

如果 provider 需要 npm 依赖，可以在 `../.opencode/package.json` 中维护。

### 6.3 Claude Code

创建：

```bash
mkdir -p ../.claude
```

最少需要：

- `../.claude/settings.json`

模板：

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "your_api_key_here",
    "ANTHROPIC_BASE_URL": "https://your-anthropic-compatible-endpoint/",
    "ANTHROPIC_MODEL": "claude-opus-4-6",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": 1,
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": 1
  },
  "permissions": {
    "deny": [
      "Read(!/path/to/workspace-root/**)",
      "Edit(!/path/to/workspace-root/**)",
      "Write(!/path/to/workspace-root/**)"
    ]
  },
  "sandbox": {}
}
```

## 7. 安装 MinerU 环境

MinerU 是单独的运行环境，和三套 agent CLI 不是一回事。你至少需要：

- `mineru-api` 命令
- `mineru` 命令
- 一个本地 MinerU 模型目录

建议单独建环境，例如：

```bash
conda create -n mineru python=3.11 -y
conda activate mineru
```

然后按你使用的 MinerU 发布方式安装，使下面命令可用：

```bash
command -v mineru-api
command -v mineru
mineru-api --help
mineru --help
```

### 7.1 MinerU 模型安装

你需要先把 MinerU 模型下载到本地，再在 `mineru-api/mineru_api.env` 里通过 `MODEL_DIR` 指向该目录。

建议把模型放在工作区外层的统一目录，例如：

```text
../models/MinerU2.5-2509-1.2B/
```

推荐做法是使用 `huggingface-cli` 下载模型快照。

先安装下载工具：

```bash
pip install -U "huggingface_hub[cli]"
```

然后下载模型：

```bash
mkdir -p ../models
huggingface-cli download opendatalab/MinerU2.5-2509-1.2B   --local-dir ../models/MinerU2.5-2509-1.2B
```

如果你的环境要求先登录 Hugging Face，再执行：

```bash
huggingface-cli login
```

下载完成后，`MODEL_DIR` 应该指向下载后的模型根目录，而不是它的上一级目录。

例如：

```bash
MODEL_DIR=../../models/MinerU2.5-2509-1.2B
```

### 7.2 验证模型目录

至少先确认目录存在：

```bash
test -d ../models/MinerU2.5-2509-1.2B && echo ok
```

再确认 `mineru-api/mineru_api.env` 中的 `MODEL_DIR` 指向的是同一个目录。

如果你已经配置好 `mineru-api/mineru_api.env`，也可以直接启动服务验证：

```bash
bash ./mineru-api/start_mineru_api_bg.sh
bash ./mineru-api/healthcheck_mineru_api.sh
```

如果健康检查通过，说明模型目录和 `mineru-api` 基本已经连通。

## 8. 配置 MinerU API

先复制模板：

```bash
cp mineru-api/mineru_api.env.example mineru-api/mineru_api.env
```

然后按实际环境修改。模板里支持相对路径，默认相对基准就是 `mineru-api/` 目录。

例如：

```bash
ACTIVATE_BASE_SH=../activate_my_base.sh
CONDA_ENV_NAME=mineru
MODEL_DIR=../../models/MinerU2.5-2509-1.2B
RUNTIME_ROOT=../tmp/mineru_api_home
LOG_FILE=../tmp/mineru_api_18000.log
PID_FILE=../tmp/mineru_api_18000.pid
```

## 9. 验证安装完成

在仓库根目录执行：

```bash
command -v codex
command -v opencode
command -v claude
```

切到 MinerU 环境后执行：

```bash
command -v mineru-api
command -v mineru
```

启动 MinerU API 后执行：

```bash
bash mineru-api/healthcheck_mineru_api.sh
```

如果这些都通过，再去看 `README.md` 里的批跑命令。
