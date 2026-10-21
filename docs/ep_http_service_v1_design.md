# HTTP 服务级持续性协议（提案，未冻结、未实现、未运行）

> 状态：**设计提案**。不修改 v2，不进入任何冻结清单，未执行任何 trial。
> 目的：把故障对象从"某一个结果页 URL"改为"站点的搜索服务端点"，使换 query 仍然
> 访问同一故障服务，从而真正检验"重试/改查询后仍失败会怎样"。

## 1. 为什么需要新协议

v2 锁定**完整资源 URL**。task 118 的个案显示：`q=mouth+guard` 出错后，agent 换成
`q=night+guard` 就完成了任务——这是访问**另一个资源**，按 v2 定义属于合法绕行。
若要研究"搜索服务持续失效"，故障对象必须是**端点**，而不是单个查询结果。

## 2. 实验身份与范围

- `experiment_id`: `ep-http-service-v1`
- 独立于 `ep-http-persistence-v2`、Stage C 与 E-P v1；不与之合并分析。
- 仍为**观察层**模拟（不拦截真实 HTTP、不撤销浏览器副作用），措辞不得称为真实断连。
- 单次与持续两种策略、同期控制、相同触发规则与 500/503 抽样。

## 3. 故障对象（事前冻结的端点清单）

每个站点在冻结清单中给出：

```json
{
  "shopping": {
    "host": "localhost:7770",
    "path_prefix": "/catalogsearch/result/",
    "query_keys": ["q"]
  },
  "reddit": {
    "host": "localhost:9999",
    "path_prefix": "/search",
    "query_keys": ["q"]
  }
}
```

匹配规则：

- **scheme + host + path 前缀**必须一致（只去 fragment，保留端口）；
- URL 必须带至少一个清单中的 query key（`q` / `search`），**query 值不参与匹配**；
- 其他路径（首页、分类页、商品页、分页 `/reviews?p=2`）**不在范围内**，保持正常；
- 目标在运行前确定并写入冻结清单；运行中**不得扩大**到其他端点或站点。

这样 `?q=mouth+guard` → `?q=night+guard` 仍命中同一故障服务，而分类导航/商品页
是可用的替代路径。

## 4. 触发与释放

- 初始导航保持干净；进入 Agent 执行前激活（沿用 v2）。
- 首次合格导航：显式 `goto`/刷新，或 click/back/forward 等导致资源 URL 改变，
  且新 URL **匹配端点清单**。
- 被动观察、同页点击、fragment-only 不激活。
- `single`：第一次匹配观察替换后，下一动作恢复。
- `persistent`：从 t0 起在**预设动作窗口**（默认 10 个 proxy 执行步，`[t0,t0+9]`）
  内，每次对匹配端点的观察都替换；窗口结束释放。没有三次上限。
- 同一 action 重读观察保持错误但不另计注入；只按新动作计次数。

## 5. 证据与验收

每条 trial 写 `fault_evidence.json`：`snapshot`（端点、t0、window_end、注入次数、
释放原因）、`events`、`exposures`（每次观察的 URL、是否匹配端点、匹配的 query key、
是否投递、原因）；SHA-256 写入 trial record。trial record 记录协议元数据
（`target_policy_version`、端点模式、窗口、`bootstrap_navigation_faulted=false`、
`retarget=false`）。

四臂块验收标准（不要求成功率下降）：

1. 首次合格导航**确实匹配端点**，且 control 臂 0 注入；
2. 换 query 的同一端点观察**仍然被替换**（这是与 v2 的关键区别）；
3. 非端点 URL（首页、分类、商品页）保持干净；
4. `persistent` 在窗口内至少两次投递，或明确记录"agent 未再访问该端点"；
5. 窗口到期恢复；证据哈希与记录一致；无机制错误/基础设施错误；
6. 成本可接受（记录实际 token 用量）。

## 6. 分析

- 主口径为**按分配**（ITT）：全部已分配且有合法官方评分的配对，含未触发；
- `D_f = Δ_persistent − Δ_single`，先在同一 block 内计算，再跨任务等权；
- 实际触发/重复次数只作机制描述，不做确认性推断；
- 4 任务版本不输出确认性 p 值；
- 与 v1/v2 的记录分开报告，效果不跨版本合并。

## 7. 实现与冻结顺序（待批准后执行）

1. 在**新分支**实现端点匹配（不复用 `fault_target_url`；新增
   `fault_target_endpoint`/冻结清单），复用 v2 的 episode/证据/释放校验；
2. 离线测试：query 变化仍命中、路径前缀边界、不同 host、非端点页面、窗口释放、
   两架构一致的重试/失败处理、证据哈希；
3. 零付费验收通过后，写冻结清单（端点模式、窗口、`target_policy_version`、
   数据集哈希、代码 commit）；
4. 先跑一个**同期四臂块**（single/persistent × control/fault），验收机制后再决定
   是否扩展；不以"必须失败"作为通过条件。
