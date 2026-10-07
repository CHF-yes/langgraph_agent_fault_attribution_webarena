# Stage C 故障实验数据包

- 生成时间(UTC): 2026-10-07T08:48:34Z
- 代码版本: 6639e50 (branch stagec-exec-chain)
- 内容: 预注册 768 格网格 (4 组 × 96 × seed 1/2)，全部 native 官方判分
- 单元格(trial record): 768
- 故障注入: 367 个故障臂触发 / 384 个故障 job（未触发 17，多为 agent 未执行被参数化动作）

## 目录
- experiments/stage_c_slice{1,1b}: deepseek_v41_flash × react (seed 1/2)
- experiments/stage_c_slice{2b,2c}: deepseek_v41_flash × plan_execute (seed 1/2，已含提示词对等修复)
- experiments/stage_c_slice{3,3b}: qwen38_flash × react (seed 1/2)
- experiments/stage_c_slice{4,4b}: qwen38_flash × plan_execute (seed 1/2)
- experiments/stage_c_slice2: 修复前 deepseek × plan_execute seed 1（对照保留，已被 2b 取代）
- experiments/stage_c_full: 768 格合并 rows.json + 预注册分析 (analysis.json/md)
- experiments/stage_c_pe_fixcheck: 提示词修复的单格验证
- experiments/HAR_MANIFEST.tsv: HAR 清单
- traces/stage_c_slice*: 每调用的 llm_provenance（含 token 用量、端点回显模型）

## 未纳入
- 768 个 network.har（约 6.6 GiB）未打包（见 experiments/HAR_MANIFEST.tsv）
- 原始页面/AX 快照、.env（密钥）均未纳入

## 复现
```
python3.11 scripts/run_fault_matrix.py --model-profile <M> --architecture <A> --task-ids 21 22 24 25 27 28 30 66 102 118 132 133 134 258 274 308 \
  --fault-types web_http_error agent_param_error web_dom_missing --trials 1 --fault-seed <S> \
  --output-dir experiments/<root>/run --official-output-root experiments/<root>/outputs/<M>/<A>
python3.11 scripts/stage_c_pipeline.py run --root experiments/<root>/outputs --output-dir experiments/<root>/pipeline --config experiments/webarena_local_config.json --model-profile <M> --architecture <A> --fault-types web_http_error agent_param_error web_dom_missing --conditions control fault --seeds <S>
python3.11 scripts/stage_c_analysis.py --rows experiments/stage_c_full/rows.json --output-dir experiments/stage_c_full/analysis
```
