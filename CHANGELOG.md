# v0.8.0b4（本机验收版，未发布 HACS）

- 主账号新增可选每日自动签到，在 HA 时区02:00–06:00内持久随机调度，先查签到信息与记录，已签到即跳过。
- 复现 App H5 的额外签名，保留 WXAPP 认证和账号锁；签到前持久记录提交状态，结果不明不重复提交。
- 新增每日签到状态实体；重启恢复同一随机计划，超过窗口不在白天补跑，不使用补签卡和额外奖励接口。

# v0.8.0b3（本机验收版，未发布 HACS）

- 主账号每天凌晨3点（HA时区）自动检查近60天，按行程ID只下载缺失或资料变化的详情；首次启用或当日未运行时启动补跑，重启可续传。
- 每日执行记录与归档进度持久保存；失败日期保留原数据，认证与协议错误停止请求，避免反复重试。
- 日历日期、行程列表和轨迹详情只读本地数据库；日历点击不再触发远程同步，仍保留手动补同步入口。
- 官方历史过期后仍保留本地日汇总、轨迹和地址；远程空结果不清零既有历史。

# v0.8.0b2（本机验收版，未发布 HACS）

- 主账号轨迹默认隐藏中间点标识，保留完整轨迹线与起终点；可勾选“显示轨迹点”恢复中间点标识。
- 起终点没有文字地址时，明确提示已有坐标；未配置高德 Key 与已配置但尚未补全分别显示。

# v0.8.0b1（本机验收版，未发布 HACS）

- 新增主账号官方行车日历：日期标记、每日汇总、完整分页、单次行程与轨迹地图；仅显式同步所选日期，每次最多31天。
- 官方历史独立存储，主账号不采集本地轨迹；接口失败与无行程分开显示，未提供的轨迹点时间保持未知。
- 主账号实测日历314个可选日期、单日2段行程及532个轨迹点，独立测试历史库同步成功。

- 新增主账号 / 授权账号配置，支持首次配置、重新配置、重新认证与选项；旧配置默认授权账号。
- 仅授权账号可启用现有本地轨迹记录，运行时按账号类型拦截采集；保留已有历史。主账号签到留待后续开发。
- 新短信登录实验性使用 `WXAPP`；加密保存客户端类型，刷新与恢复保持一致，旧会话和旧短信流程默认 `APP`。
- 主账号实测短信登录、只读车辆查询及刷新成功；用户确认手机 App 保持登录后再次读取车况成功，短期双端共存验证通过，长期行为仍待观察。

# v0.7.2

- bmsPackSOH保留在 `Other / period.bmsPackSOH`，默认启用，不再因移入“其他”而禁用。仍撤回健康度/百分比解释，不设置单位、缩放或统计类别。
- 恢复0.7.1由集成默认禁用的原始实体；保留旧实体ID、清除旧健康度命名与百分比单位，用户手工禁用仍受尊重。
- “其他”的173项字段均默认启用。首次配置固定模式的界面规则保持不变。

# v0.7.1

- 撤回将 bmsPackSOH 解释为电池健康度或百分比的结论。改为无单位、不缩放的 `Other / period.bmsPackSOH` 原始诊断字段，两种模式均默认禁用，需要者自行查找启用。
- 旧“电池健康度”实体保留ID、改名并迁移禁用；用户此后手动启用的选择保持不变。README特别说明字段含义、单位和缩放尚未确认，历史数字不能证明健康度百分比。
- “其他”共173个字段入口，其中172项默认启用。不增加查询，不改变配置时固定的只读/全功能模式。

# v0.7.0

- 开放172项英文原始诊断字段，两种模式均默认启用并添加“其他”标签；附中文暂定释义和证据状态，24个旧原始实体复用ID，新增148个实体。复用现有车况缓存，不增加云端请求。
- （此解释已在v0.7.1撤回）新增默认启用的电池健康度（SOH）传感器，读取 period.bmsPackSOH，按0–100%原值显示并保留小数；两种模式均可用，不新增接口或轮询，缺失/null/越界值显示未知。
- 新增只读 / 全功能运行模式，仅首次配置时选择，之后不可切换；重新配置和重新认证保留原模式，旧配置固定为只读。
- 全功能开放已解析的实验性车控按钮、数值设置与受限服务；协议由车辆 support_3.0 标志选择，不在失败后切换协议或重发。
- UID 仅保存于加密会话；指令与响应加密留存，超时或中断后须检查车辆并人工恢复。
- 新增界面风险说明与文档。服务器受理与实际执行分别表述，尚未实车验证。

# Changelog

## Unreleased

- 单车账号允许车况响应省略 VIN 或返回 null，按请求车辆接收并缓存车况，避免正常车况因缺少 VIN 导致整个集成暂停；明确 VIN 不匹配和多车缺少 VIN 仍停止。
- 增加单车缺少 VIN 的读取、缓存和重启恢复，以及多车缺少 VIN 的回归测试。
- 临时 GET 网络错误及 HTTP 408/429/5xx 使用持久退避重试，避免一次读取故障导致集成长时间暂停。
- 车辆读取退避期间仍维护认证；过期且未提交登录的短信验证码流程不再阻塞有效会话。
- 保留未知登录、令牌刷新结果及身份/数据校验异常的停止机制，并记录脱敏异常类型与时间。
- 增加验证码失效、跨重启退避、退避期间续期和 GET/POST 重试边界的回归测试。

## 0.6.2

- 按启动/熄火保存独立行程，同日支持多段；起终点使用长时间稳定的停车定位确认。
- 新增本地轨迹详情接口、地图路线与逐点时间表，保留全部已采用样本。
- 熄火时保存行程，后续确认稳定终点时更新同一段；证据不足留空，不复用缓存或其他行程的位置。
- 轨迹地图只为已确认的稳定停车点绘制起终点标记。
- 地址补全按匹配坐标合并，避免旧查询覆盖后确认的位置；基于留存样本重算前归档旧记录。
- 继续遵循既有车况查询间隔，不增加查询频率。

## 0.5.1

- 起终点改为连续稳定的停车位置：50米范围至少10分钟、两个独立云端样本，里程不增加；离开100米或里程增加开始记录，临时P挡不直接结束。
- 保留停车锚点作为下一次起点，终点回溯首次到达，处理定位漂移、定位缓存、缺失和停车确认期间的采样缺口。
- 原始样本回放重算历史，旧版本保留在 trip_revisions；无覆盖样本的历史保持原样。

## 0.5.0

- 使用现有车况采样自行记录未来行程，不访问车主专用日历接口或更换账号。重复/过旧/缺少云端时间的快照跳过；记录采样边界、缺口、里程回退和漏采短途推定区间。
- SQLite 长期保存样本、行程、按出发日汇总、地址缓存与重启续接状态，独立于 HA Recorder 保留期。
- HA 配置页增加本地记录开关、高德 Web服务 Key 加密存储和查询间隔；侧边栏提供历史查询、进行中状态、日期筛选、JSON/CSV 导出和可选导入。
- 无 Key 时保存坐标，高德失败保留行程；已查询地址缓存复用。查询只读本地数据库，手动采样复用原有 v6 查询和认证/故障停止机制。

## 0.4.5

- Fix valid location becoming unavailable when the cloud changes the observed coordinate format code from 1 to 0. The App 3.2.4 direct autonavi path does not branch on this field; support observed codes 0/1 with the same GCJ-02 interpretation, without claiming a complete manufacturer enum.
- Add safe format and location-status counts to diagnostics, so unsupported formats and invalid/missing coordinates can be distinguished even when HA removes unavailable tracker attributes.
- Add regression coverage for a live entity switching from format 1 to 0, decimal string precision and malformed format codes; keep coordinates out of diagnostics.

## 0.4.4

- Add an enabled-by-default total mileage sensor from `period.vehOdo`, mapped directly to integer km after corroboration with the owner’s LS6 instrument cluster. Other models still require verification.
- Use distance device class and cumulative total statistics without a reset timestamp; share existing automatic and manual telemetry reads.
- Keep valid zero, reject missing/negative/out-of-range readings instead of fabricating zero, and preserve the historical raw diagnostic and pending entity identities.

## 0.4.3

- Add a GPS device tracker using coordinates already present in the encrypted, VIN-checked v6 cloud snapshot; automatic and manual queries update location together with telemetry.
- Validate decimal coordinate strings, ranges and missing values separately so malformed location does not break other telemetry.
- Convert the observed format 1 from GCJ-02 to WGS-84 based on the App 3.2.4 autonavi geocoder call chain; leave unsupported formats unavailable. This interpretation is not a complete manufacturer enum.
- Preserve snapshot timestamps without claiming an independently verified GPS sampling time or accuracy; keep coordinates out of diagnostics.

## 0.4.2

- Add a per-vehicle “立即重新查询车况” button to fetch cloud telemetry immediately, bypassing only the local telemetry cache and resetting the next automatic query time.
- Serialize scheduled and manual account requests; preserve authentication checks, VIN validation and durable failure stops.

## 0.4.1

- Default cloud vehicle polling to 60 minutes; expose an integer-minute interval during SMS setup and encrypted-session import.
- Reject intervals below five minutes in both configuration flows and the runtime client; reuse the configured cache across manual updates and restarts.
- Apply the one-hour default to existing entries without the setting and preserve custom intervals through reauthentication. Allow changing it through reconfiguration.
- Report the effective interval in entity attributes and diagnostics; retain separate local auth checks and metadata caching.

## 0.4.0

- Replace pending vehicle data with VIN-scoped v6 reads, cached for five minutes across manual updates and restarts. Reads may wake the vehicle.
- Add battery percentage, separate CLTC/estimated ranges, four tyre pressures and temperatures, cabin/outside/weather temperatures, and left/right AC setpoints.
- Add doors, covers, window positions, online/connected flags, observed App lock display and the APK's combined operating classification.
- Map 17 charging states, feature-dependent charge target and reservation times. Gate current durations/power by explicit charging modes; retain cached raw durations separately.
- Extend the parser to 129 category model fields, feature presence and homepage metadata without exposing locations or private identity in HA entities.
- Keep missing/null/unknown values unknown; leave odometer and chargedPower unitless, and disable raw seat/steering/state diagnostics by default.
- Show cloud snapshot/retrieval timestamps and source presence; root freshness does not establish individual sensor freshness.
- Reject missing or mismatched response VINs and preserve encrypted responses and a durable failure marker before retry could occur. Authentication remains manual after failure.
- Preserve SMS setup, old credentials, hashed device identities and disabled legacy placeholders. Validate HA lifecycle, cache, crash and cross-VIN isolation using synthetic fixtures.

Mapping evidence covers an LS6. Other models and long-running live HA polling need deployment validation; no vehicle controls, location or active refresh flow are introduced.

## 0.3.0

- Bundle common protocol parameters separately from personal account storage.
- Simplify SMS setup to phone number and verification code, without manual protocol files or key mounts.
- Generate a random local 256-bit account encryption key per HA instance and keep it under the HA configuration directory.
- Persist device identity and encrypted sessions in account directories derived using a keyed phone hash; keep phones and codes out of HA entries.
- Authenticate the local storage marker before reuse; missing or changed keys stop setup instead of silently creating a different account.
- Preserve v0.1/v0.2 encrypted sessions, external keys, custom protocol overrides and existing entity identities.
- Correct the version reported by diagnostics and extend release auditing to permit only the approved common protocol file while still rejecting personal secrets.

Common protocol parameters are public. Personal encryption keys and account sessions are generated locally and never included in releases. Image/risk verification and real vehicle telemetry remain pending.

## 0.2.0

- Add mainland China phone input, SMS send and verification-code login in the HA configuration UI.
- Support SMS reauthentication and login from reconfiguration, retaining existing client identity and entities.
- Create a persistent device identity and encrypted session when only protected protocol configuration and its matching key are supplied.
- Keep encrypted session import available; store only paths and hashed identity in HA entries.
- Allow explicit SMS resend after 60 seconds and resume an existing valid challenge without sending another SMS.
- Retain request markers and encrypted responses before parsing; stop unknown outcomes and provide offline login recovery.
- Pause account polling during authentication and refuse to overwrite a session changed after login began.
- Add translated errors for invalid/expired codes, server rejection, image/risk verification and account binding.

Protocol configuration and its external key remain required. Image/risk verification is not implemented in the HA UI. Vehicle telemetry remains pending. New SMS integration tests use synthetic data and mocked HTTP, with live network blocked.

## 0.1.0

- Initial HACS release of the read-only IM Motors integration.
- Import portable encrypted sessions without storing tokens or keys in HA configuration.
- Register associated vehicle devices and account metadata entities.
- Preserve missing and null metadata as unknown; do not substitute false or zero.
- Refresh account credentials at the server's suggested time and persist rotated credentials.
- Stop requests after failures or interruption until manual review and recovery.
- Provide redacted diagnostics and disabled placeholders for unverified vehicle telemetry.
- Include installation, data mapping, backup and recovery documentation.

Live SOC, range, charging and other vehicle telemetry remain unverified and are not queried.
