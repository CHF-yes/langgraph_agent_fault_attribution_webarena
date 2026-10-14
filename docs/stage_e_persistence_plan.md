# E-P：兼容 Stage C 的持续故障实验设计

设计日期：2026-10-14。状态：设计草案，尚未实现、尚未运行。
核对基线：`origin/stagec-exec-chain @ 35cda5c`。
本文件定义实施和验收契约；下文新增 CLI 参数须实现后才能执行。

## 1. 要回答的问题

在相同模型、架构、任务、步数预算和评分规则下，把一次性扰动改为在同一目标上
重复出现的有限时长扰动，是否增加成功率退化、动作开销和预算耗尽？

原 768 条 Stage C 是已经看过结果的一次性故障实验，完整保留。
E-P 是根据该结果提出的后续探索实验。旧 Stage E 的“注入步扫描”不变更含义；
E-P 单独表示 persistence，不同时扫描起始步、错误类型、强度和模型。
这项研究验证的是持续暴露的影响，不以“必须显著”或“Agent 必须失败”为验收条件。

## 2. 固定因素和唯一新增因素

沿用 `deepseek_v41_flash`、`qwen38_flash`，以及 `react`、`plan_execute`。
沿用三个 fault_type、`max_steps=20`、官方 evaluator、HAR、response、trial_record、
独立 run_id、相同 job seed 的 control/fault 配对。thinking、temperature、工具集、
回答协议、模型端点、任务数据集和 evaluator 版本在本批冻结并记录。

| 因素 | single | repeat3 |
|---|---|---|
| condition | 各有 control / fault | 各有 control / fault |
| 强度 | high | high |
| 首次注入位置 | HTTP/DOM：proxy execution step 2；参数：第 1 个参数动作 | 相同 |
| 首次扰动及随机抽样 | 沿用现有 Stage C 实现 | 同种子首次扰动与 single 相同 |
| 最多实际注入次数 | 1 | 3 |
| 目标 | 首次注入选出的目标 | 锁定同一个目标，不重新随机换目标 |
| 活跃窗口 | 首次实际注入后 5 个 proxy 执行动作，包含首次 | 相同 |
| 释放 | 首次注入后释放 | 第 3 次注入后，或窗口结束后释放，取先发生者 |

`repeat3` 表示最多三次、最多五动作窗口的重复暴露，不保证每条 trial 恰好注入三次。
如果 Agent 绕开目标、提前终止或未再次触及目标，实际次数可以为 1 或 2。
该行为本身需要报告，不能为了凑足三次而强迫导航或把故障扩到整个站点。
“同种子首次扰动相同”仅在相同的原始输入观察/动作下成立；独立的远端模型运行
可能在首次注入前已走到不同页面，不能保证所有 variant 或模型碰到同一语义目标。
应记录并描述首次目标与注入前轨迹的分布，不能事后挑出目标相同的 trial 当主样本。

窗口使用 proxy 的 execution_step：初始 goto 也可能占用一次，必须与当前实现一致。
trace.step、proxy.execution_step、parameter_action_index 分别记录，不再假定数值相同。
设第一次实际注入的 proxy 步为 t0，窗口为 [t0, t0+4]；t0+5 起完全释放。
同一 action_uid 内，工具返回和后续重复读取观察不额外消耗次数，planner 调用也不计步。
刷新/重新 goto 是新动作，命中同一目标时应再次遭遇故障。
所有计数器都是每 trial 独立状态，不能是共享模块全局变量。

## 3. 三种故障的精确定义

### 3.1 web_http_error：同页重复的合成错误观察

首次沿用 `inject_http_error` 的 500/503 抽样，记录扰动前的 URL 作为目标。
窗口内每次产生该目标页的新观察时，返回同一种假错误页；刷新一次不能清除故障。
URL 匹配基于扰动前的真实 URL，不用 `about:error` 作为目标。
最小规范化只移除 fragment；保留 path、query 和站点端口，不合并分页或不同资源。
其他 URL 不受此 episode 影响，允许 Agent 寻找替代路径。

当前框架实际完成导航后替换观察，并不产生真实 HTTP 500，HAR 也可能记正常请求。
E-P 继续这个层级以保持 single/repeat3 可比；论文称“合成 HTTP 错误观察”。
若另做真实网络拦截，应另设机制版本，不与本设计的持续性比较混在一起。

### 3.2 web_dom_missing：相同元素的重复观察屏蔽

首次沿用 Stage C 的 seed 抽样，从当前观察选择 high 对应的最多三个元素。
不根据任务答案或 evaluator 真值选择“关键元素”，也不因试跑效果小而重新挑目标。
记录它们的语义身份，在后续同页观察中重新识别、屏蔽同一批元素。

身份来自原始 element_map/页面可取得的 role、name、href/稳定 selector 和页面 URL；
本地 AX 数字 id 只作当步定位，不能跨刷新作为身份。实施时须把实际可用的
字段、规范化和多候选判定算法写入版本化 `target_policy`。
匹配不唯一时跳过本次，记录 ambiguous；元素本来不存在时记录 absent。
两者都不消耗注入次数，不能随机删除别的元素补足剂量。

每次观察重建后重新应用屏蔽，屏蔽结果同步到 page_content 与 element_map。
这是观察层遗漏，真实 DOM 不删；Agent 若找到其他有效访问路径可以完成任务。
不把“不同轨迹的 AX id 真子集”当作注入证据，证据直接来自注入器的前后差异。

### 3.3 agent_param_error：同目标参数动作重复损坏

首次沿用当前动作变换：element_id 改为 invalid_<n>，如有 text 则保留现有文本污染规则。
锁定“真实页面 URL + 原动作类型 + 原目标元素语义身份”，匹配发生在参数改写前。
窗口内同类动作再次指向同一语义目标时，重放同一参数损坏规则。
重新读取页面获得新 AX id，不能自动解除该目标的故障；改用不同目标或动作允许绕开。

原动作没有可建立稳定身份的目标时，不把它静默改成任意下一参数动作的故障：
记录 target_unresolvable，整个格子仍保留在按分配分析中，机制分析单列。
第一次不能锁定目标的情况也必须在 single 中同样标注。
已有参数动作资格判定沿用原实现，不为降低未触发率扩大到原本不注入的工具。

## 4. 为什么同时重跑 single

单次与 repeat3 各用原框架的一组 control/fault。四个新 trial 构成一个 block：
single/control、single/fault、repeat3/control、repeat3/fault。
控制臂是两次独立运行，不复制或引用同一条 control 来伪装两条独立观测。
控制臂 intensity=off、injection_step=None、实际注入数=0；variant 仅表示所属 block。

同 block 用同一任务和 seed，在尽量相同时间窗口交错运行；轮换两个 variant 顺序、
两个 condition 顺序、模型和架构顺序。用确定性调度 seed 写出 schedule.json。
seed 主要控制故障随机抽样和配对标识，不声称能使远端模型输出完全确定。
旧 768 条只作历史对照；持续性效应以这批同期 single 为参照。

## 5. 任务、预算与分阶段执行

四任务机制子集：24（shopping review）、27（reddit）、132（gitlab repo）、
118（public navigation）。它们来自旧任务集；选择参考了 Stage C 结果，明确属于探索性
子集，不称为独立随机抽样或新的确认性主实验。
控制成功率高的导航任务可以测退化；地板任务才没有进一步下降空间。

| 阶段 | 配置 | 新 trial 数 |
|---|---|---:|
| 离线验收 | mock 工具/观察、状态机、产物审计；不调用模型 API | 0 |
| 首批可行性 | DS Flash × 两架构 × task 24/118 × 三故障 × seed 1 × 两 variant × 两 condition | 48 |
| 四任务完整探索 | 两模型 × 两架构 × 四任务 × 三故障 × seeds 1/2 × 两 variant × 两 condition | 384 |

384 包含满足同一冻结协议的那 48 条，余下 336 条，不能把两行相加。
若首批后修改 Agent 可见信息、目标策略、窗口或注入逻辑，则首批独立归档，不能合入新版本。
首批只决定实施是否正确及预算是否可接受；不根据 p 值、成功率下降幅度或模型胜负决定放行。
384 是提议预算，不代表已授权运行；以首批实测 token/缓存/输出/账单估价，后续需明确运行授权。

四任务每类只有一个，按四类分层 bootstrap 会退化为固定样本，因此该规模不输出确认性
p 值或把退化区间作为跨任务泛化证据。主要提供逐任务、逐 seed 效果和聚合描述。
若需要任务级推断，须在扩展采集前另行冻结至少每类两个任务的方案；例如八任务版在
其余因素不变时为 768 条。任务数和重复数不能等同为独立证据量。

## 6. 指标与推断

对故障 f、variant v，定义 Δ_v = P(官方成功|control,v) - P(官方成功|fault,v)。
核心持续性对比为 D_f = Δ_repeat3 - Δ_single；正值表示持续性增加成功率退化。
先在同 task/model/arch/fault/seed 内计算，再在任务之间等权汇总。
三种故障分别报告；模型×架构仅作分层描述，四任务版本不以交互显著性为目标。

主口径使用全部已分配且有合法官方评分的配对，包括未触发、部分剂量、提前结束。
实际触发子集以及三次完整剂量子集另列为选择性机制描述，不能解释为随机分配的因果效果。
Evaluator error/incomplete/missing 单列并阻断完整矩阵推断，不当作 Agent 的任务失败。
Agent 自报成功不能代替 official_success。

同步报告：实际注入次数 0/1/2/3、暴露于模型的次数、首次/末次注入步、窗口过期原因、
目标缺失/歧义、离开目标页、最终成功、耗尽率、fault-control 的步数/调用数/墙钟/token 差。
时延和费用分开；缓存字段未知保留 null；成本使用真实 endpoint 价格与计费模式。

“同类工具下一步无报错”保留原名作描述，不称为恢复成功。
另新增可验证的“释放后同目标动作成功”：必须匹配原目标、动作语义、合法结果，
且发生于故障释放之后。记录同目标的失败、绕路和放弃，不能仅用工具名称判断重试。
DOM 可见性用精确注入日志与实际送入模型的 observation_uid 连接，不猜测模型主观察觉。

## 7. 最小框架改动与兼容性

| 位置 | 需要的扩展 |
|---|---|
| `fault_injection/config.py` | episode 状态机；single 默认不变；新增 variant、budget、window、目标状态 |
| `fault_injection/web_faults.py` | 固定错误样本重放；锁定 DOM 目标，不按次重新随机删除 |
| `fault_injection/agent_faults.py` | 参数污染前匹配语义目标；重放同一损坏规则 |
| `fault_injection/proxy.py` / `injector.py` | action_uid、execution_step、观察缓存去重和暴露事件；检查实际调用链 |
| `main.py` | 透传配置、保存 episode 记录与 canonical run_id/trace；control 按 off 处理 |
| `scripts/run_fault_matrix.py` | variant 外层调度、独立输出、指纹/resume 新字段、结构化注入校验 |
| `trial_metadata.py` / `stage_c_pipeline.py` | E-P schema、身份/配对/唯一性/完整性检查、只读已评分结果 |
| 新 E-P 分析入口 | 按 variant 计算差中差，保留原 Stage C 分析默认行为 |

目前 `main.py` 的实际入口是 `FaultInjector`（`injector.py`）；`FaultProxy`
（`proxy.py`）不是同一个类。核心验收必须通过实际入口，不能仅改辅助 proxy 后声称已接入。

新增参数的计划命名：`--experiment-id ep-persistence-v1`、
`--fault-variant single|repeat3`、`--fault-window-steps 5`。
budget 由 variant 固定，不提供本轮任意修改开关。
实现前不能直接把这些参数加到服务器命令执行。

旧 Stage C 记录读取时默认 legacy/transient，只在各自命名空间审计；新增 E-P schema 必须
明确带 experiment_id/variant，缺失即拒绝作为 E-P 产物，不能静默补 single。
有效身份为 (experiment_id, variant, model, arch, fault, task, seed, condition, replicate)。
pair_key 含 experiment_id/variant；计算 D_f 时显式组成跨 variant block，不覆盖旧 pair。

输出示例：`experiments/ep_persistence_v1/repeat3/outputs/<model>/<arch>/...`，
trace stem 同样含 experiment_id/variant。只靠目录隔离不足以防合并误碰撞，审计 key、
trial_id、trace stem、resume 指纹必须全部识别新维度。
指纹同时冻结目标策略/窗口/budget、任务和数据集 hash、模型参数、代码与评分版本。

旧 classify_log 的 `count == 1` 只对 Stage C/single 有效。
E-P repeat3 接受 0..3：0=not_triggered，1..2=partial_dose，3=full_dose。
真实日志缺失、重复 action_uid 注入、同一次动作计多次、count>3、预算/窗口不一致才是实现错误。
不得用合成 HTTP 500/503 的存在把同 job 的真实 API 429/502/503 或连接错误整体豁免。
不能因未触发/部分剂量自动补跑，否则会按 Agent 行为选择样本。

## 8. 必须落盘的机制证据

每 episode：episode_id、fault_type、variant、配置和解析后的注入单位、target_policy/version、
目标语义身份、t0、结束步、budget、实际次数、结束原因、run_id。
每次实际注入：action_uid、proxy_execution_step、trace_step、parameter_action_index、
exposure_index、目标匹配状态、原参数/损坏参数或被屏蔽元素、扰动前后内容 hash。
每次交给模型的观察：observation_uid、源 action_uid、episode_id、实际包含的故障标记。
日志不能混进模型消息；记录故障被交付，不声称模型心理上识别了故障。
日志中原参数/页面内容须按现有公开数据规则复审后再发布。

E-P rows 保存 run_id、相对 trace 路径、trial_record/agent_response/HAR/official_eval checksum。
离线对账遇到 hash 不符即阻断，不再把另一轮相同目录的评分和 trace 拼接。
评分复用沿用最新护栏，但已有评分只有绑定同一 response/HAR/config/evaluator hash 时才可复用。
重评显式启用并版本化，报告默认只读；HAR 缺失时只可读取已归档评分，不能宣称重新复现。

## 9. 零付费验收与首批门槛

1. single 的首个错误、DOM 删除样本、参数污染和触发位置与 Stage C fixture 一致。
2. repeat3 同目标连续刷新至少两次仍故障；第三次后释放，窗口过期也释放。
3. off-target 不污染、不消耗次数；同 action 重读不计次；目标匹配歧义/缺失有记录。
4. 参数计数/执行计数/trace 计数及初始 goto 的测试覆盖两架构实际调用链。
5. 不同 trial/worker 不共享 episode；控制臂始终注入零次；长 trial 上限仍为 20。
6. 0/partial/full dose 都可正确审计/resume；缺机制日志和次数越界拒绝。
7. variant key 不碰撞；重复运行产物不覆盖；rows 与 artifact/checksum 对账失败必须阻断。
8. 用离线 fixture 验证官方响应 schema、只读评分和合成错误/真实基础设施错误的区分。

48 条首批由用户授权后启动。逐 block 审计证据交付、模型回显、评分路径、产物和账单，
发现实现错误停止本批；正常未触发、绕过故障或失败不按实现错误停止，也不选择性重跑。
若整个机制被实现为全站永久不可用，应判为不符合本设计并修复。
首批完成后报告结果和余下预算，再决定是否运行完整探索矩阵。
