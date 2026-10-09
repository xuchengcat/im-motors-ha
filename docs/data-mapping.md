# 数据对应关系与待定字段

账号元数据已完成真实读取验证。车况字段的源码对应关系与真实可读取能力分别记录；
找到模型字段或看到历史 App 界面，不代表当前集成已经取得该数据。

## 当前 HA 实体

| 实体 | 来源与语义 |
|---|---|
| 关联车辆设备 | `/app/vus/v3/isc/scene/vehicleList` 的 `data[].vin`，以 SHA-256 标识设备 |
| 设备名称 | 固定“智己汽车”；不展示账号自定义名称 |
| 车型项目代码 | 场景列表 `projectCode`，缺失时 unknown |
| 支持场景设置 / 功能 | 场景列表 `hasSetting` / `hasSupport`；是能力元数据，非实时车辆状态 |
| 凭据到期时间 | 登录/刷新 `expirationTime`，毫秒 Unix 时间 |
| 建议刷新时间 | 登录/刷新 `suggestRefreshTime`，控制账号刷新调度 |
| 元数据更新时间 | 本次读取记录的本机时间，不是车辆上报时间 |
| 车况解析状态 | 本地固定“待定” |

场景接口可能只返回 VIN、名称、图片/车牌元数据和 `themeStatus`，并不返回全部 Java 模型字段。
缺失和 null 保持 unknown；明确 false 保持 false，明确零保留零。
Java primitive boolean 的默认 false 不能作为服务端实际返回证据。

车辆管理接口 `/app/vus/v4/vehicle/management` 的 `role`、`isChargeSetting`、`otaSupport`
和 `isSwitch` 不在当前 HA 实体数据源内，也不能替代场景能力标志。
管理响应可能没有 VIN，不能仅按车辆名称与场景列表合并。

## 实时车况：尚未开放查询

候选聚合路径为 `/app/capp-vus/v3/vehicle/category/all`。App 还可能从缓存和 WebSocket
推送获取相同模型值。查询服务端副作用尚未明确，当前集成不会调用这个路径。

| 数据 | 已找到的模型/展示关系 | 尚待验证 |
|---|---|---|
| SOC | `period.originalBmsPackSOCDsp` 进入电池显示链；快充、取整及功能开关影响最终显示 | 本车功能值、显示分支和真实值 |
| 页面主续航 | 非 HYBRID 分支优先 `cltcVehElecRng`，否则 imcu 取整；HYBRID 分支取仪表电续航 | 本车分支与同期正文/UI 对值 |
| 卡片续航 | 与页面主续航不同，存在 icb / imcu 选择链 | 不能所有续航实体共用一个字段 |
| 总里程 | `period.vehOdo` | 显示调用、单位及实值 |
| 充电状态 | `hvBattery.bmsChargeStatus`，不是非零即充电 | 完整枚举及真实状态 |
| 充电功率 | `period.power` / `electricPilePower`，UI 为 kW；选择受状态及功能开关影响 | 本车条件及真实值 |
| 已充电量候选 | `period.chargedPower` 与瞬时功率字段独立 | 含义与单位；不猜 kWh |
| 充电剩余时间 | `period.chargingRemainTime`，分钟；1023 为“估算中” | 实际返回值 |
| 门窗、车锁 | door/window/lock 模型及开度候选 | 枚举、单位和实值；图标不作为状态证据 |
| 胎压 | period 四轮独立字段 | 缩放、单位和异常值 |
| 在线 / 连接 | 独立 `isOnLine` / `isConnected` 及时间字段 | 枚举和真实值；驻车不代表在线 |
| 位置 | period 经纬度候选 | 坐标体系、采样时间及查询副作用 |

这 11 类车况占位默认禁用且 unavailable，手动启用也不会查询或补造数值。
手机状态栏电量、天气温度、控制按钮和历史截图不能代替车辆实时读数。
