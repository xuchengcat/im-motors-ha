# 总里程来源与更新

[返回文档导航](../README.md)

## 新来源

v0.8.0b5起，现有“总里程”实体读取官方行程H5接口：

```text
GET https://m.immotors.com/app/planet/api/v1/drive/daily/new?tripDate=YYYY-MM-DD
字段：data.totalMileage
单位：km
```

始终请求 **Asia/Shanghai当天**。查询旧日期会得到当时累计里程，不能当成当前读数。字段允许整数和小数公里，不除以1000、不取整、不加偏移。原实体ID、device_class=distance和state_class=total保持，不重置HA历史统计。

原v6字段 `category.period.vehOdo` 在本车2026-11-18晚之后的响应中缺失；它仍是旧缓存和“其他”原始诊断中的字段，不再驱动总里程实体。车辆健康接口的 `mileage` 可能较旧，本次不把它作为回退；IM AD累计里程和距下次保养公里数也不作为总里程。

## 何时查询

- 首次加载检查新缓存，之后沿用车况配置间隔，默认60分钟、最短5分钟。
- “立即重新查询车况”会同时刷新总里程。
- 凌晨跨越上海日期时，下一次协调器检查会请求新一天的数据。
- 缓存及最近确认值加密保存在账号目录 `odometer.imvault`，重启不绕过有效缓存。
- 此读取独立于“每日自动归档官方行程”开关，未开启归档也可更新总里程。每次需要刷新时会额外进行一次H5 GET，不会下载行程列表或轨迹。

## 账号权限与错误

已实测当前主账号。集成对账号关联进行校验，目前这条H5链路仅支持关联一辆车。授权账号是否能读取取决于服务端权限；本地选成主账号不会授予权限。接口失败不会停止其他有效车况更新，不能用昨日里程或估算补出当前读数。

实体属性包含 `source_field`、`trip_date`、`retrieved_at`、`last_attempt`、`status` 和 `error`。`retrieved_at`是读取时间，不能当作里程独立上报时间。

| error | 含义 |
|---|---|
| `not_vehicle_owner` | 服务端拒绝非车主账号 |
| `multiple_vehicles` | 关联车辆不唯一，避免混用累计值 |
| `vehicle_mismatch` | 返回VIN缺失或与所选车辆不一致 |
| `invalid_odometer` | 字段缺失、null、非数值、负数或无效哨兵值 |
| `odometer_decreased` | 新值低于最近确认值，保留核查，不伪造里程重置 |
| `authentication_failed` / `account_unavailable` | 认证或账号状态不可用 |
| `storage_unavailable` | 私有缓存无法读取或保存 |

失败时显示unknown；最近确认值仍留在受保护缓存用于防止倒退，不作为新读数发布。普通失败按配置间隔再检查，支持明确的手动重新查询；不会每分钟反复请求失败接口。需要备份时一并保存账号目录及密钥，见[备份指南](backup-and-recovery.md)。
