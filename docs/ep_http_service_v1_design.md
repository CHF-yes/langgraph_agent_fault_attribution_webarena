# HTTP 服务级持续性协议（提案，未冻结、未实现、未运行）

> 状态：**已实现、离线验收通过、冻结清单已生效；仅批准一个四臂块，尚未运行**。不修改 v2，不进入任何冻结清单（冻结清单见候选文件），
> 未执行任何 trial。目的：把故障对象从"某一个结果页 URL"改为"站点的搜索服务端点"，
> 使换 query 仍然访问同一故障服务，从而检验"重试/改 query 后仍失败会怎样"。

## 1. 为什么需要新协议

v2 锁定**完整资源 URL**。task 118 的个案显示：`q=mouth+guard` 出错后，agent 换成
`q=night+guard` 就完成了任务——这是访问**另一个资源**，按 v2 定义属于合法绕行。
若要研究"搜索服务持续失效"，故障对象必须是**端点**，而不是单个查询结果。

## 2. 实验身份与范围

- `experiment_id`: `ep-http-service-v1`
- 独立于 `ep-http-persistence-v2`、Stage C 与 E-P v1；不与之合并分析。
- 仍为**观察层**模拟（不拦截真实 HTTP、不撤销浏览器副作用），措辞不得称为真实断连。
- 单次与持续两种策略、同期控制、相同触发规则与 500/503 抽样。

## 3. 端点定义与匹配（**确定性规则，事前冻结**）

端点由四项组成，配置必须显式给出 **scheme**：

```json
{
  "experiment_id": "ep-http-service-v1",
  "endpoints": {
    "shopping": {"scheme": "http", "host": "localhost", "port": 7770,
                 "path": "/catalogsearch/result"},
    "reddit":   {"scheme": "http", "host": "localhost", "port": 9999,
                 "path": "/search"}
  },
  "window_steps": 10,
  "target_policy_version": "endpoint-path-segment-v1"
}
```

匹配规则（实现必须逐条测试）：

1. **scheme** 必须一致（`http` 与 `https` 不互相匹配）。
2. **host** 必须一致（大小写不敏感）；**port** 必须一致；缺省端口按 scheme 归一化
   （http=80，https=443）。
3. **path 边界**：先去掉配置 path 末尾的 `/`（根路径除外）。匹配条件为
   `path == configured` 或 `path.startswith(configured + "/")`。
   - `/search` 与 `/search/results` 属于同一端点；
   - **`/searching`、`/searching/x` 不属于**（必须有路径段边界）。
4. **query 完全不参与匹配**：`q` 的值、额外参数（如 `p`、排序参数）都不影响。
   `/catalogsearch/result/?q=mouth+guard`、`?q=night+guard`、`?q=night+guard&p=2`、
   `?q=x&sort=price` 都命中同一端点。
5. **fragment 忽略**（沿用 normalize_url）。
6. 其他路径（首页、分类页、商品页、评价分页 `/reviews?p=2`）**不在范围内**。

分页的边界必须按"是否搜索端点"判断，而不是"是否分页"：

| URL | 预期 |
|---|---|
| `/catalogsearch/result/?q=mouth+guard` | 受影响 |
| `/catalogsearch/result/?q=night+guard` | 受影响 |
| `/catalogsearch/result/?q=night+guard&p=2` | **仍受影响**（搜索端点的分页） |
| `/reviews?p=2` | 正常（非搜索端点） |
| 商品页、分类页 | 正常 |

目标在运行前确定并写入冻结清单；运行中**不得扩大**到其他端点或站点。

## 4. 触发与释放

- 初始导航保持干净；进入 Agent 执行前激活（沿用 v2）。
- 首次合格导航：显式 `goto`/刷新，或 click/back/forward 等导致资源 URL 改变，
  且新 URL **匹配端点**。
- 被动观察、同页点击、fragment-only 不激活。
- `single`：第一次匹配观察替换后，下一动作恢复。
- `persistent`：从 t0 起在**预设动作窗口**（默认 10 个 proxy 执行步，`[t0,t0+9]`）
  内，每次对匹配端点的观察都替换；窗口结束释放。没有三次上限。
- 同一 action 重读观察保持错误但不另计注入；只按新动作计次数。

## 5. 证据、运行合法性与验收覆盖（三者分开）

证据文件 `fault_evidence.json`：`snapshot`（端点身份、t0、window_end、注入次数、
释放原因）、`events`、`exposures`（每次观察的 URL、是否匹配端点、匹配的
scheme/host/port/path、query、是否投递、原因）；SHA-256 写入 trial record。

- **运行合法性**：证据完整、身份/协议参数一致、控制臂 0 注入、无机制/基础设施错误。
  即使 `persistent` 只有一次暴露，也可以是一条**合法**记录。
- **验收覆盖**：某条验收项是否被真实运行覆盖，必须单独标记。例如 `persistent` 只
  暴露一次时：
  > 运行与证据合格；本条**没有覆盖**"换 query 后仍被阻断"这一项。
  不得把"合法运行"同时算作该项验收通过；不得因此自动扩量，也不得强迫 Agent 重试。
- 离线测试只能证明代码行为；真实覆盖由四臂块的实际暴露记录决定。

四臂块验收清单（逐项标记 covered / not covered，不要求成功率下降）：

1. 首次合格导航**确实匹配端点**；control 臂 0 注入；
2. **换 query 的同一端点观察仍被替换**（真实覆盖项，未发生则标 not covered）；
3. 非端点 URL（首页、分类、商品页、`/reviews?p=2`）保持干净；
4. 搜索端点分页（`?q=...&p=2`）仍受影响；
5. 窗口到期恢复；证据哈希与记录一致；无机制错误/基础设施错误；
6. 成本可接受（记录实际 token 用量）。

## 6. 分析

- 主口径为**按分配**（ITT）：全部已分配且有合法官方评分的配对，含未触发；
- `D_f = Δ_persistent − Δ_single`，先在同一 block 内计算，再跨任务等权；
- 实际触发/重复次数只作机制描述，不做确认性推断；
- 4 任务版本不输出确认性 p 值；
- 与 v1/v2 的记录分开报告，效果不跨版本合并。

## 7. 实现与冻结顺序

1. 新分支实现端点匹配（`endpoint-path-segment-v1`），复用 v2 的 episode/证据/释放校验；
2. 离线测试：scheme/host/port、路径段边界（含 `/searching`）、query 值/额外参数、
   搜索分页、非搜索分页、窗口释放、两架构一致的重试/失败处理、证据哈希；
3. 零付费验收通过后，写**候选**冻结清单（端点模式、窗口、policy version、数据集
   哈希、代码 commit），提交**四臂运行计划**；
4. 获得授权后才做同期四臂块；验收按上面的 covered / not covered 逐项记录；
   不以"必须失败"作为通过条件，不自动扩量。
