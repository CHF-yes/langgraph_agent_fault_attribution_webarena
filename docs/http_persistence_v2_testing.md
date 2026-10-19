# HTTP 持续性 v2 测试说明

基于首批 48 条完成后的 `f64616c` 开发。本版是待真实验收的独立测试协议
`ep-http-persistence-v2`，不是已冻结的正式批次，不与 Stage C 或 E-P v1 合并分析。
本次开发没有执行模型调用，也没有修改旧实验产物或 v1 冻结清单。

## 协议

- 首次加载任务起始页保持干净；进入 Agent 执行前显式激活。
- 首次合格导航返回非空 HTTP(S) 页面观察时触发。显式 `goto`（包括刷新）合格；
  `click`、`go_back`、`go_forward`、`type_text`、`select_option` 仅在资源 URL 改变时合格。
  滚动、悬停、被动观察及单独改变 fragment 不激活首次故障。
- 默认锁定该次导航的 URL。可用 `--fault-target-url` 事前指定唯一资源过滤条件；
  其他页面不会触发首次注入。匹配只去掉 fragment，保留 path/query/端口，不扩大到站点。
- `single`：第一次观察替换后，下一动作恢复正常。
- `persistent`：从首次实际注入开始，在预设动作窗口内持续替换该资源的观察，
  没有三次剂量上限。测试入口默认窗口 10 个 proxy 执行动作，含首次；
  `[t0,t0+9]` 有效，`t0+10` 起释放。其他 URL 可正常访问，绝不追随 Agent 换目标。
- 同一动作重新读取观察时仍保持错误，不另计一次注入；只按新的动作计次数。
- 两个变体使用相同触发规则和 500/503 抽样，只有持续策略不同。独立远端运行
  不保证首次目标或前缀轨迹相同；本版未实现共享检查点分叉。
- 控制臂不注入，保留相同协议元数据。合法未触发保留；重复次数仅作机制描述，
  不使用 `partial_dose/full_dose` 宣称必须凑够剂量。

这里仍是**观察层的 HTTP 错误模拟**：真实导航已执行，HAR 可能是 200，真实 DOM
和动作副作用没有撤销。错误观察保留资源 URL，清空交互元素，不提供虚构的 Retry 按钮。
因此它只能评估此观察协议，不应称为真实网络断连或服务端宕机。目标来自实际访问事件，
并不自动证明该资源对任务必不可少。DOM、参数错误及真实网络故障没有在本轮扩展。

## 离线测试

在已安装项目测试依赖的 Python 环境、仓库根目录运行：

```bash
python -m pytest tests/test_http_persistence_v2.py tests/test_ep_acceptance.py -q
```

新测试的模型、浏览器和网络均替换为桩；临时数据集也由测试自己生成，不需要真实密钥。
覆盖事件触发、长前缀、窗口边界、重复观察、换路、控制臂、重置、非法配置、
机制日志校验、两架构 CLI 四臂产物与评分绑定、计划模式及防覆盖。

## 一个真实四臂块

使用你原有可运行 WebArena、模型配置及 `WEBARENA_DATASET` 的环境。先只打印计划：

```bash
python scripts/http_persistence_probe.py \
  --experiment-root experiments/ep_http_persistence_v2/debug_task24_react \
  --task-id 24 --architecture react --model-profile deepseek_v41_flash \
  --max-steps 20 --fault-window-steps 10 --fault-seed 1
```

计划含 `single/control`、`single/fault`、`persistent/control`、`persistent/fault`
四条，生成命令不带固定注入步。默认不写文件、不启动子进程、不调用模型。
确定要测试时，在同一命令末尾加 `--execute`。这会执行 4 条真实 trial，产生成本。
测试 `plan_execute` 时改架构及输出目录，避免复用已有目录。
如需指定资源，在两种模式的命令中均增加 `--fault-target-url '完整资源URL'`。

运行器拒绝覆盖已有 logs/status/trial 产物，不支持 resume；中断后先保留已有数据，
另选新目录。它明确标记 `development_probe_unfrozen`，没有绕过或重写 v1 冻结规则。
正式实验需要另行验收、冻结和设计调度，不能把这个开发入口直接当作新批次授权。

## 查什么

每条 trial 除原有 response、HAR、trace、trial record 外，新增 `fault_evidence.json`：

- `snapshot`：锁定目标、首次注入步、窗口末步、注入次数、释放原因；
- `events`：注入、跳过、窗口释放事件；
- `exposures`：每次返回观察的真实 URL、锁定 URL、是否匹配、是否包含错误及原因。

证据文件的 SHA-256 写入 trial record 的 run_config。测试入口在每条结束后检查
产物完整性、实验身份、协议参数、证据哈希与控制污染；不通过立即停止后续 trial。

应确认初始观察无故障、两个控制臂注入数为零；刷新/重复读取不能绕过 active 故障；
换 URL 保持干净，回到原 URL 在窗口内仍受影响；窗口到期恢复正常。
若全程没有合格导航，应出现 `not_triggered`，不能把它视为成功恢复。
只有一次接触也不自动说明协议失败，需要结合换路、最终目标和成本解释。

真实官方评分沿用原流程，对两个变体分别运行，避免把同名格子混在一个 Stage C 审计中：

```bash
python scripts/stage_c_pipeline.py evaluate \
  --root experiments/ep_http_persistence_v2/debug_task24_react/single \
  --model-profile deepseek_v41_flash --architecture react \
  --fault-types web_http_error --task-ids 24 --seeds 1

python scripts/stage_c_pipeline.py evaluate \
  --root experiments/ep_http_persistence_v2/debug_task24_react/persistent \
  --model-profile deepseek_v41_flash --architecture react \
  --fault-types web_http_error --task-ids 24 --seeds 1
```

若你的评分环境需要显式配置，沿用原批次的 `--config` 和数据集设置。
旧 `ep_analysis.py` 只支持 v1 的 single/repeat3，本版没有用它生成持续性统计结论。
