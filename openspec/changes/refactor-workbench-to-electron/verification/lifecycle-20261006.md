# 生命周期核查与受控进程验收

日期：2026-10-06。范围为 5.3–5.6 的本机实现和受控进程测试，不启动手机事件、不采集 IoT 流量、不停止共享 ADB，也不关闭主机或整个 WSL。此次取消的安装、干净机器和多入口额外演练不恢复执行；既有安全保护与测试源码保留。

## 实现与修复

- `draining` 幂等：第一次退出取消本实例排队任务、写入安全停止标记并禁止继续派发；重复父管道 EOF 或重复停止请求不会不断重置状态、停止时间或添加重复停止日志。后台等待已验证的 worker 和所属子进程退出。
- worker 账本增加其后台父 PID 与创建身份。仅 `source=console` 的记录可以恢复清理所有权；原 worker 已死亡，或记录的原后台创建身份已死亡时，才转入本实例清理。父仍活或缺少身份依据的旧 worker 不被抢占。恢复只处理停止与核查，不重新执行实验。
- Appium 在真实 `Popen` 后立即写入账本，早于 HTTP 就绪等待；启动等待期间的 worker 故障也能定位该服务。抓包进程仍在启动时写入账本与租约。
- 抓包安全停止等待超时后保留进程、部分文件和所属租约，不在 15 秒时暗中执行 `terminate`。用户明确请求、首次安全停止已经至少 60 秒且创建身份仍一致时，才允许强制终止。
- Windows 孤儿抓包进程仅在账本明确记录独立控制组时发送 `CTRL_BREAK`；旧账本没有该证明时保留进程并报告，避免向共享控制台发送信号。无法发送信号也不自动改用杀进程。
- 所属进程已死亡后按 `gui:<taskId>`、worker 创建身份和 `lease_id` 精确释放主租约及兼容副本。未知、其他所有者或仍有活子进程的租约不删除。
- `terminate_owned_tree` 同时保护共享 ADB 根和 ADB 后代。强制结束或异常结束仍标记中断，原产物保留待核查。
- Node broker 合并并发停止请求，核对排空健康响应；进程通过信号退出时使用 `signalCode` 与 `exitCode` 一起判断，避免等待已经退出的进程。异常退出保持故障/待恢复含义。
- 清理释放租约同时核对数据库和文件两份所属进程证据，不能在账本已写入活子进程、锁副本尚未更新时删除锁；与当前任务无关的损坏锁保持原样，不使整个调度线程停止。子进程恰好自然结束按幂等成功处理；访问失败转为诊断。
- worker 启动参数携带父创建身份，启动时优先保存原父身份，不把复用该 PID 的新进程重新认作控制器。

## 本次执行

源码定向命令（在 `automation/` 执行）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lifecycle_ownership.py tests/test_desktop_service.py tests/test_console.py --basetemp runs/lifecycle-focused-20261006e
.\.venv\Scripts\python.exe -m ruff check src/iot_exp/scheduler.py src/iot_exp/worker.py src/iot_exp/task_store.py src/iot_exp/resources.py src/iot_exp/process_cleanup.py src/iot_exp/backends/system.py src/iot_exp/backends/capture.py tests/test_lifecycle_ownership.py
node --test desktop/tests/handshake.test.cjs
```

结果：29 项 Python 定向测试通过；Ruff 通过；3 项握手测试通过。Python 测试仅有 Starlette/AnyIO 依赖弃用警告。测试含真实自有子进程：旧 worker 单独死亡而 Appium 替身存活，恢复核对后清理该替身并释放两份所属租约；父身份存活时不接管，父创建身份失效时只请求清理；抓包替身的停止超时不会暗中杀死进程，合成部分文件字节保持不变。

真实 broker 首轮使用 Windows 私有 bundle `6ede68c43f4cdfac0202`。修复后最终复验使用 **`270166530697ecb1c556`**，直接启动制品解释器、认证 sidecar 和真实模拟 worker。首轮日志 `automation/runs/lifecycle-20261006-broker.log` 保留；最终日志 `automation/runs/lifecycle-20261006-broker-final.log`、结构化结果 `automation/runs/lifecycle-20261006-broker-final/result.json` 和各场景工作区均保留。

```powershell
$env:IOT_EXP_INTEGRATION = '1'
$env:IOT_EXP_SMOKE_ROOT = Join-Path (Get-Location).Path 'runs\lifecycle-20261006-broker-final'
node --test --test-name-pattern='private sidecar|parent crash|sidecar crash|worker crash|dead sidecar|owned residual' desktop/tests/backend.integration.test.cjs
```

首轮结果为 **5 通过、1 失败**：真实等待超过 60 秒后，强制终止接口仍按 `updated_at_ns` 判断，因此返回 409；调度器使用的首次 `stop_requested_at_ns` 没有被重置。统一 HTTP/调度/UI 的首次停止时间并重冻私有 bundle 后，完整复验 **6 通过、0 失败**，总耗时 172.024 秒。实际等待使用真实时钟与活子进程，没有人为修改停止时间绕过门槛。测试包含：

1. 认证与正常排空：运行中的模拟被安全取消，`draining` 后提交返回 409，并发两次停止完成同一次清理。
2. broker 父进程退出：管道/父身份失效触发 sidecar 与 worker 安全清理，数据库保留一个原任务。
3. sidecar 单独故障：仍活的真实模拟 worker 通过父身份监控取消；重开保留原 ID，不重提任务。
4. worker 故障：所属 Appium 替身从账本清理，合成部分证据保持原字节。
5. sidecar 与 worker 同时结束：仍活的所属子进程由新后台恢复核对，完成清理后显示中断，不自动重跑。
6. 真实 60 秒门槛：保留一个自有、未证明独立控制组的抓包替身；前 60 秒强制请求被拒绝，实际等待超过 60 秒后再显式终止；重开仍是一条中断任务，合成部分文件不变。

## 可复现的用户行为与预期

- 正常退出：有任务时选择安全停止，等待退出界面显示的所属进程清理。排空开始后不能创建新任务。取消退出应保留当前后台与任务；界面验收由桌面 UI 报告提供。
- 后台故障：界面显示后台故障并禁用新提交；重试核对旧 worker、子进程和租约，期间状态为恢复。没有完整结束证据的记录显示中断或待核查，不重新执行事件。
- 停止迟迟不结束：等待至少 60 秒后选择强制终止。只处理当前实例或已核实恢复的所属创建身份；捕获的部分文件与原动作记录保留。
- 界面故障：重建 renderer 并读取当前任务状态；后台不因为界面重建而停止，也不重复提交。实际 Electron renderer/main 的注入与窗口截图由本次 UI 验收报告单独记录。

## 证据边界与未验收项

本报告中的 Appium/抓包替身是测试自己创建的 Python 子进程；名字或账本角色不使其成为真实 Appium、Dumpcap 或真实 PCAP。受控终止进程也不等于真实操作系统强制关机、注销或断电。

原生 Ubuntu 的 USB、异网隔离、正式接口双向流量、实际 Dumpcap 排空与最小闭环仍待独立验收。Windows 无控制台环境的真实 Dumpcap 安全信号与写盘行为不能由本报告的替身测试推定；随后已用真实工具完成仅限自有localhost UDP的复验，见[capture-loopback-20261006.md](capture-loopback-20261006.md)。该记录与本报告的draining、worker、报告生成、子进程及租约核查共同覆盖5.3的本机生命周期；正式Ubuntu采集仍由9.3保留。真实强制关机/注销后的完整演练未执行；最初包含此条件时5.5/9.4保持未完成，后续用户范围修订见文末。失败与复验日志保持原样。

可选 WSL/Linux 预验证只尝试发行版枚举；`wsl.exe --list --quiet` 返回 `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`（退出码 4294967295）。诊断保存在 `automation/runs/lifecycle-linux-20261006/wsl-access-diagnostic.json`。没有执行 Linux 解释器、创建 Linux 替身或发送 Linux SIGINT，因此本报告没有新增 Linux 信号/写盘实测结论。

## 0.1.1最终跨平台复验

上述WSL默认沙箱访问失败记录保留。随后在用户已经授权的WSL开发范围内，以提升工具访问权限进入Ubuntu，执行了Linux预验证，结果另见[linux-20261006.md](linux-20261006.md)；这里不把工具访问权限等同于应用或Linux用户提权，也不由WSL推定原生Ubuntu正式采集通过。

Linux定向回归发现一个真实退出竞态：子进程在reconcile核对后、最终cleanup分支前退出，该分支直接标为interrupted而未释放已经全死的所属租约。最小共用修复是在最终确认分支先调用`release_dead_task_leases`，再标中断；该方法仍逐项检查主进程/子进程创建身份及两份账本，活进程和未知锁保留。新增稳定的竞态回归，另修正测试夹具的Linux信号处理及清理方法。Windows和Linux定向31项均通过；Windows最终全量188项通过、Ruff通过。

Windows最终私有bundle为`cbd2a4524aa9b7657351`，桌面工程0.1.1。按本报告相同的六场景命令，以`IOT_EXP_SMOKE_ROOT=automation/runs/lifecycle-20261006-v011`执行，Node结果6通过、0失败、总321.236秒；目录下分别保留drain、sidecar-crash、worker-crash、orphan-recovery、force-sixty-seconds、parent-crash的隔离状态/数据库/产物。汇总为该根下`result.json`，六项结果来自实际Node测试输出。强制停止场景仍使用真实时钟与存活自有进程等待至少60秒；没有回写停止时间。

最终Windows真实无控制台Dumpcap回环复验也已通过，见抓包报告0.1.1补充。此修复与复验不改变操作系统强制关机未执行的事实。

## 用户取消操作系统关机验收后的任务判定

2026-10-06用户明确无需继续5.5/9.4的操作系统强制关机问题，故取消强制关机、注销或断电专项演练。至少60秒安全停止后显式force、PID创建身份核对、所属残留进程处理、部分证据保留和恢复不自动重跑继续保留。最终Windows与WSL各6项真实生命周期、GUI取消/恢复/正常退出及Windows真实localhost Dumpcap排空已有证据覆盖修订后范围，任务清单据此将5.5/9.4标完成。操作系统强制关机实际验证仍为false、验收要求为false；不将受控进程终止称为整机关机验收，也不改早期失败日志或相关任务状态历史。
