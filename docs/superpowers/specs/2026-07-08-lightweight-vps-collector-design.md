# 轻量 VPS 数据采集器设计

日期：2026-07-08

## 目标

为本地 `D:\something\arbTest` 这套 ArbDashboard 系统，在 `txy` VPS 上部署一个轻量数据采集器。

VPS 的角色是低频“数据抽水机”：每天按计划把源数据采集成 JSON 文件。本地 Windows 系统仍然负责数据入库、估值计算、看板展示、券商客户端连接，以及所有交易相关流程。

## 非目标

- 不把完整 ArbDashboard 网页应用部署到 VPS。
- 不在 VPS 上运行 QMT、IB、Futu OpenD 或券商交易逻辑。
- 不开放公网看板或公网 API。
- 不在 VPS 上保存券商凭据。
- 在 SSH key 或 agent 认证可用时，不要求把 VPS 密码写进本地仓库。

## 目标主机与路径

本地项目：

```text
D:\something\arbTest
```

VPS SSH 目标：

```text
Host: txy
User: ubuntu
HostName: 101.34.73.38
Port: 22
```

VPS 部署目录：

```text
/home/ubuntu/LOFarb/
```

VPS 数据目录：

```text
/home/ubuntu/LOFarb/siphon_data/
```

本设计使用 `/home/ubuntu`，不使用 `/root`。原因是当前 SSH 登录用户是 `ubuntu`，采集器也不需要特权操作。

## 组件

### `deploy/041_jsl_cloud_shares.py`

VPS 端场内基金份额采集器。

职责：

- 从 `jsl_vps_symbols.txt` 读取基金代码。
- 对深交所基金逐只抓取，并使用保守请求间隔。
- 对上交所基金优先使用可用的批量接口。
- 每次运行写出一个 JSON 文件：

```text
shares_YYYY-MM-DD.json
```

输出值使用“万份”为单位，和现有 `daily_updater.py` 的入库预期保持一致。

### `deploy/cloud_siphon.py`

VPS 端非份额类日级数据采集器。

职责：

- 在公开接口可用时抓取每日人民币汇率中间价。
- 在接口可用时抓取期货结算价或收盘价。
- 只有在 VPS 本地安全配置了 API token 时，才可选抓取 Woody 数据。
- 写出本地同步端已经约定好的文件名：

```text
fx_YYYY-MM-DD.json
futures_YYYY-MM-DD.json
woody_YYYY-MM-DD.json
```

第一版可以只支持公开数据链路；如果 VPS 上没有明确配置 Woody 凭据，则 Woody 采集保持禁用。

### `deploy/deploy_vps.py`

本地部署辅助脚本。

职责：

- 读取 `arbcore/config/lof_config.yaml`。
- 根据配置中的基金代码生成 `jsl_vps_symbols.txt`。
- 上传采集脚本和基金代码文件到 `txy`。
- 创建 `/home/ubuntu/LOFarb/siphon_data`。
- 如有需要，在 VPS 虚拟环境中安装最小 Python 依赖。
- 安装或更新采集器 cron 任务。

部署脚本必须是幂等的：重复运行只能更新文件和 cron，不应产生重复任务。

### 本地 `daily_updater.py` 兼容性

本地同步函数已经会从 `VPS_DATA_DIR` 读取远端 JSON 文件，但当前逻辑要求 `VPS_PASSWORD` 非空才尝试同步。

需要修改：

- 当 `VPS_HOST`、`VPS_USER` 存在，并且 `VPS_PASSWORD`、`VPS_KEY_PATH`、SSH agent/key 认证任一可用时，都允许尝试同步。
- 密码认证保留为兜底，但优先使用 key/agent 认证。

本地私密配置建议设置为：

```python
VPS_HOST = "101.34.73.38"
VPS_PORT = 22
VPS_USER = "ubuntu"
VPS_PASSWORD = None
VPS_DATA_DIR = "/home/ubuntu/LOFarb/siphon_data"
VPS_KEY_PATH = None
VPS_KEY_PASSWORD = None
```

## 数据流

```text
VPS cron 定时任务
  -> 采集脚本
  -> /home/ubuntu/LOFarb/siphon_data/*.json
  -> 本地 daily_updater.py 通过 SFTP 拉取
  -> ArbDashboard/data/vps_sync/
  -> 本地 SQLite 数据库
  -> 看板与估值服务
```

入库后，本地应用仍然是数据和计算的主系统。VPS 只保存日级原始采集文件。

## Cron 计划

初始 cron 计划：

```text
0 6 * * 1-5    场内份额采集
20 9 * * 1-5   汇率/早盘数据采集
30 16 * * 1-5  期货/收盘数据采集
```

以上时间均为 VPS 本地时间。实施时需要确认 VPS 时区；如果不是 Asia/Shanghai 或 Asia/Hong_Kong，需要调整 cron 时间。

## 错误处理

- 采集成功后才写出结构化 JSON。
- 部分失败需要写入旁路日志，不能静默当成完整数据。
- 如果份额文件中的基金数量异常偏少，需要输出警告。
- 同一天已有文件时，只有显式 rerun 参数或部署时手动命令才能覆盖。
- 本地入库保持容错：如果 VPS 数据缺失，现有本地兜底链路继续运行。

## 安全

- 不把券商凭据放到 VPS。
- 不把私钥复制到 VPS。
- 如需 Woody token，必须配置在 VPS 本地 `.env` 或其他不进 git 的配置文件中。
- SSH 部署使用现有 `txy` Host 配置。
- 采集器不开放任何公网端口。

## 验收标准

满足以下条件才算部署成功：

1. `ssh txy` 可以列出 `/home/ubuntu/LOFarb/siphon_data`。
2. 手动运行份额采集器后能生成 `shares_YYYY-MM-DD.json`。
3. 份额 JSON 中包含合理数量的配置基金代码。
4. 本地 `daily_updater.py` 可以从 `VPS_DATA_DIR` 通过 SFTP 拉取文件。
5. 下载后的文件出现在 `ArbDashboard/data/vps_sync/`。
6. 本地 SQLite 至少成功写入一只已知基金的份额数据。
7. cron 中每个采集任务只有一个受管理条目，没有重复任务。

## 回滚

回滚应保持简单：

- 删除受管理的 cron 条目。
- 除非用户明确要求删除数据，否则保留 `/home/ubuntu/LOFarb/siphon_data`。
- 如有需要，回退本地 `daily_updater.py` 和私密 VPS 配置修改。

任何回滚步骤都不能删除本地数据库或已有看板数据。
