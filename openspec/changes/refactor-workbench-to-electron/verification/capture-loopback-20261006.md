# Windows 无控制台 Loopback 抓包排空诊断

日期：2026-10-06。限时两轮诊断，不属于多入口并行演练。仅使用 `\Device\NPF_Loopback`，由测试自己的 sender/receiver 在 `127.0.0.1` 发送 UDP；BPF 同时限定 IPv4、UDP、两个地址和两个自有端口。没有操作手机、抓取 IoT 或任何实验网流量、停止共享 ADB、关闭主机/WSL、广播控制事件、关闭 Electron sandbox 或扩大 ACL。

薄入口为 `automation/desktop/scripts/verify_capture_loopback.cjs` 和 `verify_capture_loopback.py`。真实 Electron GUI 主进程使用与 broker 相同的 `windowsHide:true` 和管道 stdio 启动 bundle `270166530697ecb1c556` 中的私有 CPython，直接复用 `DumpcapCaptureBackend` 的启动/停止、process callback、进程创建身份和 `ResourceLease.track_processes/release`。`GetConsoleWindow()` 两轮均为 null，证明 Python 子进程没有控制台。

实际工具：`C:\Program Files\Wireshark\dumpcap.exe`；接口只读枚举的第 8 项为 `\Device\NPF_Loopback (Adapter for loopback traffic capture)`。PCAP 的读取核查使用同目录 `tshark.exe -n -r`，核对每个包的源/目的地址、源/目的端口和专属 UUID payload。

## 第一轮：原产品路径失败

首轮原保存于`automation/runs/capture-loopback-20261006/`；该合成测试目录后来被旧入口覆盖，不能再作为首轮原始机器输出引用。首轮原因和下列数值来自当时的工具诊断，覆盖经过见文末订正。

- 自有 UDP：57324 → 57323；发送、接收均 24/24。
- PCAP 3208 字节，可读取 24 个包；所有包均为 `127.0.0.1` 和上述端口，payload 均属于本次 UUID。
- `capture.stop()` 没有进入 OS 安全信号路径，直接因 `AttributeError("module 'subprocess' has no attribute 'CTRL_BREAK_EVENT'")` 失败。
- 产品没有暗中把终止当作安全成功。测试只清理自己创建且 PID/token 一致的 Dumpcap PID 28384，清理返回码 15；`capture_complete=false`。
- 文件保留；最终无残留 capture PID 和主/兼容租约。

## 第二轮：仅测试进程纠正常量后正常排空

证据目录：`automation/runs/capture-loopback-20261006-signal-diagnostic/`，同样保留结果、真实 Electron 子进程信息、账本、逐包字段和 PCAP。

这一轮未修改产品或磁盘中的私有 bundle，仅在测试进程中将 `subprocess.CTRL_BREAK_EVENT` 别名指向 `signal.CTRL_BREAK_EVENT`，以诊断实际无控制台信号路径。结果明确带有 `signal_constant_diagnostic=true`。

- 自有 UDP：58960 → 58959；发送、接收均 24/24。
- Python PID 42412，无控制台；所属 Dumpcap PID 5076。
- 正常 `capture.stop()` 返回码 0，无 error；停止约 0.0051 秒。
- 无强制清理：`forced_test_cleanup=false`；`capture_complete=true`。
- PCAP 3316 字节，TShark 返回码 0，读取 24 个包；全部包属于地址、端口与 UUID 的严格名单。
- 无残留 capture PID，主租约和兼容租约均释放。

## 最小修复与验收边界

产品 `backends/capture.py` 应使用已导入的 **`signal.CTRL_BREAK_EVENT`**，并增加真实正常停止路径的常量回归测试。该替换是本次诊断确定的最小修复；无需引入 Console helper、AttachConsole、广播信号或修改系统权限。

原产品未修复前，本报告不能把正常退出标记通过。第二轮证明的是“纠正常量后的真实 Loopback 排空路径”，不是不带诊断别名的最终制品验收。由主任务实施这一行修复、更新 bundle，再用原入口不带 `--signal-constant-diagnostic` 完成产品复验。

## 产品修复后复验

主任务已将产品改为`signal.CTRL_BREAK_EVENT`，并加入Windows正常停止常量回归测试。最终私有bundle为`011d11521f1c9d30ff22`。不带诊断参数的最终复验结果在`automation/runs/capture-loopback-20261006-final-unique/result.json`：Python PID42284无控制台，所属Dumpcap PID25236正常停止返回0、约5.257ms，61275→61274两个自有UDP端口发送/接收24/24；PCAP3316字节，TShark读取24包，所有包均属于本次UUID与本机回环地址。`capture_complete=true`，无强制清理、无剩余PID/锁。此前首次最终复验的副本另存于`automation/runs/capture-loopback-20261006-final/`，其路径字段仍指向原测试目录。

记录订正：旧薄入口的输出目录固定，首次最终复验覆盖了先前原产品的合成失败目录；原失败原因和数值仍保存在本报告及对话工具诊断，但不再把该目录称为未覆盖的原始机器输出。主任务立即增加IOT_EXP_LOOPBACK_TEST_ROOT和已存在结果的拒绝覆盖保护；随后用新的final-unique目录再次验证。上述影响只涉及隔离的合成回环测试，不涉及真实实验或10月4日手机会话。

本报告没有证明原生 Ubuntu、目标 IoT 网卡、正式 BPF、真实实验网隔离或完整正式会话；这些仍按对应硬件任务独立验收。测试清理第一轮自有 PID 的授权不等于产品提前绕过 60 秒强制停止门槛。

## 0.1.1最终制品

共用租约退出竞态修复后，bundle刷新为`cbd2a4524aa9b7657351`（桌面0.1.1），再次执行同一无诊断别名入口，结果见`automation/runs/capture-loopback-20261006-v011/`：Electron和Python退出码均0；Python PID51984无控制台，Dumpcap PID51168正常停止返回0、约15.317ms。53687→53686自有localhost UDP发送/接收24/24，PCAP3316字节、24包均属于本次UUID；没有强制清理、残留PID或租约。新结果使用独立目录，未覆盖此前记录。
