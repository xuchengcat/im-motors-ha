# 开发、打包与目录约定

[返回文档导航](../README.md)

## 目录职责

| 目录 | 内容 |
|---|---|
| `custom_components/im_motors/` | HA生命周期、配置、实体、行程、签到与前端 |
| `custom_components/im_motors/primary/` | 主账号：官方行程API与归档、签到、行程H5总里程 |
| `custom_components/im_motors/authorized/` | 授权账号：本地采样/行程、健康诊断API与总里程 |
| `custom_components/im_motors/energy/` | 主副账号共用的本地充电/加油采样、档案与查询 |
| `custom_components/im_motors/pyim_china/` | 认证、签名、请求校验与加密存储 |
| `tests/` | 合成账号与HA回归测试，阻止真实HTTPS |
| `tools/` | `package_release.py`打包、`audit_release.py`审计、`account_cli.py`离线恢复 |
| `docker/` | 旧外置路径的独立手工测试模板 |
| `docs/guides/` | 当前版本使用指南与恢复步骤 |
| `docs/reference/` | 字段来源、单位与确认状态 |
| `docs/development/` | 开发流程和验证边界 |
| `docs/archive/` | 已被替代的历史行为，不能当成当前指南 |
| `dist/` | 构建ZIP与校验值，不提交Git |

个人账号、加密会话、密钥、APK、解包结果、抓包和真实位置历史放在工程外的受保护目录，不加入源码或发布包。不要为整理目录移动正在使用的HA数据目录。

## 修改主、副账号功能

- 主账号总里程：`primary/odometer.py`；行程接口与归档：`primary/trip_api.py`、`primary/trips.py`；签到：`primary/checkin_api.py`、`primary/checkin.py`。
- 副账号总里程：`authorized/odometer.py`；车辆诊断：`authorized/health_api.py`；本地记录：`authorized/trips.py`、`authorized/trip_recorder.py`。
- 公共层：根目录 `h5.py` 负责纯签名/解密，`odometer.py` 负责按角色选源与私有缓存，`trips.py` 负责共享任务/地址补全；`trip_store.py`、`trip_web.py` 仍供两种行程来源共用。

主、副账号模块不相互导入。入口在 `__init__.py` 和协调器根据账号类型分派，运行模式保持独立。修改一种来源时优先在其目录内完成，涉及共同缓存、签名或界面时再修改公共层；不要复制两份认证会话或共享可变的来源缓存。旧模块路径已在源码和测试中统一更新。

## 本地验证与打包

从工程根目录执行：

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m pytest -q
python tools/package_release.py
```

测试覆盖HA加载、配置、角色、缓存与认证、行程归档和签到去重；真实账号请求不放入自动测试。对签到等有副作用的接口，区分模拟测试、只读实测与真实提交确认，不把前两者称为完整生产验证。

打包器使用文件类型白名单，递归收集 `docs/` 的Markdown；输出运行包 `dist/im_motors.zip`、完整工程包 `dist/im_motors_ha-版本.zip` 及 `SHA256SUMS`。运行包不含指导文档、测试和工具；完整工程包包含这些内容。版本取自 `manifest.json`，需与 `const.py` 保持一致。

文档移动时更新所有相对链接；历史规则移入archive并标明被哪个当前指南替代。修改文档后检查相对路径与锚点，并核对ZIP中文档完整。纯文档编辑无需重新部署HA。

## Container路径与旧测试模板

新手机号登录通常只需持久化HA的 `/config` 卷。旧导入账号可使用额外挂载：

```yaml
volumes:
  - /srv/homeassistant/config:/config
  - /srv/im-motors/data:/config/im_motors/data
  - /srv/im-motors/secrets/vault.key:/run/secrets/im_vault_key:ro
```

以上是占位示例，不是本机实际路径。HA配置使用容器内路径。HACS需要代码目录可写，勿同时用只读代码bind mount覆盖它；仓库 `docker/compose.yaml` 是独立手工测试模板，不宜与HACS代码更新混用。

## 发布与反馈

修改代码、推送main、创建Release和在HA部署是不同动作。发布前检查测试、包内容、凭据泄漏以及说明中的实测边界；个人数据不进入问题附件。工程采用MIT许可，见[LICENSE](../../LICENSE)。
