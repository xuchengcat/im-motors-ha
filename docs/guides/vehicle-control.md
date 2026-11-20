# 运行模式、车控与风险

v0.7.0 仅在首次添加集成的短信登录/导入页选择运行模式，配置完成后不可切换。选项页不提供模式设置，重新配置及重新认证均保留原模式；已有旧配置固定为只读。

- **只读（默认）**：显示车况和行程，不发送车控指令。旧配置升级后保持只读。
- **全功能（含实验性车控）**：保留读取功能，开放下列已解析的控制；须在首次配置页面阅读并勾选风险确认。后续不可改为只读；需要停止所有请求时，可在 HA 禁用集成。已发往服务器的指令无法撤回。

[173项英文原始诊断字段](../reference/other-fields.md)归于“其他”标签；全部173项在两种模式中均默认开放，含义存疑的bmsPackSOH仅以Other原始字段保留，供需要者自行查找；用户手工禁用的实体保持禁用。旧占位实体继续禁用，未知字段不会凭空获得数值。新增车控按钮、空调温度/充电目标/方向盘和前排座椅级别设置，以及“车控状态”实体。

## 风险和责任说明

**全功能会发送真实车辆控制指令。** 解锁、车窗/后备箱/前舱盖动作可能影响人身、物品和车辆安全；空调、电池预热、充电和哨兵可能唤醒车辆或消耗电量。车况可能陈旧，网络可能超时，账号可能无权限，厂商接口可能随时变化。请只操作本人有权控制的车辆，并在每次操作前自行检查车辆、周边人员与障碍物。请谨慎配置自动化和语音助手。

本项目非官方产品，与车辆厂商无关联。**本版本所有新增车控均仅完成静态协议核对和模拟测试，尚未实车验证**；命令存在不代表每个车型或账号可用。“全功能”表示开放本项目已实现的控制，不表示覆盖官方 App 全部功能。

软件依照 [MIT 许可证](../../LICENSE) 按“原样”提供，不作明示或默示担保。在适用法律允许范围内，开发者及贡献者不对使用、误用、故障、自动化配置或车辆操作引发的人身伤害、财产损失、数据丢失等后果承担责任。使用者应自行评估风险并对自己的操作和自动化负责。本说明不承诺能够排除法律规定不能排除的责任。

只读模式的查询也可能唤醒车辆，不保证零耗电或不影响其他客户端会话。

## 协议与状态

依据本地授权分析的 App **3.2.4 / versionCode 353**：

| 项目 | 依据 |
|---|---|
| 旧协议 | POST `/app/vus/v3/vehicle/control` |
| 新协议 | POST `/app/vus/v4/vehicle/control3` |
| 协议选择 | 车辆首页 `vehicleFunction.features` 中是否存在 `support_3.0`。缺失/空值导致能力未知，阻止控制；不会按车型名称猜测 |
| 账号标识 | 登录响应 `uid`，来自 App `y0.C() → UserInfoGetBean.getUid()`；只存于加密会话，不要求在 HA 明文填写 |
| 请求 | `userAccountId`、`vin`、`commandType`、`commandValue`、`duration`、`operateDevice=ANDROID`，按实际 JSON 文本签名 |
| 哨兵 | POST `/app/vus/v4/vehicle/sentry/model`，`vin`、`sentryModeSource=2`、`sentryModeReq=1/2`、默认镜子请求字段0 |
| 返回 | 只标记 `accepted`（服务器已受理），`execution_confirmed=false`。未实现 App 推送事件确认链，不声称操作成功，不乐观改写车况 |

UID 不可用时会提示重新短信登录。旧版受管会话可从创建该会话的已提交加密登录响应恢复 UID；无法匹配的导入会话不会从别处猜测 UID。协议能力未知时请先“立即重新查询车况”；真实响应仍无能力标志则保持不可用。

每次指令仅发送一次。不会因超时、认证失效或拒绝自动重试，也不会从新协议回退重发旧协议。控制操作之间至少间隔3秒。服务端拒绝可在检查原因后重新操作；超时/中断后，新指令被持久阻止，读取仍可继续。**先检查车辆实际状态和加密记录，再在“重新配置 → 更新路径或恢复请求”勾选已完成排查并恢复请求**，才解除结果不明的控制阻止。这不会重放上一条指令。每次尝试及响应保存在加密 `controls/*.imvault`，最近状态保存在 `ha-control.imvault`；不要公开这些文件或删除 pending 状态绕过检查。

## 可用控制

`list_controls` 返回当前车辆协议下的候选动作，**不等于车型硬件支持清单或可用性验证**。功能仍受车型配置、车辆状态、车主/授权账号权限与服务端校验限制。以下英文标识可用于 HA 动作 `im_motors.vehicle_control`。

| 动作 | 参数 | 说明 |
|---|---|---|
| `lock` / `unlock` | 无 | 锁车/解锁 |
| `open_trunk` / `close_trunk` | 无 | 后备箱开/关，旧协议5/1，新协议1:0/2:0 |
| `unlock_trunk` | 无 | 仅旧协议，解锁与打开是不同动作 |
| `open_windows` / `close_windows` / `vent_windows` | 无 | 全车窗开/关/通风 |
| `find_car` / `stop_find_car` | 无 | 鸣笛闪灯；停止仅新协议，旧协议寻车duration=30 |
| `open_bonnet` / `close_bonnet` | 无 | 前舱盖，仅新协议 |
| `open_charge_port` | 无 | 充电口开启，仅新协议 |
| `start_charge` / `stop_charge` | 无 | 有线充电启停 |
| `battery_warm_on` / `battery_warm_off` | 无 | 电池预热，仅新协议 |
| `ac_on` / `ac_off` | 开启可选 `duration`，默认10，整数1–30 | 空调启停；duration用于App空调时间参数，不是HTTP重试超时 |
| `ac_temperature` | 必填 `temperature` 16–30；可选 `duration` 同上 | 旧协议整数，新协议允许0.5度步进 |
| `seat_heat` / `seat_vent` | `position`、整数 `level` 0–3 | 0关闭；座位见下文；硬件不支持时服务端可能拒绝 |
| `steering_heat` | 整数 `level` 0–3 | 方向盘加热，0关闭 |
| `charge_target` | 整数 `percent` 40–100 | 旧协议仅整十档；新协议直接百分比 |
| `charge_current` | `current`: `6A`、`8A`、`16A`、`max` | 仅新协议，映射App电流枚举1–4 |
| `schedule_charge` | `start_hour` 0–23、`start_minute` 0–59、`end_hour` 0–23、`end_minute` 0–59，均为整数 | 预约充电时间段 |
| `cancel_schedule_charge` | 无 | 关闭预约充电，仅新协议 |
| `sentry_on` / `sentry_off` | 无 | 哨兵开/关，使用独立接口 |

座位 `position`：`front_left`、`front_right`、`rear_left`、`rear_right`、`rear_middle`、`third_left`、`third_right`。协议有这些位置不代表车辆配有对应座椅或加热/通风。前排有常用数值实体，其他位置可使用服务。

尚未接入：远程启动/下电、冰箱、钥匙有效期、离车不下电、预约出行、独立鸣笛/闪灯等。虽然部分枚举或方法已发现，参数含义、完整调用条件或副作用还没有完成核对，不能仅凭枚举开放原始命令。没有任意URL或原始 `commandType` / `commandValue` 的发送入口。

## HA 动作示例

先运行 `im_motors.list_controls` 并请求返回值，获得车辆哈希 ID 与动作列表。`entry_id` 可在动作界面选择对应集成。单辆车可省略 `vehicle_id`，多辆车必须填写。通用控制服务供管理员和本地自动化使用；实体按钮遵循 HA 实体权限。

```yaml
action: im_motors.vehicle_control
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  action: open_trunk
```

```yaml
action: im_motors.vehicle_control
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  action: seat_heat
  parameters:
    position: front_left
    level: 1
```

HA 中显示的设置值来自下一次云端车况，不会在提交指令后立即改成请求值。需要核对时手动重新查询车况，并以现场车辆实际状态为准。

## 开发验证记录

0.7.0 在隔离、禁用网络的 HA 2026.11.2 / Python 3.14.10 容器中运行324项测试全部通过，包含模拟的车控请求、只读隔离、模式固定与修改拦截、账号及车辆范围、加密留存、请求中断及重启恢复。未调用真实车控接口。此前最低版本验证记录不代表本次对最低版本重新验证。

静态来源：`VehicleControlService` 的HTTP声明；`VehicleControlApi.Z/m0/j0/h0/i0/b0/c0/d0` 的旧协议构造；`vehicle.api.c.B/C/A/w` 的后备箱、门锁和车窗；`VehicleControlV3BatteryEngineApi` 的充电和预热；`VehicleControlV3AcApi` 的空调；`vehicle.api.e` 的座椅；`vehicle.api.d.v` 的哨兵。补充反编译了 `CarControlType`、`CarControlTypeV3` 和 `CarControlRequest`，确认枚举名称即发送字符串，以及默认 `duration=0`、`operateDevice=ANDROID`。原始私有反编译材料不随安装包发布。
