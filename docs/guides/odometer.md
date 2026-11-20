# 总里程来源与更新

[返回文档导航](../README.md)

## 主、副账号使用不同来源

| 账号类型 | 接口 | 字段 |
|---|---|---|
| 主账号 | `GET /app/planet/api/v1/drive/daily/new?tripDate=YYYY-MM-DD` | `data.totalMileage` |
| 授权账号（副账号） | `POST /app/planet/api/v1/maintenance/status`，JSON包含所选车辆VIN | `data.mileage` |

两个接口都位于 `https://m.immotors.com`，单位均为km，不除以1000、不取整或加偏移。保留原“总里程”实体ID、device_class=distance与state_class=total，不重置HA历史。主账号来源从v0.8.0b5接入，副账号诊断来源从v0.8.0b6接入。

主账号始终请求 **Asia/Shanghai当天**。查询旧日期会得到当时累计里程，不能作为当前读数。授权账号的POST是读取车辆健康/保养状态，不生成诊断报告，不预约保养或发送车控。

原v6 `category.period.vehOdo` 在本车2026-11-18晚以后缺失，只保留为旧缓存与“其他”原始诊断。IM AD累计里程、增程里程和距下次保养里程不作为总里程。

## 新鲜度与车辆校验

健康/诊断数据可能比行程数据滞后。2026-11-20实测，主账号行程累计为38492 km；副账号健康接口为38445 km，返回更新时间08:08。此差异不是自动缩放或偏移的依据，也不证明固定刷新周期。

实体属性中 `source_field` 标明取值字段，`source_updated` 保存健康接口的 `updateTime`；该时间是响应中的状态更新时间，未证明为里程独立采样时间。`retrieved_at`只是本机读取时间。

主账号日历链路目前仅支持关联一辆车，且校验返回VIN。副账号诊断请求按车辆标识在受保护关联表中选择VIN，显式请求该VIN；当前响应不返回VIN，如后续返回冲突VIN则拒绝。每辆车的缓存独立，实际访问仍受服务端账号权限限制。

## 查询与缓存

- 默认每60分钟查询，最短5分钟，沿用车况配置间隔；手动“立即重新查询车况”会同时刷新里程。
- 跨越上海日期时，下一次协调器检查会刷新来源。
- 不依赖官方行程归档开关，不下载行程列表或轨迹。
- 私有缓存 `odometer.imvault` 按账号角色和车辆分别存储；主账号升级时复用旧有效缓存，重载不重复请求。
- 切换角色不会共用两种来源的最近确认值，避免把滞后的健康数据误判为主账号里程倒退。请勿以频繁切换角色校准里程；统计采用的官方来源和新鲜度本就不同。

同一来源读数降低时不伪造重置；失败时显示unknown，最近确认值仍留在加密缓存用于核查，不当作新读数发布。普通失败按配置间隔重查，不每分钟反复请求，也不影响其他有效车况。

## 状态属性

`source_field`、`source`、`trip_date`（仅主账号）、`source_updated`（健康状态）、`retrieved_at`、`last_attempt`、`status`和`error`用于排查，不包含VIN或令牌。

| error | 含义 |
|---|---|
| `not_vehicle_owner` | 官方拒绝该账号的接口访问 |
| `multiple_vehicles` | 主账号日历关联车辆不唯一 |
| `vehicle_unavailable` / `vehicle_mismatch` | 所选车辆无有效关联，或返回VIN冲突 |
| `invalid_odometer` | 缺失、null、非数值、负数或无效哨兵 |
| `odometer_decreased` | 同一来源低于最近确认值 |
| `authentication_failed` / `account_unavailable` | 认证或账号状态不可用 |
| `storage_unavailable` | 私有缓存无法读取或保存 |

需要备份时一并保存实际账号目录与密钥，见[备份指南](backup-and-recovery.md)。代码位置和角色边界见[开发指南](../development/contributing.md)。
