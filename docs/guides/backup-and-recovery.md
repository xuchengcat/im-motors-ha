# 备份、恢复与迁移

[返回文档导航](../README.md)

## 需要备份什么

备份完整 HA 配置，并保留所有实际使用的外置账号目录与密钥。新短信登录通常位于 `/config/.storage/im_motors/`，导入或定制部署可能位于其他路径，不能只按默认目录猜测。

| 文件或目录 | 内容 |
|---|---|
| `vault.key`、`storage.imvault` | 匹配的本地密钥与存储标记 |
| `accounts/<账号哈希>/` | 账号完整数据目录；导入账号可能直接使用自选目录 |
| `device.imvault`、`session.imvault` | 客户端身份、令牌、认证期限和会话状态 |
| `ha-login.imvault`、`attempts/` | 登录及刷新状态、加密响应与离线恢复材料 |
| `ha-metadata.imvault`、`ha-telemetry*.imvault` | 车辆关联、车况缓存与响应 |
| `trip-settings.imvault` | 加密的高德Key等行程设置 |
| `cloud-trips.sqlite3` | 主账号长期归档的官方行程、轨迹与地址 |
| `trips.sqlite3` | 授权账号自行记录与导入的行程 |
| `checkin-state.imvault` | 签到随机计划、每日提交状态与最近结果 |

`.imvault`是加密存储；**SQLite行程库本身不是加密文件**，其中有个人位置历史。文件权限不等于加密，备份应保存在私有位置，不上传到公开仓库或问题反馈。

## 一致备份与恢复步骤

1. 记下当前集成版本、实际数据目录、密钥路径及外置挂载。使用 HA 备份时，确认外置目录是否也被包含。
2. 要手工复制文件，先停止HA，完整复制配置、账号目录和匹配密钥，避免数据库与会话处于写入中。不要只拷贝正在写入的 `.sqlite3` 而漏掉可能存在的 `-wal` 等配套文件。
3. 恢复到目标机后核对容器内路径及挂载，再恢复文件所有者与权限。HA进程需能访问密钥并写入账号目录；Linux新文件通常为0600、目录为0700。
4. 只启动一个使用该账号存储的HA实例。核对登录状态、车辆、行程和签到 `next_run`。

HACS更新只更换代码，不覆盖账号数据；删除集成条目也不会自动清除私人账号目录。密钥损坏或丢失时恢复完整匹配备份，不生成新密钥覆盖旧目录。不要让旧、新机器并行刷新同一账号。

## 请求停止与恢复

临时GET网络错误、HTTP 408/429/5xx按持久退避时间自动恢复；认证失效、VIN不一致、解析异常或未知写请求结果需要分别处理。重启不会清除这些保护状态。

先暂停集成并查看脱敏诊断。成功响应已经保留时可离线恢复；结果未知先核查实际结果。认证失效用HA重新认证；排查完成后可在 **重新配置 → 更新路径或恢复请求** 勾选恢复请求，未解决的pending仍会阻止恢复。

完整工程内的离线工具从工程根目录执行，路径仅为占位示例：

```sh
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key check
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key recover-login
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key recover-refresh \
  --result /config/.storage/im_motors/accounts/账号哈希/attempts/refresh-实际文件名.imvault
```

上述命令不发送HTTP，不重发短信；独立Python环境需安装PyCryptodome。HACS运行包不含 `tools/`，需要时下载完整工程。不要删除pending或加密回执来绕过未知结果。

签到 `unknown` 与认证刷新pending不是同一状态：不要使用登录恢复命令清除签到结果。签到提交结果未知时不自动重复POST，先核对App当天记录，见[每日签到](daily-checkin.md)。
