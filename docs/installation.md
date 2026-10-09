# 会话配置、安装与恢复

v0.1.0 需要已有的便携加密会话。它不提供短信登录或生产配置初始化入口。
未准备会话的账号可以安装集成文件，但暂时无法完成配置。

## 文件与路径

准备同一客户端身份的当前文件：

| 文件 | 容器内示例路径 | 用途 |
|---|---|---|
| `session.imvault` | `/config/im_motors/data/session.imvault` | 当前 token、认证时间和会话状态 |
| `device.imvault` | `/config/im_motors/data/device.imvault` | 持久客户端身份 |
| `production.imvault` | `/config/im_motors/data/production.imvault` | 加密的协议配置 |
| 外置 32 字节密钥 | `/run/secrets/im_vault_key` | 解密上述文件，须与其匹配 |

密钥单独只读挂载，数据目录可写。Linux 目录建议 0700、文件 0600，所有者须为 HA 进程用户。
HA 配置表单仅保存两个路径，不保存密钥或 token 内容。

不要用旧登录响应覆盖轮换后的会话。迁移时复制完整当前数据目录，保留 pending 状态及
`attempts/`，也不要交替刷新多个会话副本。

## 已有 Home Assistant Container

在已有 Compose 的 HA 服务中增加挂载，例如：

```yaml
volumes:
  - /srv/homeassistant/config:/config
  - /srv/im-motors/data:/config/im_motors/data
  - /srv/im-motors/secrets/vault.key:/run/secrets/im_vault_key:ro
```

集成代码由 HACS 安装到可写的 `/config/custom_components/im_motors/`。
不要再用只读 bind mount 覆盖这个代码目录，否则 HACS 无法更新它。

重启 HA，在“添加集成”选择“智己汽车（只读）”，填写：

- 数据目录：`/config/im_motors/data`
- 密钥文件：`/run/secrets/im_vault_key`

表单验证只读取本地文件。配置完成后首次加载会请求关联车辆元数据。
Home Assistant OS 等环境也需提供 HA 进程能够访问的绝对路径；本项目尚未验证这些环境。

## 独立 Docker 模板

仓库 `docker/compose.yaml` 是独立 HA 容器模板，采用源码只读挂载，适合手工安装测试。
它不适合在同一代码路径上同时使用 HACS 更新。

```sh
cd docker
cp compose.env.example compose.env
# 设置 Linux 主机目录及 HA 进程的 UID/GID。
docker compose --env-file compose.env config
docker compose --env-file compose.env up -d
```

模板固定在测试过的 HA 2026.2.3。实际 Docker 启动仍未验证。

## 自动刷新与请求停止

每分钟检查本地认证时间，未到服务器建议刷新时间时不发送认证请求。
账号场景元数据缓存 30 分钟。手动更新实体也不能绕过缓存间隔。

元数据请求执行中断或失败时保留 `ha-fault.imvault`，后续周期和 HA 重启都不自动重发。
刷新存在 pending 状态时，也会阻止继续发请求。

恢复流程：

1. 暂停集成，检查保留的故障和 pending 状态。
2. 如果成功刷新响应已保存，可用下面的离线工具恢复；结果未知时先人工排查。
3. 认证过期时，通过原会话初始化工具取得同一客户端身份的新会话，然后使用 HA 的“重新认证”。
4. 完成排查后，在集成“重新配置”勾选恢复请求；未解决的刷新 pending 仍会阻止恢复。

不要通过删除 pending 文件来绕过未知刷新结果。

仓库工程内工具，从工程根目录执行：

```sh
python tools/account_cli.py --data-dir /config/im_motors/data \
  --key-file /run/secrets/im_vault_key check
python tools/account_cli.py --data-dir /config/im_motors/data \
  --key-file /run/secrets/im_vault_key recover-refresh \
  --result /config/im_motors/data/attempts/refresh-实际文件名.imvault
```

这两个命令不会发送 HTTP。HACS 运行包不包含 `tools/`，需要工具时下载完整工程或克隆仓库。
单独运行工具的 Python 环境需安装 PyCryptodome。

## 备份与反馈

运行数据和外置密钥一起构成完整备份，请分别保存在不同位置。HACS 更新不会下载或替换这些文件。
反馈问题时优先提供 HA 脱敏诊断、HA 版本和错误类别，不提交真实账号资料或完整响应。
