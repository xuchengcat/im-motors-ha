# 智己汽车 Home Assistant 只读集成

[![Validation](https://github.com/xuchengcat/im-motors-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/xuchengcat/im-motors-ha/actions/workflows/validate.yaml)

通过 Home Assistant 管理智己账号关联车辆元数据和认证会话。支持 HACS 自定义仓库安装，运行不依赖手机、ADB 或 Windows。

**v0.1.0 读取关联车辆元数据，电量、续航及其他实时车况仍待定。** 这一版导入已有的便携加密会话，不提供 HA 界面短信登录。安装完成后还需准备会话文件和外置密钥，才能添加集成；HACS 不会下载或生成这些私人文件。

## 通过 HACS 安装

要求 Home Assistant **2026.2 或更高版本**，已在 Linux HA 2026.2.3 验证。

1. 打开 HACS，点击右上角 **⋮ → 自定义存储库 / Custom repositories**。
2. 添加 `https://github.com/xuchengcat/im-motors-ha`，类型选择 **Integration / 集成**。
3. 搜索 **智己汽车（只读）**，下载最新版本，然后重启 Home Assistant。
4. 按 [会话配置说明](docs/installation.md) 挂载已有加密文件和密钥。
5. 进入 **设置 → 设备与服务 → 添加集成 → 智己汽车（只读）**，填写容器内绝对路径。

这属于 HACS 自定义仓库安装，尚未申请默认仓库列表收录。可参考 [HACS 官方自定义仓库说明](https://www.hacs.dev/docs/faq/custom_repositories/)。

## 当前能力

| 数据或功能 | v0.1.0 行为 |
|---|---|
| 账号关联车辆 | 读取场景车辆列表，以 VIN 哈希建立 HA 设备 |
| 设备名称 | 统一显示为“智己汽车” |
| 账号凭据到期时间、建议刷新时间 | 显示服务器认证时间；到建议时间才刷新 |
| 元数据更新时间 | 本机读取时间，不是车辆上报时间 |
| 车型项目代码、场景支持标志 | 仅按实际响应显示；缺失或 null 显示 unknown，false 保持 false |
| SOC、续航、里程、充电、门窗、车锁、胎压、在线和位置 | 待定占位，默认禁用且不可用 |
| 车况解析状态 | 显示“待定” |
| 请求失败或中断 | 持久停止自动请求，支持人工排查后恢复 |

元数据接口可能不返回车型项目代码和场景支持标志。实体存在不代表已取得读数。功能标志也不代表当前车辆状态；完整说明见 [数据对应关系](docs/data-mapping.md)。

本集成仅开放账号 token 刷新和场景关联车辆列表，不调用实时车况、位置、唤醒或车辆控制接口。手动启用待定实体不会触发额外查询。

## 安装与维护

- [会话配置、Docker 挂载、备份与故障恢复](docs/installation.md)
- [已确认数据、字段对应与待定内容](docs/data-mapping.md)
- [版本记录](CHANGELOG.md)
- [反馈问题](https://github.com/xuchengcat/im-motors-ha/issues)

也可在 [Releases](https://github.com/xuchengcat/im-motors-ha/releases) 下载 `im_motors.zip`，将其中内容解压到 `/config/custom_components/im_motors/` 后重启 HA。HACS 使用同一个运行文件包；`im_motors_ha-版本号.zip` 则包含完整工程及开发工具。

运行数据和密钥应独立挂载，并在不同位置备份。不要同时让多个进程或多份过时会话刷新同一账号。反馈问题时请使用 HA 脱敏诊断，不附带会话、密钥、手机号、VIN 或完整服务端响应。

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
