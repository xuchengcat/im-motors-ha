# 车辆数据对应关系（v0.4.5）

本版将已确认字段接入 HA。依据包括授权 LS6 云端正文、APK 模型与展示调用，以及手机首页、胎压和充电设置页面。手机页面与云端正文不是同时采样，能支持字段对应，不证明每个值长期实时。

## 读取和归属

场景接口 `/app/vus/v3/isc/scene/vehicleList` 提供账号关联 VIN；设备标识为 VIN 的 SHA-256。VIN 仅保存在加密账号文件中，设备名称固定为“智己汽车”，不使用车牌、车主或用户自定义名称。

车况从 `/app/vus/v6/tab/vehicle` 的 `data.category` 解析，参数固定为 `sourceCode=APP`、本车 VIN、`tabType=VEHICLE` 和持久客户端型号。响应 category 的 VIN 必须与请求及关联车辆一致，缺失、null 或不一致均停止读取。多车响应各自验证，车辆从账号列表移除后不展示其缓存。

场景数据缓存30分钟；每辆车况按配置间隔缓存，默认60分钟、最短5分钟，重启和常规实体更新复用缓存，手动查询按钮可跳过车况缓存。只读请求可能唤醒车辆。本版不调用 v5 主动刷新、旧 category 请求、独立即时位置或车辆控制接口，不启动推送/UDP 接收。

## 默认启用的车况

除特别标注外，下表字段位于 `data.category`。缺失、null、未知枚举和转换失败均显示 unknown；缺少字段不伪造为零或关闭。

| 实体 | 字段与转换 |
|---|---|
| 电量 | `period.originalBmsPackSOCDsp`，0..100%，保留原始小数，不模仿 App 取整 |
| CLTC / 估算续航 | `period.cltcVehElecRng` / `imcuVehElecRng`，km，分开显示，不为所有车型决定首页算法 |
| 总里程 | `period.vehOdo`，整数 km，不缩放、不加偏移；LS6 本车仪表盘已核对；缺失、null、负数和32位最大值及以上不展示，合法0保留 |
| 四轮胎压 | `period.frontLeft/frontRight/rearLeft/rearRightTirePressure`，浮点除以100为 bar；保留两位精度，非正数或相应 `warning.*TireStatus=1` 不显示 |
| 四轮胎温 | `period.fl/fr/rl/rrTireTem`，°C，四轮独立 |
| 车内 / 车辆外温 | `period.acInCarTemperature` / `outsideCarTemperature`，°C |
| 天气温度 | 外围 `data.weatherInfo.temperature`，有限数值字符串转 °C，不与车辆外温混用 |
| 左右空调设定 | `ac.aclTemDspCmd` / `acrTemDspCmd`，°C，零不套用 App 默认25 |
| 四窗开度与开闭 | `period.frontLeft/frontRight/rearLeft/rearRightWindowPosition`，0..100%；0关闭、正值打开，不补造缺失的 window 分组状态码 |
| 四门 | `door.frontLeftDoorStatus`、`frontRightDoorStatus`、`rearLeftDoorOpenStatus`、`rearRightDoorOpenStatus`，仅0/1为关/开，其他码 unknown |
| 前舱盖 / 尾门 / 充电口盖 | `door.bonnetOpenStatus` / `trunkOpenStatus` / `chargeCapOpenStatus`，0/1为关/开 |
| App 云端锁闭显示 | `lock.vehLockingState=3` 显示锁闭，其他码待定。APK 分支为 `<2 → OPENED`、`>=2 → CLOSED`；完整协议枚举和物理/BLE 来源未闭合，本版保守只使用已核对码3 |
| 在线 / 连接 | 根 `isOnLine` / `isConnected`，独立0/1；驻车不等于在线 |
| 运行分类 | `basic.shifterPosition` 与 `powerTrain.eptReadyStatus`：1/1→ready，(0或1)/0→parking，>=2/1→driving，其余 unknown；不把单独挡位码1命名为P挡 |
| 充电状态 | `hvBattery.bmsChargeStatus`，按下表0..16枚举，未知码不归为“未充电” |
| 充电目标 | `imcuCharge.imcuChrgTrgtSOCDspCmd`；完整 features 含 `support_3.0` 时直接使用1..100%，否则1..7→40/50/60/70/80/90/100%；列表缺失/null或未知码保持 unknown，不采用 App 默认80% |
| 当前充电剩余时间 | `period.chargingRemainTime`，分钟；1023估算中、负数无效；仅状态1/10/12/16显示 |
| 当前充电耗时 | `period.chrgngSpdngTime`，分钟，仅状态1/10/12/16显示，不把停止后的历史耗时称为当前耗时 |
| 车辆功率 / 桩功率 | `period.power` / `electricPilePower`，kW，分开显示；仅状态1/10/12/16显示非负值；字段缺失不填0，不复制 App 条件选择链 |
| 预约开启 / 起止 | `imcuCharge.imcuReserCtrlDspCmd=1` 为时段开启，3为仅开始时间；其他控制码待定。起止来自 `imcuReserSt/SpHourDspCmd` 与 `St/SpMinuteDspCmd`，校验有效时分；结束不晚于开始解释为次日 |
| 首页系列 / 项目代码 | 外围 `data.vehicleFunction.vehicleSeries` / `projectCode`，独立于场景字段 |
| 云端更新时间 | 根 `updateTime`，毫秒 Unix 时间；四个 Java Long 时间字段兼容实际观察到的 ASCII 十进制字符串，其他数字字段不强制转换字符串 |

App 可能按在线、蓝牙来源、车型功能或控制进度改变显示。本集成展示云端字段，功能支持标志不能证明数据存在或当前状态。

## 充电状态枚举

| 原码 | 状态 |
|---|---|
| 0 | none：未充电 |
| 1 | on_board_charging：车载充电 |
| 2 | charge_done：充电完成 |
| 3 | balancing：均衡充电 |
| 4 | charge_fault：充电故障 |
| 5 | connecting：连接中 |
| 6 | connected_not_recognized：已连接但未识别 |
| 7 | connected_not_charged：已连接但未充电 |
| 8 | charge_cease：充电中止 |
| 9 | charge_reserved：预约充电 |
| 10 | off_board_charging：非车载充电 |
| 11 | discharging_gun_connecting：放电枪连接中 |
| 12 | multiple_charging：多路充电 |
| 13 | discharging：放电中 |
| 14 | cease：中止 |
| 15 | discharging_done：放电完成 |
| 16 | wireless_charging：无线充电 |

枚举来自 APK。名称不足以为所有车型区分 AC/DC；预约和状态9不等于正在充电。均衡与放电未纳入本版当前充电时间/功率显示范围。

## 车辆定位

定位来自 `data.category.period.latitude` / `longitude`，与其他车况使用同一个已按VIN校验的加密快照，不新增网络接口。字段可为有限数值或ASCII十进制字符串；缺失、null、单轴0、超范围、布尔值或格式错误不展示位置，也不会使其他车况解析失败。

本车已观测到 `coOdntSysFmt=1` 和 `0`，服务端会在不同行驶/快照周期切换。APK 3.2.4 的 `vehicle.viewmodel.v3.r.j` 读取 Period 经纬度后保存 `(longitude, latitude)`；`WeakTipsViewModel.s` 用该位置创建高德 `RegeocodeQuery(..., "autonavi")`，中间 `tt.c.e` 只重建 LatLonPoint，没有坐标转换。这条展示调用链没有读取或判断 `coOdntSysFmt`。**据此将已观测格式0/1均按该链路的 GCJ-02 来源解释**，输出给 HA 前在本地逆转换为 WGS-84。未取得完整厂商枚举，其他格式码保持 unavailable，不假定2或其他未观测格式代表何种坐标。

定位实体属性给出来源、坐标解释依据、云端根时间和本机查询时间。没有独立GPS采样时间或精度字段，不把根时间解释为定位采样时间，不增加地址反查请求。坐标只发布在定位实体；定位实体提供安全的原格式码。HA诊断仅增加格式码计数与失败状态计数，不含经纬度，定位不可用时也可查看拒绝原因。

## 诊断原码与未确认内容

解析器保留129个已找到模型类型的 category 字段，类型与含义分开验证。新增默认禁用的诊断实体包括：BMS 显示 SOC、原始充电状态、原始剩余/耗时、车锁原码、方向盘加热及16个座椅加热/通风级别。座椅 `fl/fr/sl/sm/sr/tl/tm/tr` 分别保留，缺失座位不填0，未闭合的档位不包装成控制选项。

`period.vehOdo` 的 km 单位已与本车车辆仪表盘相同读数对照确认，默认启用独立总里程实体（key=odometer），不加偏移。健康页是独立 H5，其历史读数存在差异；该页的来源和独立更新时间仍未确认，不替代车况数据。历史原始里程诊断仍保留无单位身份以避免改变已有记录。累计里程采用 `SensorStateClass.TOTAL`，无 `last_reset`，不把云端旧快照可能造成的下降解释为计数器归零；选择依据 [HA 传感器文档](https://developers.home-assistant.io/docs/core/entity/sensor/#how-to-choose-state_class-and-last_reset)。其他车型单位仍需实车验证。`period.chargedPower` 传递链已找到，但显示单位未闭合，保留无单位诊断，不加 kWh 或能量统计类别。定位以以下章节为准。

旧版11个占位实体保持默认禁用和 unavailable，供既有安装保留身份；实际读数使用新增实体，启用占位不会产生读数。

## 时间、失败和元数据

车况实体属性提供 `source_field`、`source_presence`、`cloud_updated`、`retrieved_at`、`cloud_snapshot_age_seconds`、`individual_sample_time_verified=false`。根时间可能较旧，离线时可能返回缓存。各分组时间在真实样本中缺失，根时间新鲜不等于所有分组刚采样。

现有元数据保持语义：认证到期/刷新来自账号凭据，元数据更新时间是本机获取时间，场景 `hasSetting/hasSupport` 是能力而非当前状态。车辆管理不在本版数据源中，管理字段不能按名字合并。

请求前持久保存故障标记，返回正文先加密保留再解析；成功且 VIN 一致才提交缓存并清除标记。失败或中断停止后续账号请求，重启不能自动重发。认证失败需手动处理；业务码22905/22908不按 token 过期处理。诊断只输出版本、计数、开关和待定类别，不输出 VIN、token、路径、天气地区或完整响应。

车况更新间隔可在创建时的短信登录或会话导入页面设置，单位为分钟，默认60，必须为不少于5的整数。已有集成未配置此项时使用60分钟；可在重新配置的路径页面修改，重新认证保留原设置。账号认证仍每分钟本地检查，元数据仍缓存30分钟，两者不使用车况间隔。
