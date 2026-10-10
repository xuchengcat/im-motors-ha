# 短信登录、更新与恢复

v0.4.5 新安装只需手机号和短信验证码，无需准备 `production.imvault` 或挂载密钥。
通用协议参数随集成发布；保护账号会话的密钥在每个 HA 实例本地随机生成。

## 新账号配置

1. 在 HACS 自定义存储库中添加 `https://github.com/xuchengcat/im-motors-ha`，类型选择集成。
2. 下载最新版本，重启 HA。
3. 在“设置 → 设备与服务 → 添加集成”选择“智己汽车（只读）”。
4. 选择“手机号与短信登录”，输入中国大陆手机号，提交发送短信。
5. 输入收到的短信验证码并提交。登录后首次加载会读取关联车辆元数据及车况。

需要新验证码时勾选“重新发送验证码”，验证码可留空；重发间隔至少 60 秒。
本地验证码流程有效期 10 分钟，服务端可能更早过期。
关闭页面后，重新输入同一手机号可以继续有效流程，无需重发。
手机号和验证码不会保存到 HA 配置中，界面无需填写目录或密钥路径。

服务端要求图形/风控验证或账号绑定时，流程停止并显示提示。这些额外步骤暂未在 HA 实现。
新版验证使用真实 HA 框架和 mock HTTP；实际账号登录及 HA OS 部署仍需实际环境验证。

## 本地存储与备份

新账号的存储自动放在 HA 配置目录内，通常为 `/config/.storage/im_motors/`：

| 文件或目录 | 用途 |
|---|---|
| `vault.key` | 本机随机生成的 32 字节账号加密密钥，每个 HA 实例独立 |
| `storage.imvault` | 用于确认密钥仍与已有存储匹配的加密标记 |
| `accounts/<账号哈希>/device.imvault` | 持久客户端身份 |
| `accounts/<账号哈希>/session.imvault` | 当前 token、认证时间和会话状态 |
| `accounts/<账号哈希>/ha-login.imvault` | 短信流程、请求状态和加密响应 |
| `accounts/<账号哈希>/attempts/` | 刷新尝试及离线恢复材料 |

账号目录名使用本地密钥计算手机号 HMAC，不直接存放手机号。HA 配置只保存路径及客户端身份哈希。
Linux 下新密钥和加密文件使用 0600 权限，新账号目录使用 0700，文件所有者须为 HA 进程用户。

通用协议参数位于代码包的 `pyim_china/protocol.json`，不含手机号、token、VIN、设备身份或本地账号密钥。
公开这些协议参数不能解密账号会话。`vault.key` 和加密会话必须作为私人数据保留在本机。

备份时保留**整个 `.storage/im_motors/` 目录**，包括密钥、标记、所有账号和 pending 状态。
HACS 更新只更新 `custom_components/im_motors/` 下的代码，不更换密钥或账号数据。
密钥丢失、被替换或损坏时会停止配置；请恢复完整匹配备份，不会自动生成新密钥覆盖已有数据。

删除 HA 集成条目不会自动清除这个目录，以便继续未完成流程或离线排查。
不要把目录上传到 GitHub，也不要交替使用多个会话副本刷新同一账号。

## 从 v0.1.0 / v0.2.0 / v0.3.0 更新

在 HACS 下载 v0.4.5 后重启 HA，已有数据、设备身份和实体保留，不会自动迁移加密文件或替换密钥。
原来通过外置路径配置的账号继续使用原目录和原密钥，因此已有挂载需保留。
已有 `production.imvault` 继续作为自定义协议配置使用；未提供时使用安装包中的通用配置。
已有文件损坏或解密失败时会报错，不会悄悄回退到通用配置。

重新登录时打开集成的“重新配置 → 手机号与短信登录”，只需填写手机号和验证码。
认证过期后的“重新认证”也提供同一入口。请使用原账号重新认证。
新账号应从“添加集成”进入，自动创建独立账号目录。

## 导入已有加密会话

此方式适合旧账号迁移和高级配置。选择“导入已有加密会话”，填写已有数据目录与匹配密钥的绝对路径。
目录中须有同一客户端身份的 `session.imvault` 和 `device.imvault`；`production.imvault` 可选。
导入步骤只离线检查，完成后首次加载查询关联车辆元数据及车况。

Home Assistant Container 的旧挂载示例：

```yaml
volumes:
  - /srv/homeassistant/config:/config
  - /srv/im-motors/data:/config/im_motors/data
  - /srv/im-motors/secrets/vault.key:/run/secrets/im_vault_key:ro
```

使用新短信登录方式时，已有 `/config` 持久卷即可，不需要后两项账号挂载。
HACS 需要可写的 `/config/custom_components/im_motors/`；不要用只读 bind mount 覆盖代码目录。
仓库 `docker/compose.yaml` 保留为旧路径的独立手工测试模板，不适合同时使用 HACS 更新代码。

## 自动刷新与请求停止

每分钟检查本地认证时间，到服务器建议刷新时间才发送认证请求。账号场景元数据缓存 30 分钟，
v6 车况按 VIN 和配置间隔缓存，默认60分钟、最短5分钟，常规实体更新和重启均不能绕过缓存间隔；“立即重新查询车况”按钮可立即重新读取云端。升级后首次补充关联 VIN 并创建车况缓存。车况读取可能唤醒车辆，禁用集成可暂停。短信登录期间暂停账号读取及刷新。

未知短信、登录或刷新结果会持久停止重发；元数据或车况失败保留故障标记，VIN 不一致也停止。HA 重启不会绕过这些检查。
不要通过删除 pending 文件或 `ha-login.imvault` 继续未知请求。

恢复流程：

1. 暂停集成，检查保留的故障和 pending 状态。
2. 成功响应已保存时，可使用离线工具恢复；结果未知时先人工排查。
3. 认证过期时，使用“重新认证 → 手机号与短信登录”，或导入同一客户端身份的有效会话。
4. 排查完成后，在“重新配置 → 更新路径或恢复请求”勾选恢复请求；未解决的 pending 仍会阻止恢复。

完整工程内的离线工具，从工程根目录执行；按实际账号路径替换“账号哈希”和刷新文件名：

```sh
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key check
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key recover-login
python tools/account_cli.py --data-dir /config/.storage/im_motors/accounts/账号哈希 \
  --key-file /config/.storage/im_motors/vault.key recover-refresh \
  --result /config/.storage/im_motors/accounts/账号哈希/attempts/refresh-实际文件名.imvault
```

这些命令不发送 HTTP。`recover-login` 不重发短信或验证码，也不会覆盖登录开始后被其他进程改变的会话。
HACS 运行包不包含 `tools/`，需要时下载完整工程或克隆仓库。独立 Python 环境需安装 PyCryptodome。

反馈问题时优先提供 HA 脱敏诊断、HA 版本和错误类别，不提交账号目录、密钥或完整响应。

## 车况与手机账号

v0.4.5 自动增加车况实体，原来的占位保持禁用。诊断原码可在设备的实体列表中按需启用，未确认单位不会自动补齐。

手机与 HA 使用同一账号时，重新登录可能使另一端 token 失效。认证失败会停止查询并要求手动处理，不会循环抢登录。此次发布验证使用离线数据及 mock HTTP，没有重新登录真实账号。

`ha-metadata.imvault` 保存受保护的 VIN 关联，`ha-telemetry.imvault` 保存加密车况缓存，`ha-telemetry-response.imvault` 保存最近一次加密正文。不要直接分享这些文件。

车况更新间隔可在创建时的短信登录或会话导入页面设置，单位为分钟，默认60，必须为不少于5的整数。已有集成未配置此项时使用60分钟；可在重新配置的路径页面修改，重新认证保留原设置。账号认证仍每分钟本地检查，元数据仍缓存30分钟，两者不使用车况间隔。
