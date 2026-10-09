# 智己汽车 Home Assistant 只读集成

[![Validation](https://github.com/xuchengcat/im-motors-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/xuchengcat/im-motors-ha/actions/workflows/validate.yaml)

通过 Home Assistant 管理智己账号关联车辆元数据和认证会话。支持 HACS 自定义仓库安装，运行不依赖手机、ADB 或 Windows。

**v0.3.0 安装后只需在 HA 界面填写手机号和短信验证码，即可完成配置。** 通用协议参数随集成提供，账号加密密钥由每个 HA 实例在本地随机生成，无需手动准备 `production.imvault` 或挂载密钥。读取关联车辆元数据，电量、续航及其他实时车况仍待定。

## 通过 HACS 安装

要求 Home Assistant **2026.2 或更高版本**，已在 Linux HA 2026.2.3 验证。

1. 打开 HACS，点击右上角 **⋮ → 自定义存储库 / Custom repositories**。
2. 添加 `https://github.com/xuchengcat/im-motors-ha`，类型选择 **Integration / 集成**。
3. 搜索 **智己汽车（只读）**，下载最新版本，然后重启 Home Assistant。
4. 进入 **设置 → 设备与服务 → 添加集成 → 智己汽车（只读） → 手机号与短信登录**，填写中国大陆手机号。
5. 提交发送短信，输入收到的验证码完成登录。

从 v0.1.0 / v0.2.0 升级时，在 HACS 更新并重启 HA，已有会话、外置密钥和实体可继续使用，不会自动迁移或重建。需要重新登录时，打开集成的 **重新配置 → 手机号与短信登录**；认证过期后的“重新认证”也提供同一入口。已有加密会话仍可通过导入方式配置。

这属于 HACS 自定义仓库安装，尚未申请默认仓库列表收录。可参考 [HACS 官方自定义仓库说明](https://www.hacs.dev/docs/faq/custom_repositories/)。

## 当前能力

| 数据或功能 | v0.3.0 行为 |
|---|---|
| 手机号与短信登录 | 仅需手机号和验证码；自动生成本地密钥、设备身份和加密会话，支持重发和重新认证 |
| 账号关联车辆 | 读取场景车辆列表，以 VIN 哈希建立 HA 设备 |
| 设备名称 | 统一显示为“智己汽车” |
| 账号凭据到期时间、建议刷新时间 | 显示服务器认证时间；到建议时间才刷新 |
| 元数据更新时间 | 本机读取时间，不是车辆上报时间 |
| 车型项目代码、场景支持标志 | 仅按实际响应显示；缺失或 null 显示 unknown，false 保持 false |
| SOC、续航、里程、充电、门窗、车锁、胎压、在线和位置 | 待定占位，默认禁用且不可用 |
| 车况解析状态 | 显示“待定” |
| 请求失败或中断 | 持久停止自动请求，支持人工排查后恢复 |

元数据接口可能不返回车型项目代码和场景支持标志。实体存在不代表已取得读数。功能标志也不代表当前车辆状态；完整说明见 [数据对应关系](docs/data-mapping.md)。

本集成使用短信登录、账号 token 刷新和场景关联车辆列表，不调用实时车况、位置、唤醒或车辆控制接口。手动启用待定实体不会触发额外查询。服务端要求图形/风控验证或账号绑定时，登录流程停止并提示，当前 HA 界面暂不支持这些额外步骤。

## 安装与维护

- [短信登录、旧版本兼容、备份与故障恢复](docs/installation.md)
- [已确认数据、字段对应与待定内容](docs/data-mapping.md)
- [版本记录](CHANGELOG.md)
- [反馈问题](https://github.com/xuchengcat/im-motors-ha/issues)

也可在 [Releases](https://github.com/xuchengcat/im-motors-ha/releases) 下载 `im_motors.zip`，将其中内容解压到 `/config/custom_components/im_motors/` 后重启 HA。HACS 使用同一个运行文件包；`im_motors_ha-版本号.zip` 则包含完整工程及开发工具。

新账号的私人数据和本地密钥自动保存在 HA 配置目录的 `.storage/im_motors/`；备份时保留整个目录。公开的通用协议参数不能解密这些账号文件。HACS 更新只更新代码，不替换账号密钥和数据。不要同时让多个进程或多份过时会话刷新同一账号。反馈问题时请使用 HA 脱敏诊断，不附带会话、密钥、手机号、VIN 或完整服务端响应。

## 开发

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m pytest -q
python tools/package_release.py
```

测试使用合成账号、临时加密文件和 mock HTTP，并阻止真实 HTTPS 连接。覆盖真实 HA 加载/卸载、配置流程、实体、诊断，以及缓存、轮换、故障停止和离线恢复。GitHub Actions 同时运行测试和 HACS 仓库检查。

长期真实自动刷新、其他车型/多车账号、实时车况和实际 Docker 部署仍需进一步验证。本项目为非官方集成，与车辆厂商没有关联。代码许可见 [LICENSE](LICENSE)。
