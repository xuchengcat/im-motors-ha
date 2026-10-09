# 短信登录、安装与恢复

v0.2.0 可在 HA 配置界面完成手机号与短信登录，也保留已有会话导入方式。
登录前须准备 `production.imvault` 和与其匹配的外置密钥；协议配置初始化仍需在集成外完成。
未准备这两个私人文件时，可以安装代码，但无法发送短信或完成配置。

## 文件与路径

短信登录需要协议配置和外置密钥；若已有会话，请保留同一客户端身份的完整当前文件：

| 文件 | 容器内示例路径 | 用途 |
|---|---|---|
| `session.imvault` | `/config/im_motors/data/session.imvault` | 当前 token、认证时间和会话状态；首次短信登录成功时创建 |
| `device.imvault` | `/config/im_motors/data/device.imvault` | 持久客户端身份；首次短信登录流程创建 |
| `production.imvault` | `/config/im_motors/data/production.imvault` | 加密的协议配置 |
| 外置 32 字节密钥 | `/run/secrets/im_vault_key` | 解密上述文件，须与其匹配 |

密钥单独只读挂载，数据目录可写。Linux 目录建议 0700、文件 0600，所有者须为 HA 进程用户。
HA 配置仅保存两个路径和客户端身份哈希，不保存手机号、验证码、密钥或 token 内容。
短信流程的状态及服务端响应加密保存在 `ha-login.imvault`。手机号仅在当前流程内存中使用，
持久状态使用绑定客户端身份的手机号哈希以便重启后匹配同一验证码流程。

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

选择“手机号与短信登录”，输入手机号和上述路径，然后提交发送短信。
收到短信后输入验证码提交登录。需要新验证码时勾选“重新发送验证码”，验证码可留空；
重发须间隔至少 60 秒。本地流程有效期 10 分钟，服务端验证码可能更早过期。
关闭流程后，只要状态有效，在同一路径重新输入同一手机号可继续使用已有验证码，无需重发。

已有会话可选择“导入已有加密会话”，该步骤仅离线检查。
配置完成后首次加载会请求关联车辆元数据。
Home Assistant OS 等环境也需提供 HA 进程能够访问的绝对路径；本项目尚未验证这些环境。

## 从 v0.1.0 更新及重新登录

在 HACS 下载 v0.2.0 并重启 HA，已有数据和实体无需重新建立。
需要短信登录时，在集成菜单选择“重新配置 → 手机号与短信登录”；认证过期时的
“重新认证”也提供短信登录及导入选项。路径默认沿用当前配置，客户端身份保持不变。
请使用原账号重新认证；切换其他账号时应使用独立的数据目录并添加新的集成。

提交手机号前会离线检查配置、身份和未解决的刷新状态。提交后才发送短信；
检查验证码格式不合格或本地流程过期时，不会提交登录请求。
服务端要求图形/风控验证或账号绑定时，界面会停止并提示；这些额外步骤尚未在 HA 实现。

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
3. 认证过期时，使用 HA 的“重新认证 → 手机号与短信登录”，或导入同一客户端身份的有效会话。
4. 完成排查后，在集成“重新配置 → 更新路径或恢复请求”勾选恢复请求；未解决的 pending 仍会阻止恢复。

短信登录期间暂停账号读取及刷新。短信或登录请求结果不明时，保留状态并停止重发。
不要通过删除 pending 文件或 `ha-login.imvault` 来绕过未知请求结果。
若登录成功响应已加密保存，但解析或会话提交中断，可用 `recover-login` 离线恢复。
它不发送短信或登录请求，也不会覆盖登录开始后被其他进程更改的会话。

仓库工程内工具，从工程根目录执行：

```sh
python tools/account_cli.py --data-dir /config/im_motors/data \
  --key-file /run/secrets/im_vault_key check
python tools/account_cli.py --data-dir /config/im_motors/data \
  --key-file /run/secrets/im_vault_key recover-login
python tools/account_cli.py --data-dir /config/im_motors/data \
  --key-file /run/secrets/im_vault_key recover-refresh \
  --result /config/im_motors/data/attempts/refresh-实际文件名.imvault
```

这些命令不会发送 HTTP。HACS 运行包不包含 `tools/`，需要工具时下载完整工程或克隆仓库。
单独运行工具的 Python 环境需安装 PyCryptodome。

## 备份与反馈

运行数据和外置密钥一起构成完整备份，请分别保存在不同位置。HACS 更新不会下载或替换这些文件。
反馈问题时优先提供 HA 脱敏诊断、HA 版本和错误类别，不提交真实账号资料或完整响应。
