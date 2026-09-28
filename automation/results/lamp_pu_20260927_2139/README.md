# 小米台灯 1S 四类事件 P/U 采集

采集时间：2026-09-27 21:37–23:35（Asia/Shanghai）。共 480 次目标操作、88 次穿插的其他事件操作，
四组各有 142 条原始操作记录。操作完成后随机等待 5–10 秒；无成功率门槛，不做时钟同步。

| 组 | 目标安排 | 目标操作 | U 来源操作 | PCAP 网络记录 |
|---|---|---:|---:|---:|
| [brightness](brightness/actions.jsonl) | 30% ↔ 80%，60 轮 | 120 | 22 | [2,500](brightness/traffic.pcapng) |
| [color_temperature](color_temperature/actions.jsonl) | 3200K ↔ 4600K，60 轮 | 120 | 22 | [2,407](color_temperature/traffic.pcapng) |
| [focus_mode](focus_mode/actions.jsonl) | 开 ↔ 关，60 轮 | 120 | 22 | [1,454](focus_mode/traffic.pcapng) |
| [scene](scene/actions.jsonl) | 电脑、温馨、休闲、办公、阅读、娱乐，各 20 次 | 120 | 22 | [1,406](scene/traffic.pcapng) |

每 10 次目标操作之间插入关灯→开灯，一组有 11 对、22 次 U 来源操作。`role=target/u_source`
只记录操作安排，原始 PCAP 未作包级标签。Wireshark 每行是一条网络包记录，7,767 条包记录不等于
事件次数，也不等于已经切分的 PU 学习实例。所有 88 个 U 操作窗口均有目标设备双向 IPv4 流量。

## 文件入口

- `delivery_summary.json`：操作数量、唯一 ID 和本机页面证据完整性汇总。
- `campaign.json`、`configuration.yaml`、`provenance.json`：运行配置、最终进度、用户确定的采集口径。
- 每组 `capture_plan.json`、`actions.jsonl`、`run_journal.jsonl`：操作计划和完整原始日志。
- 每组 `traffic.pcapng`、`acquisition_report.json`、`capture_inventory.json`：连续原始抓包及采集统计。
- `capture_lamp_pu_source.py`：本次实际执行的采集源码快照；与 `provenance.json` 中的哈希一致。
- `archive_manifest.json`：发布文件哈希与原采集目录映射。

`dispatched=true` 表示 UI 控制动作已发送，不代表独立确认设备状态变化。HA 日志由外部电脑接收，
本归档未导入 HA 日志。固定采集协议与常规 `ExperimentRunner` 会话契约不同，不使用
`validate-session` 声称通过其验收。

迁移保留原始内容，因此日志中的旧路径仍为 `runs/campaigns/lamp_pu_20260927_2139/...`；
实际文件现位于本目录。每组 `screenshots/` 和根目录 `appium.log` 保留在采集机本地。
`delivery_summary.json` 的页面证据完整性结论基于完整本机归档，Git 中只发布下述清单文件。
