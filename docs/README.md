# 文档导航

首次使用从安装指南开始；已有账号可直接按功能进入。当前开发版为 **0.8.0b4**，具体安装版本与 HACS Release 请分别核对。

## 使用指南

| 任务 | 文档 |
|---|---|
| 安装、短信登录、手工安装与升级 | [安装指南](guides/installation.md) |
| 账号类型、自动续期、WXAPP 与手机共存 | [认证管理](guides/authentication.md) |
| 主账号官方日历、长期归档与轨迹 | [官方历史行程](guides/official-trip-history.md) |
| 副账号从车况自行记录未来行程 | [本地行程记录](guides/local-trip-recording.md) |
| 主账号随机自动签到 | [每日签到](guides/daily-checkin.md) |
| 地图、坐标、高德 Key 与文字地址 | [地图与地址](guides/maps-and-addresses.md) |
| 实验性车辆控制与运行模式 | [车辆控制](guides/vehicle-control.md) |
| 备份、恢复与数据迁移 | [备份与恢复](guides/backup-and-recovery.md) |
| 缓存、登录失败、行程缺失与错误状态 | [故障排查](guides/troubleshooting.md) |

## 字段参考

- [数据来源与已确认映射](reference/data-mapping.md)：里程、位置、单位及响应解释。
- [“其他”原始字段](reference/other-fields.md)：173项英文诊断字段、中文暂定释义和确认状态。

## 开发资料

- [开发、打包与目录约定](development/contributing.md)
- [WXAPP 登录验证边界](development/wxapp-validation.md)
- [版本记录](../CHANGELOG.md)

## 历史说明

[0.5版本地行程规则](archive/trip-history-0.5.md)仅用于理解旧数据；当前按启动至熄火分段，应以本地行程记录指南为准。

目录按用途划分：`guides/` 面向使用者，`reference/` 保存字段参考，`development/` 保存开发与验证资料，`archive/` 保存已被替代的行为说明。个人账号、位置历史、APK 与解包材料不放入此目录。
