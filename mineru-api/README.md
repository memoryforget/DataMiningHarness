# MinerU API

这里是 `DataMiningHarness` 的本地 `mineru-api` 辅助脚本。

## 文件

- `mineru_api.env.example`: 配置模板
- `start_mineru_api.sh`: 前台启动
- `start_mineru_api_bg.sh`: 后台启动
- `stop_mineru_api.sh`: 停止服务
- `healthcheck_mineru_api.sh`: 健康检查

## 使用方式

### 1. 安装 MinerU 模型

先准备一个本地模型目录，例如：

```text
../models/MinerU2.5-2509-1.2B/
```

推荐使用 `huggingface-cli` 下载：

```bash
pip install -U "huggingface_hub[cli]"
mkdir -p ../models
huggingface-cli download opendatalab/MinerU2.5-2509-1.2B \
  --local-dir ../models/MinerU2.5-2509-1.2B
```

如果需要登录，再先执行：

```bash
huggingface-cli login
```

### 2. 配置 `mineru_api.env`

先复制模板：

```bash
cp ./mineru-api/mineru_api.env.example ./mineru-api/mineru_api.env
```

复制模板后，再修改 `mineru_api.env`，其中路径都可以写成相对 `mineru-api/` 目录的相对路径。

例如：

```bash
ACTIVATE_BASE_SH=../activate_my_base.sh
CONDA_ENV_NAME=mineru
MODEL_DIR=../../models/MinerU2.5-2509-1.2B
RUNTIME_ROOT=../tmp/mineru_api_home
LOG_FILE=../tmp/mineru_api_18000.log
PID_FILE=../tmp/mineru_api_18000.pid
```

启动：

```bash
bash ./mineru-api/start_mineru_api_bg.sh
```

检查：

```bash
bash ./mineru-api/healthcheck_mineru_api.sh
```

停止：

```bash
bash ./mineru-api/stop_mineru_api.sh
```

默认本机地址：

```text
http://127.0.0.1:18000
```
