# Electron 实施与验收记录

验证记录日期：2026-10-04、2026-10-06；最新范围修订日期：2026-10-07。此记录区分已执行验证、当前活动验收和用户取消范围；安装包为未签名内部测试制品，不把取消验收记录为通过。

## 2026-10-06范围修订

用户先明确取消8.2（安装升级/卸载深度演练）、8.3（干净系统断网安装专项验收）、8.4（规模化分发签名/维护及升级回退）、9.1（多入口并行完整演练）。原任务ID保留在tasks.md取消说明中，活动任务数由41调整为37，首次修订时完成项仍24；9.4同时移除升级回退演练。随后同日用户进一步取消5.5/9.4中的操作系统强制关机、注销或断电验收条件，普通生命周期要求仍保留。历史执行结果和制品散列保留；已有防护实现不删除，未执行的操作系统关机不被记录为通过。

私有依赖、自包含核心运行、不在线安装、版本/组件许可追溯、原始证据保护、共享锁及所属进程清理继续适用。原生Ubuntu桌面、USB和正式抓包仍须独立验收，WSL仅预验证。未签名或未做干净系统/升级专项验收不再单独阻塞当前有限实验者的本机功能交付；当前能力声明仍只依据实际证据。

## 2026-10-06本机收尾

活动任务37项，2026-10-06修订后完成32项，2026-10-07基础资产审查按收窄范围完成后为33项。此次完成5.3、5.4、5.6、6.3、6.4、6.5；用户取消操作系统关机条件后，已有Windows/WSL各6项生命周期、实际GUI与Windows真实localhost抓包正常停止证据覆盖修订后的5.5/9.4，两项也标为完成。实际整机关机未执行，`operating_system_forced_shutdown_validated=false`，但已不再要求专项验收；32/37没有把取消条件计为测试通过。最终Windows私有bundle为`cbd2a4524aa9b7657351`，桌面版本0.1.1，包含抓包停止信号和退出租约竞态修复；0.1.0阶段`011d11521f1c9d30ff22`的首次安装验证另保留。以下证据补充并更新10月4日基线，不把早期bundle的测试说成最终安装包的完整验收。

| 范围 | 当前结果与材料 |
|---|---|
| 全量源码回归 | Python188项全部通过，Ruff通过；前端14项测试、类型检查及构建通过。Python仅有Starlette/AnyIO依赖弃用警告。 |
| 生命周期 | Windows和WSL私有Python、认证后台和真实模拟worker各6项集成通过；覆盖draining、父/后台/worker故障、子进程清理、恢复不重跑，以及实际等待至少60秒后显式强制终止。创建身份、部分产物保留和正常GUI退出/取消均有实际证据，修订后的5.5/9.4完成。详见[lifecycle-20261006.md](lifecycle-20261006.md)和[linux-20261006.md](linux-20261006.md)。 |
| Windows真实抓包排空 | 真实Electron以无控制台方式启动私有Python和Dumpcap，仅发送自有localhost UDP；最终产品24/24包、正常停止返回0，PCAP可读，所属进程及租约均已释放。详见[capture-loopback-20261006.md](capture-loopback-20261006.md)。不证明正式IoT网络采集。 |
| 实际桌面UI | 12项实际main/preload/React交互通过；覆盖独立事件目标、模板切换、一键启动、日志、正式基本配置失败阻止、初始化重试、renderer/后台恢复、取消退出和安全清理进度。详见[ui-20261006.md](../../../../automation/desktop/verification/ui-20261006.md)。正式配置使用UI手机fixture，没有发送真机事件。 |
| 原文件操作 | 真实shell打开原始测试PCAP，实际Wireshark进程参数指向同一路径；散列及目录内容不变，原位置定位、选择目录、越界拒绝和打开失败提示通过。详见[files-20261006.md](files-20261006.md)。 |
| Windows外部工具 | 固定driver的实际doctor返回0，必需修复0项、可选建议3项；现有SDK实际版本和完整命令见[sdk-20261006.md](sdk-20261006.md)。该现有集合不宣称是最小集合。 |
| 组件与制品 | SBOM按实际安装组件生成，Electron/Chromium通知显式收入resources/licenses/electron；JDK NOTICE聚合保留。材料与最终包内容另见[assets-20261006.md](assets-20261006.md)，1.4按2026-10-07确认的基础资产审查范围完成；未核对的全面分发材料如实留档，不作为本轮完成前置。 |
| Windows实际安装 | 0.1.0首次安装与最终0.1.1正常安装分别记录；最终Guarded-Setup和安装版桌面退出码均0，非管理员令牌运行，自身ASAR/私有运行时完成2/2模拟与文本/图片预览，运行后18080个载荷文件散列一致。详见[package-20261006.md](package-20261006.md)。8.1的原生Ubuntu安装运行仍待验收；资源不写入以正常安装运行及散列核查为据，不追加Windows ACL专项。 |
| Linux最新预验证 | WSL2 Ubuntu24.04.4普通uid1000；0.1.1 bundle `978a2dc7ccf2f33c6fae`，31项定向测试、6项私有broker生命周期通过，源码及实际打包ASAR/资源的WSLg沙箱窗口均2/2模拟和文本/图片预览通过。Deb399686464字节，复制至当前工作区后SHA-256再次一致。详见[linux-20261006.md](linux-20261006.md)。未安装Deb，原生Ubuntu桌面/USB/正式采集不据此完成。 |

## 2026-10-07最终验收范围

用户要求排除有限实验用途下不必要任务：保留基础资产来源、版本、原许可声明与既有通知、实际SBOM及默认包排除核对，1.4据已执行证据完成。全面对外分发条件证明、全部源码缓存/重链接演练移出当前验收，原材料缺项和license_clearance_complete=false等历史元数据保留，不改为全面权利审查通过。SDK按固定已验证准备清单验收，不穷尽最小集合；资源不写入的契约保持，不追加Windows ACL专项。当前33/37，剩余1.3、7.2、8.1、9.3均只剩原生Ubuntu部分。

## 已执行验证

| 范围 | 结果与证据 |
|---|---|
| 源码回归 | Python 173 项测试通过；前端12项事件标签/独立目标测试、TypeScript检查、Vite构建通过。后续定向验证覆盖只读schema拒绝与导入恢复。 |
| Windows真实桌面 | `automation/runs/desktop-smoke-final-state/smoke-result.json`：实际沙箱窗口连接认证后台，私有worker完成2/2模拟事件；XML/图片实际预览、脚本不执行、无桌面桥接、原文件内容不变。 |
| Linux桌面预验证 | 本机WSL2 Ubuntu24.04.4 + WSLg；`/home/administrator/.cache/iot-exp-build/gui-smoke-state/smoke-result.json`记录相同模拟与实际文本/图片预览结果。不能替代原生桌面/USB/抓包。 |
| 可搬迁运行时 | Windows `automation/runs/relocation/result.json` 与WSL缓存`relocation/result.json`：资源临时移到中文/空格路径，移除全局工具PATH后后台、worker、CLI和UiAutomator2 6.9.3仍可用；恢复原目录。最终验证脚本还以同名cwd模块检查-I隔离。 |
| 认证/协议 | 无凭证GET返回403；bootstrap不含令牌；旧令牌、非工作台Origin拒绝；握手核对实际子进程PID、实例、协议和端口，30秒超时关闭所属管道；用途限定IPC拒绝任意URL/方法/穿越。 |
| 进程所有权 | 私有后台正常停止模拟、父进程异常退出请求所属worker清理；身份复用不误杀；强制终止前60秒门槛；worker退出后存活所属capture/Appium仍阻止退出；排除共享ADB。真实集成与单元验证的覆盖边界分别保留。 |
| 单实例 | `automation/runs/single-instance/result.json`：同路径重复启动、另一目录0.0.9版本启动均保持一个后台，原实例安全退出。 |
| 共享锁 | 不同输出根互斥、端口分配、空/部分锁、两个进程并发回收、所有权更替、登记旧目录活锁与不可读锁阻断；数字接口与同一网卡名称归并。旧目录只读，不删未知锁。 |
| 迁移/回退 | WAL一致备份、重复导入去重、用户配置保留、磁盘满事务回滚、重启恢复日志、DDL失败schema回滚及备份保留；拒绝未来schema且原数据库散列不变。PCAP/报告不复制或改写。 |
| 发现根 | 两根同名会话独立身份、外部不可控、根移动保留索引；环境设置显示不可读取原因；拒绝legacy及其子目录，自动发现不进入legacy。 |
| 生产载荷 | CPython3.13.16、Node24.21.0、Temurin运行版本17.0.20.1+1（厂商SemVer17.0.20+101）、Appium3.7.0、driver6.9.3、Electron44.5.1。来源/散列、依赖锁和core wheel摘要见runtime-inputs.lock.json及制品manifest。 |
| 许可材料 | Windows/Linux CPython对应完整发行中许可证与PYTHON.json、Node LICENSE、JDK legal、npm/Python许可保留，补入React/react-dom/scheduler许可与SBOM。Google SDK、Npcap和USB驱动为外部准备。完整分发权利/源码义务审查未计为完成。 |
| 安装器保护（历史局部结果） | Windows Guarded-Setup包装入口编译成功；`automation/runs/gate-test-state/result.json`记录空闲0、活动创建身份2、PID复用0；gate-lease-test验证没有GUI marker时打包CLI租约也阻止替换。Forge自动输出保护入口；Deb包含preinst/prerm。基本安装/普通用户仍属8.1，升级/卸载深度演练已取消，未计为已通过。 |
| PDF | `automation/runs/pdf-preview-state/pdf-result.json`与pdf-preview.png：实际Chromium PDF窗口渲染一页测试PDF，原文件内容不变，无桌面桥接。薄测试入口复用完整main/preload/protocol，不修改应用实现。 |

## Windows真机无抓包闭环

用户授权：当前LG LM_V405，UDID `LMV405UAd6421e56`，米家台灯；手机与IoT实验网隔离且USB共享关闭。设备实测Android10/API29、米家11.8.703；复用SDK PlatformTools36.0.0、BuildTools36.1.0，现有Platforms34/36。此集合不是已证明的跨平台最小集合。

只读inspect通过，预检记录手机地址10.208.124.237、无192.168.1.0/24冲突；目标IP未配置，不把该预检描述成完整正式隔离验收。

首个会话`electron_lg_lamp_minimal_20261004_001`：关灯后的旧元素失效，1条automation_error、1条app_ack_only；完整保留。修正失效元素读取后，用新ID执行`electron_lg_lamp_minimal_20261004_002`：开灯/关灯各1条app_ack_only，App成功率2/2，结构校验通过。数据位于`automation/runs/hardware-desktop-workspace/runs/sessions/`。不是独立设备确认，未生成PCAP/HA证据，固件仍未知；没有验收亮度/情景/专注等真实事件。

用户于本次对话确认“时钟对齐”和manual_review两项，仅用于本次桌面真机闭环交付。时间来源仅同一主机host_system_clock，无跨机偏差/全程漂移测量。此确认记在派生交付记录，原始动作、clock_sync及质量报告不回写。

## 保留未完成的验收

- 原生Ubuntu固定SDK准备清单、driver doctor及手机要求实测（1.3）；Ubuntu设备工具路径/USB权限实测（7.2）。Windows已验证清单保留，不再穷尽最小集合。
- 原生Ubuntu桌面/USB/正式接口短抓包与最小闭环（9.3），包括双向流量、隔离、同会话PCAP/时钟/质量。
- 原生Ubuntu Deb安装、普通用户桌面运行及安装资源/数据分离验收（8.1）；Windows正常安装、非管理员运行和18080载荷散列一致已有证据。WSL预验证不代替原生Ubuntu安装和桌面运行。
- 1.4的基础组件来源/版本/上游声明/既有通知、SBOM与外部工具边界审查已完成；全面对外分发材料与逐库重链接专项已移出本轮，不再列为待完成任务。安装包仍是未签名内部制品。

操作系统强制关机、注销或断电仍未执行，但用户已明确取消5.5/9.4的该专项条件，不再列为未完成任务；普通进程故障、至少60秒门槛、恢复不重跑、UI取消/恢复和Windowslocalhost实际抓包排空是两项剩余范围的完成依据，不能被描述成整机关机测试通过。

活动范围内未通过的整项保持空框，按修订后的计划继续；取消项从活动复选框移除且不计完成。未归档此OpenSpec变更。
