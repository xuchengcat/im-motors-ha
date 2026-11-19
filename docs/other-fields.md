# Other / 其他：原始诊断字段

0.7.2提供173个默认启用的英文原始信号入口，归于“其他”标签。**`Other / period.bmsPackSOH` 含义存疑，不能确认为电池健康度或百分比，仅保留原始值展示，不禁用。** 需要研究时在实体列表搜索 `bmsPackSOH`，或按“其他”标签查找。

旧“电池健康度”实体保留ID、改名并清除百分比单位。0.7.1由集成默认禁用的该实体会恢复启用；其他原始诊断字段保持启用，用户手动禁用的选择仍保留。

## 在 HA 中查找

实体名称统一为英文 `Other / group.fieldName`，同时添加 HA 标签 **其他**。可在“设置 → 设备与服务 → 实体”按此标签筛选。这些实体归于设备的诊断类别，不建立新的物理区域，也不影响原有常规电量、续航或位置实体。

`root` 表示 `data.category` 根；其他分组默认来自 `data.category.<group>`，只有 `displayConfig` 来自车辆首页外围 `data.displayConfig`。比如 `Other / period.bmsPackVol` 的来源是 `data.category.period.bmsPackVol`。

## 状态与释义

- 展示原始数值、字符串或布尔文本；不缩放、不将未知枚举解释成开关、不自动套单位，不设置统计类别。普通用户应优先使用原有已规范化实体。

- 缺失和null都显示unknown，但属性 `source_presence` 区分 `missing` / `null` / `value`；`validation` 说明长度或类型问题，`raw_type` 说明原始JSON类型。不会补零或假定硬件支持。

- 简单的一维JSON数组（例如星期设置）原样序列化；超过255字符的状态、过长数组、嵌套对象和不支持的类型显示unknown，避免把整个响应塞进HA属性。

- `meaning_zh` 是中文解释；`meaning_status=partially_confirmed` 只表示部分传递链已有证据，`inferred_from_name` 表示按字段名暂译，`unknown` 表示含义待定。含义确认不能替代枚举、单位或缩放核对；`unit_status`、`enum_status` 单独记录确认程度。已有证据的单位、缩放和部分原码说明放在 `unit_hint`、`scale_hint`、`known_codes`、`evidence_note` 中，仅供参考，不改变原始状态。

- “历史响应出现”也可能只是字段键存在或值为null；“仅模型声明”不代表本车服务端曾返回。当前缺少的字段仍会创建实体，供后续样本对照。

- 复用已有加密车况正文、缓存和查询间隔，不增加接口或云端请求，不开放任意字段透传；VIN、账号凭据、精确坐标不在此目录中。原始状态可保留在HA记录中，长时间存储量取决于Recorder配置。

## 协作核对方法

修改目录 `custom_components/im_motors/pyim_china/debug_fields.json` 中的解释和确认状态，并补充已脱敏的证据及测试。建议逐项记录：车辆配置和App版本、具体字段路径、现场操作前后原值、是否缓存、无效值、单位/缩放、完整枚举及异常码；字段名字相似不等于同一信号。可先从普通传感器与原始字段并排对照入手，不能用安全气囊、碰撞等危险操作取样。

可优先核对电压/电流、空调滤芯、哨兵设置与激活状态等日常可观察项；定义确认后再单独新增带单位或枚举的常规实体，保留现有原始实体ID与数据含义。

## 全部字段与暂定释义

以下全部原值输出，不声明HA单位。10个字段附已有映射确认的单位参考；部分充电状态、锁闭状态和胎压过滤码附已知原码说明，其余不推测完整枚举。

| 英文字段 | 暂定中文含义 | 含义确认 | 字段证据 |
|---|---|---|---|
| `ac.acAutoStatus` | 空调自动模式状态原码 | 按字段名推测 | 历史响应出现 |
| `ac.acFilterLife` | 空调滤芯寿命原始值 | 按字段名推测 | 历史响应出现 |
| `ac.acFrontBlowerSpeed` | 前排空调鼓风机风速原码 | 按字段名推测 | 历史响应出现 |
| `ac.acOnOffDspCmd` | 空调开关显示指令原码 | 按字段名推测 | 历史响应出现 |
| `ac.acRearBlowerSwitchStatus` | 后排空调鼓风机开关状态原码 | 按字段名推测 | 仅模型声明 |
| `ac.acStatus` | 空调运行状态原码 | 按字段名推测 | 历史响应出现 |
| `ac.eventTime` | 空调事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `ac.extremeTempControl` | 极端温度控制原码 | 按字段名推测 | 历史响应出现 |
| `ac.htdRrWndAbotRsn` | 后窗加热中止原因原码 | 按字段名推测 | 历史响应出现 |
| `ac.htdRrWndState` | 后窗加热状态原码 | 按字段名推测 | 历史响应出现 |
| `ac.rmtACAbortRsn` | 远程空调中止原因原码 | 按字段名推测 | 历史响应出现 |
| `ac.temperature` | 空调分组温度原始值（测点待核对） | 按字段名推测 | 仅模型声明 |
| `aiParking.aiParkingDisplay` | 智能泊车显示状态原码 | 按字段名推测 | 历史响应出现 |
| `aiParking.aiParkingStatus` | 智能泊车状态原码 | 按字段名推测 | 历史响应出现 |
| `aiParking.autodDrvngCndNotSatisfied` | 自动驾驶或泊车条件不满足原码 | 按字段名推测 | 历史响应出现 |
| `airbag.vehCrashType` | 车辆碰撞类型原码 | 按字段名推测 | 历史响应出现 |
| `basic.eventTime` | 车辆基础状态事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `basic.fotaStatus` | 固件空中升级状态原码 | 按字段名推测 | 历史响应出现 |
| `basic.powerSupplyWarning` | 供电告警原码 | 按字段名推测 | 历史响应出现 |
| `basic.sysPwrMd` | 车辆系统电源模式原码 | 按字段名推测 | 历史响应出现 |
| `basic.usgMd` | 车辆使用模式原码 | 按字段名推测 | 仅模型声明 |
| `bmsChrgCtrl.bmsChrgCtrlDspCmd` | BMS 充电控制显示指令原码 | 按字段名推测 | 历史响应出现 |
| `bmsChrgCtrl.bmsChrgCtrlResp` | BMS 充电控制响应原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.acExecStatus` | 预约出行空调执行状态原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.acStatus` | 预约出行空调设置状态原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.batteryWarmExecStatus` | 预约出行电池预热执行状态原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.batteryWarmStatus` | 预约出行电池预热设置状态原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookHour` | 预约出行小时原始值 | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookMinute` | 预约出行分钟原始值 | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookStatus` | 预约出行状态原码 | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookWeek` | 预约出行星期设置原始值 | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookWeekValue` | 预约出行星期值原始值（编码待核对） | 按字段名推测 | 历史响应出现 |
| `bookTrip.tripBookWeekly` | 预约出行每周重复设置原码 | 按字段名推测 | 历史响应出现 |
| `displayConfig.fuelValueN` | 显示配置中的燃油数值N（含义与单位待核对） | 含义待定 | 仅模型声明 |
| `door.eventTime` | 车门事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `door.pwrLftgtManuClsReq` | 电动尾门手动关闭请求原码 | 按字段名推测 | 仅模型声明 |
| `doorHandle.frontLeftDoorHandleStatus` | 左前门把手状态原码 | 按字段名推测 | 历史响应出现 |
| `doorHandle.frontRightDoorHandleStatus` | 右前门把手状态原码 | 按字段名推测 | 历史响应出现 |
| `doorHandle.rearLeftDoorHandleStatus` | 左后门把手状态原码 | 按字段名推测 | 历史响应出现 |
| `doorHandle.rearRightDoorHandleStatus` | 右后门把手状态原码 | 按字段名推测 | 历史响应出现 |
| `driver.driverFatigueLevel` | 驾驶员疲劳级别原码 | 按字段名推测 | 历史响应出现 |
| `fragrance.frgrLifeEndRmndrlabel1` | 香氛通道1寿命结束提醒原码 | 按字段名推测 | 历史响应出现 |
| `fragrance.frgrLifeEndRmndrlabel2` | 香氛通道2寿命结束提醒原码 | 按字段名推测 | 历史响应出现 |
| `fragrance.frgrLifeEndRmndrlabel3` | 香氛通道3寿命结束提醒原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsAlternatingChargeCurrent` | BMS 交流充电电流原始值 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsBasicState` | BMS 基础状态原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsChargeControl` | BMS 充电控制原码 | 按字段名推测 | 仅模型声明 |
| `hvBattery.bmsChargeStatus` | BMS 充放电状态原码 | 部分确认 | 已有诊断实体 |
| `hvBattery.bmsChrgSpRsn` | BMS 充电停止原因原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsDsChargeTargetSOC` | BMS 放电目标 SOC 原码 | 按字段名推测 | 仅模型声明 |
| `hvBattery.bmsFastPlugOn` | 快充插枪连接标志原码 | 按字段名推测 | 仅模型声明 |
| `hvBattery.bmsOnboardChargeTargetSOC` | BMS 车载充电目标 SOC 原码，不能直接按百分比解释 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsReservationControl` | BMS 预约充电控制原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.bmsSlowPlugOn` | 慢充插枪连接标志原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.ccuOffBdChrgrPlugOn` | CCU 非车载充电插枪连接标志原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.ccuOnbdChrgrPlugOn` | CCU 车载充电插枪连接标志原码 | 按字段名推测 | 历史响应出现 |
| `hvBattery.eventTime` | 高压电池事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `hvBattery.rmtBattWarmAbotRsn` | 远程电池预热中止原因原码 | 按字段名推测 | 仅模型声明 |
| `hvBattery.rmtBattWarmSts` | 远程电池预热状态原码 | 按字段名推测 | 仅模型声明 |
| `imcuCharge.imcuChrgTrgtSOCResp` | IMCU 充电目标 SOC 设置响应原码 | 按字段名推测 | 历史响应出现 |
| `imcuCharge.imcuReserChrgCtrlResp` | IMCU 预约充电控制响应原码 | 按字段名推测 | 历史响应出现 |
| `imcuCharge.imcuReserSpHourDspCmd` | 预约充电结束小时原始值 | 部分确认 | 已有组合判断输入 |
| `imcuCharge.imcuReserSpMinuteDspCmd` | 预约充电结束分钟原始值 | 部分确认 | 已有组合判断输入 |
| `imcuCharge.imcuReserStMinuteDspCmd` | 预约充电开始分钟原始值 | 部分确认 | 已有组合判断输入 |
| `imcuCharge.wrlsChrgrAutocWrlsChrgngSetngResp` | 无线充电自动充电设置响应原码 | 按字段名推测 | 仅模型声明 |
| `imcuCharge.wrlsChrgrAutocWrlsChrgngSetngSts` | 无线充电自动充电设置状态原码 | 按字段名推测 | 仅模型声明 |
| `light.centrConsoleLampSts` | 中控台灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.centrMapLampFLSts` | 左前阅读灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.centrMapLampFRSts` | 右前阅读灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.centrMapLampRLSts` | 左后阅读灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.centrMapLampRRSts` | 右后阅读灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.dipdBeamLightStatus` | 近光灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.fogLightStatus` | 雾灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.mainBeamLightStatus` | 远光灯状态原码 | 按字段名推测 | 历史响应出现 |
| `light.sidePositionLightStatus` | 位置灯状态原码 | 按字段名推测 | 历史响应出现 |
| `lock.eventTime` | 门锁事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `lock.vehLockingState` | 车辆锁闭状态原码 | 按字段名推测 | 已有诊断实体 |
| `motor.tractionMotorState` | 驱动电机状态原码 | 按字段名推测 | 历史响应出现 |
| `period.appBattNoFuChargReq` | App 电池未充满相关请求原码（具体用途待核对） | 含义待定 | 历史响应出现 |
| `period.battSOC` | 电池 SOC 原始值 | 按字段名推测 | 历史响应出现 |
| `period.battVoltage` | 电池电压原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsAvlblEnrg` | BMS 可用能量原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsChrgOtptCrntReq` | BMS 充电输出电流请求值 | 按字段名推测 | 历史响应出现 |
| `period.bmsOfbdChrgrOtptCrnt` | 非车载充电机输出电流原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsOfbdChrgrOtptVol` | 非车载充电机输出电压原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsPackCrnt` | 动力电池包电流原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsPackSOH` | 原始 bmsPackSOH 字段；含义存疑，不能确认为电池健康度或百分比；原始值展示 | 含义待定 | 历史响应出现 |
| `period.bmsPackSOC` | BMS 电池包 SOC 原始值 | 按字段名推测 | 历史响应出现 |
| `period.bmsPackSOCDsp` | BMS 显示电量原始值 | 部分确认 | 已有诊断实体 |
| `period.bmsPackVol` | 动力电池包电压原始值 | 按字段名推测 | 历史响应出现 |
| `period.chargedPower` | chargedPower 原始值，具体含义与单位尚未确认 | 含义待定 | 已有诊断实体 |
| `period.chargingRemainTime` | 充电剩余时间原始值，1023为估算中 | 部分确认 | 已有诊断实体 |
| `period.chrgngAddedElecRng` | 充电增加的续航里程原始值 | 按字段名推测 | 历史响应出现 |
| `period.chrgngSpdngTime` | 充电耗时原始值 | 部分确认 | 已有诊断实体 |
| `period.elecConsumePer100Km` | 百公里电耗原始值 | 按字段名推测 | 仅模型声明 |
| `period.eventTime` | 周期数据事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `period.fastCharge` | 快充相关标志原码 | 按字段名推测 | 历史响应出现 |
| `period.heading` | 车辆航向原始值 | 按字段名推测 | 历史响应出现 |
| `period.icbSetVehRngAlg` | ICB 车辆续航算法设置原码 | 按字段名推测 | 仅模型声明 |
| `period.icbVehElecRng` | ICB 纯电续航原始值 | 按字段名推测 | 仅模型声明 |
| `period.icbVehFuelRng` | ICB 燃油续航原始值 | 按字段名推测 | 仅模型声明 |
| `period.imcuChrgngEstdElecRng` | IMCU 充电估算续航原始值 | 按字段名推测 | 历史响应出现 |
| `period.imcuVehActuElecRng` | IMCU 实际算法续航原始值 | 按字段名推测 | 历史响应出现 |
| `period.imcuVehCltcElecRng` | IMCU CLTC 续航原始值 | 按字段名推测 | 历史响应出现 |
| `period.inCarAirQuality` | 车内空气质量原始值 | 按字段名推测 | 历史响应出现 |
| `period.lateralAcceleration` | 横向加速度原始值 | 按字段名推测 | 历史响应出现 |
| `period.longAcceleration` | 纵向加速度原始值 | 按字段名推测 | 历史响应出现 |
| `period.onBdChrgrAltrCrntInptCrnt` | 车载充电机交流输入电流原始值 | 按字段名推测 | 历史响应出现 |
| `period.onBdChrgrAltrCrntInptVol` | 车载充电机交流输入电压原始值 | 按字段名推测 | 历史响应出现 |
| `period.outsideCarAirQuality` | 车外空气质量原始值 | 按字段名推测 | 历史响应出现 |
| `period.remainDrvDistance` | 剩余可行驶距离原始值（算法待核对） | 按字段名推测 | 历史响应出现 |
| `period.vehOdo` | 车辆累计里程原始值 | 部分确认 | 已有诊断实体 |
| `period.vehSpeed` | 车速原始值 | 按字段名推测 | 历史响应出现 |
| `period.version` | 周期数据版本标识（具体格式待核对） | 按字段名推测 | 历史响应出现 |
| `powerTrain.eptEnergyRecoveryLevel` | 动力系统能量回收级别原码 | 按字段名推测 | 历史响应出现 |
| `powerTrain.eptReadyStatus` | 动力系统 Ready 状态原码，参与运行分类 | 部分确认 | 已有组合判断输入 |
| `powerTrain.remoteVehStartEPTCrankAbortRsn` | 远程启动动力系统起动阶段中止原因原码 | 按字段名推测 | 历史响应出现 |
| `powerTrain.remoteVehStartEPTCrankAborted` | 远程启动动力系统起动阶段中止标志原码 | 按字段名推测 | 历史响应出现 |
| `powerTrain.remoteVehStartEPTRunAbortRsn` | 远程启动动力系统运行阶段中止原因原码 | 按字段名推测 | 历史响应出现 |
| `powerTrain.remoteVehStartEPTRunAborted` | 远程启动动力系统运行阶段中止标志原码 | 按字段名推测 | 历史响应出现 |
| `powerTrain.remoteVehStartStatus` | 车辆远程启动状态原码 | 按字段名推测 | 历史响应出现 |
| `root.isConnectedUpdateTime` | 车辆连接状态更新时间 | 按字段名推测 | 历史响应出现 |
| `root.offLineDisconnect` | 离线断连标志原码 | 按字段名推测 | 历史响应出现 |
| `root.offLineTime` | 车辆离线时间 | 按字段名推测 | 历史响应出现 |
| `root.onLineTime` | 车辆上线时间 | 按字段名推测 | 历史响应出现 |
| `seat.flSeatHeatLvl` | 左前座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.flSeatVentLvl` | 左前座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.frSeatHeatLvl` | 右前座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.frSeatVentLvl` | 右前座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.seatHeatingAbortRsn` | 座椅加热中止原因原码 | 按字段名推测 | 历史响应出现 |
| `seat.seatVentAbortRsn` | 座椅通风中止原因原码 | 按字段名推测 | 历史响应出现 |
| `seat.slSeatHeatLvl` | 二排左座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.slSeatVentLvl` | 二排左座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.smSeatHeatLvl` | 二排中座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.smSeatVentLvl` | 二排中座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.srSeatHeatLvl` | 二排右座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.srSeatVentLvl` | 二排右座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.tlSeatHeatLvl` | 三排左座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.tlSeatVentLvl` | 三排左座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.tmSeatHeatLvl` | 三排中座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.tmSeatVentLvl` | 三排中座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seat.trSeatHeatLvl` | 三排右座椅加热原始级别 | 部分确认 | 已有诊断实体 |
| `seat.trSeatVentLvl` | 三排右座椅通风原始级别 | 部分确认 | 已有诊断实体 |
| `seatbelt.driverSbltStatus` | 主驾安全带状态原码 | 按字段名推测 | 历史响应出现 |
| `seatbelt.frontPassengerSbltStatus` | 副驾安全带状态原码 | 按字段名推测 | 历史响应出现 |
| `seatbelt.secondRowLeftSbltStatus` | 二排左安全带状态原码 | 按字段名推测 | 历史响应出现 |
| `seatbelt.secondRowMidSbltStatus` | 二排中安全带状态原码 | 按字段名推测 | 历史响应出现 |
| `seatbelt.secondRowRightSbltStatus` | 二排右安全带状态原码 | 按字段名推测 | 历史响应出现 |
| `sentryMode.rearviewMirrorTypeSts` | 哨兵相关后视镜类型或模式状态原码 | 按字段名推测 | 历史响应出现 |
| `sentryMode.sentryModeActiveSts` | 哨兵实际激活状态原码 | 按字段名推测 | 历史响应出现 |
| `sentryMode.sentryModeSetSts` | 哨兵设置状态原码 | 按字段名推测 | 历史响应出现 |
| `steeringWheel.steeringWheelHeatAbortRsn` | 方向盘加热中止原因原码 | 按字段名推测 | 历史响应出现 |
| `steeringWheel.steeringWheelHeatingStatus` | 方向盘加热状态原码 | 按字段名推测 | 已有诊断实体 |
| `warning.airbagDeployedStatus` | 安全气囊展开状态原码 | 按字段名推测 | 历史响应出现 |
| `warning.bmsWarningStatus` | BMS 告警状态原码 | 按字段名推测 | 历史响应出现 |
| `warning.brakeFluidLevelLowStatus` | 制动液液位低告警原码 | 按字段名推测 | 历史响应出现 |
| `warning.eventTime` | 告警事件时间原始值 | 按字段名推测 | 仅模型声明 |
| `warning.forwardWarningStatus` | 前向告警原码（告警类型待核对） | 按字段名推测 | 历史响应出现 |
| `warning.frontLeftTireStatus` | 左前轮胎状态原码，参与胎压显示过滤 | 按字段名推测 | 已有组合判断输入 |
| `warning.frontRightTireStatus` | 右前轮胎状态原码，参与胎压显示过滤 | 按字段名推测 | 已有组合判断输入 |
| `warning.leftWarningStatus` | 左侧告警原码（告警类型待核对） | 按字段名推测 | 仅模型声明 |
| `warning.lowWiperFluidStatus` | 玻璃清洗液不足告警原码 | 按字段名推测 | 历史响应出现 |
| `warning.rearLeftTireStatus` | 左后轮胎状态原码，参与胎压显示过滤 | 按字段名推测 | 已有组合判断输入 |
| `warning.rearRightTireStatus` | 右后轮胎状态原码，参与胎压显示过滤 | 按字段名推测 | 已有组合判断输入 |
| `warning.rightWarningStatus` | 右侧告警原码（告警类型待核对） | 按字段名推测 | 仅模型声明 |
| `warning.samStaWarningStatus` | SAM 状态告警原码（SAM含义待核对） | 含义待定 | 仅模型声明 |
| `warning.securityAlarmTriggeredStatus` | 防盗报警触发状态原码 | 按字段名推测 | 历史响应出现 |
| `window.flWndOpenSts` | 左前车窗开启状态原码（不同于已展示的开度百分比） | 按字段名推测 | 仅模型声明 |
| `window.frWndOpenSts` | 右前车窗开启状态原码（不同于已展示的开度百分比） | 按字段名推测 | 仅模型声明 |
| `window.rlWndOpenSts` | 左后车窗开启状态原码（不同于已展示的开度百分比） | 按字段名推测 | 仅模型声明 |
| `window.rrWndOpenSts` | 右后车窗开启状态原码（不同于已展示的开度百分比） | 按字段名推测 | 仅模型声明 |
| `wiper.frontWiperModeStatus` | 前雨刷工作模式原码 | 按字段名推测 | 历史响应出现 |
| `wrlsChrg.wrlsChrgrAuxFnDetResult` | 无线充电辅助功能检测结果原码（检测对象待核对） | 按字段名推测 | 历史响应出现 |

## 验证记录

完整自动化测试324项通过（HA 2026.11.2 / Python 3.14.10，容器禁用网络），覆盖全部字段原值、缺失/null及不支持类型、HA默认启用及标签、旧实体ID保留与升级启用、无额外云端请求。释义核对仍按各字段的确认状态进行，测试通过不等于这些候选字段已有本车有效读数。
