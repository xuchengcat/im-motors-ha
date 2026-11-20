# 主账号每日自动签到

## 使用

集成选项中选择主账号并开启“每日随机自动签到（主账号，02:00–06:00）”。默认关闭，授权账号不会执行。
每天按 HA 时区选定一个随机秒，保存计划后等待执行。02:00包含、06:00不包含；重启后窗口内补跑已错过的计划，窗口外安排次日。

每日签到状态实体显示最近结果和下一次计划；`already_checked` 表示记录确认原本已签，`checked` 表示提交后记录确认成功，`unknown` 表示提交结果待核实，`error` 表示查询或认证失败。后两者不进行当日循环提交。

## App 协议依据

2026-11-20 获取 App 实际加载的签到 H5 页面 `/app/community/checkin`，资源版本 `af508e20`：

- `main-af508e20.js` 的 API host 为 `https://m.immotors.com/app/planet`。
- `1538.af508e20.js` 定义 `/api/v1/checkin/info`、`record`、`check`。
- `checkin.af508e20.js` 模块12547：info 使用GET；record 使用GET，参数为 `startTime`、`endTime`；check 使用POST，无业务字段，默认JSON对象为 `{}`。
- 签到页面用 record 的 `blocks[].signTime` 是否包含今天判断是否签到，并保留 `signType`；`signSwitch` 是签到提醒开关，不能当成今日签到状态。
- `6002.af508e20.js` 请求模块22724向 App 桥传 method、完整URL、nonce、timestamp、encrypt、原始query/body。
- APK `ImJsCommonImpl2.getApiSignature` 调用 `SignUtils.g`：大写method、完整URL、排序后的签名头、非空query、非空JSON body，以 `&` 连接；整个文本按Java URL编码规则编码，再用AppH5Secret做HMAC-SHA256、Base64。签名头是 `x-app-key=android-h5`、`x-encrypt=false`、`x-nonce` 和 `x-timestamp`；POST的 `{}` 也参与签名。与原生接口只签path的规则不同。

使用精简H5请求头、Bearer令牌、`x-original-path`、版本和浏览器来源。实测将原生设备/版本请求头直接套入info会返回22064；H5头配置成功。通用协议参数沿用工程已有H5配置，不把个人凭据写入源码。

## 持久化和避免重复

会话使用原账号加密存储与账号文件锁，不另建短信登录。计划和最近10天执行记录放在同目录加密的 `checkin-state.imvault`。提交POST前原子落盘 `submitting`；进程中断后只回查签到记录，未查到成功也不再次自动提交该日POST。网络超时、认证失败和结果未知不会触发短信或补签。

先查询info和record，确认服务端日期与本机日期一致且时差不超过5分钟，未签到才提交check；提交后再查record确认。所有POST限制在check路径，不开放re-check、盲盒、领奖或其他活动操作。

## 已验证范围

2026-11-20，使用当前HA主账号WXAPP会话：info和record实测成功，record存在当天 `signTime`、`signType=0`，与用户已签到的说明一致。今天无需重复签到，因此没有向真实账号发送check POST；POST签名、未签到分支、超时/中断去重通过模拟测试验证，真实未签到后的服务端验收待后续日程执行。
