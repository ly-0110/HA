# Electron 实验工作台

当前实现复用React和Python实验核心，桌面窗口通过受限preload访问认证本机后台。Python/Node/JDK/Appium由私有制品提供；SDK、Dumpcap和系统权限通过“环境设置”准备。

## 开发与构建

从automation目录执行：

```text
npm run web:build
npm ci --ignore-scripts --prefix desktop
npm ci --prefix desktop/runtime-appium
python desktop/scripts/prepare_windows_build.py
python desktop/scripts/build_runtime.py --uv <构建机uv完整路径>
python desktop/scripts/build_notices.py
python desktop/scripts/collect_python_licenses.py
npm run start --prefix desktop
```

运行时输入固定在runtime-inputs.lock.json。刷新输入必须显式执行lock_runtime_inputs.py并重新验证，不能在终端启动时自动升级版本。维护者需要构建工具，终端实验者不需要uv/npm或系统Python。

Windows构建结果位于desktop/build-resources；Linux原生构建应在Linux文件系统中进行，保留大小写、权限和链接。已授权的WSL预验证可执行：

```text
python3 desktop/scripts/prepare_linux_build.py
```

该命令将构建工具、私有运行时和缓存放在当前Linux用户的.cache/iot-exp-build中，不修改系统PATH。WSL结果必须标记WSL，不能用它替代原生Ubuntu桌面/USB/正式抓包验收。

Linux构建完成后，准备目标平台Electron发行，再用相同资源根生成许可和SBOM；运行build_notices.py时可通过--electron-dist显式指向同版本Linux Electron发行目录（例如Linux缓存desktop/node_modules/electron/dist）。prepare_linux_desktop.py在Linux文件系统复制桌面工程并做WSLg预验证。Linux CPython主许可证来自对应发行的licenses/python-upstream/python/licenses/LICENSE.cpython.txt。make必须从Linux缓存工程目录运行，不能从Windows源码目录直接制Deb。

Windows的make命令显式准备固定maker自带的7-Zip文件，不依赖npm安装脚本自动执行。Forge postMake将Squirrel载荷包装为Guarded-Setup.exe，普通安装使用此入口；原始Setup.exe仅为构建内部载荷。安装保护检查应用进程与创建身份，拒绝在工作台活动时替换。该既有保护保留；用户已于2026-10-06取消升级/卸载全入口深度演练，不把未验收的“应用和功能”卸载路径声明为已通过。

已有签名配置通过WINDOWS_CERTIFICATE_FILE、WINDOWS_CERTIFICATE_PASSWORD和WINDOWS_TIMESTAMP_SERVER从构建环境读取，不写入仓库。本轮交付面向有限实验者，不要求准备签名证书、规模化分发或长期维护验收。当前生成未签名内部测试制品；不推定SmartScreen或企业部署策略已通过。

## 使用与工作区

默认工作区为用户目录下IoTExperiments/default。安装资源只读，用户配置、SQLite和产物分离；“环境设置”可选择工作区、SDK、Dumpcap，登记历史目录或导入旧automation配置/任务状态。

Windows内部使用安装入口为`out/make/squirrel.windows/x64/IoTExperimentWorkbench-Guarded-Setup.exe`，安装后可使用桌面`IoTExperimentWorkbench`快捷方式。当前0.1.1已在本机以非管理员令牌完成安装与实际运行验证；启动无需准备uv、npm或系统Python。真机和抓包所需SDK/Dumpcap从环境设置配置，模拟和查看无需这些可选工具。Linux0.1.1 Deb已在WSL构建并通过实际打包可执行文件预验证，原生Ubuntu安装/桌面/USB/正式采集仍待独立验收。具体制品与验证记录见[交付记录](../../openspec/changes/refactor-workbench-to-electron/verification/implementation.md)。

导入使用SQLite一致备份，包括WAL。用户已经修改的配置保留；历史runs/results只登记读取，不自动复制PCAP。legacy不属于此工作区。迁移和切换前须完成当前任务清理。

迁移先生成state/backups/import-*.sqlite3一致备份，再暂存新增配置；SQLite事务中导入只读任务记录，记录已提交收据。失败/重启按state/import-journals核对：仅删除仍与暂存文件具有同一文件身份的未提交配置，保留用户之后替换的文件。磁盘满时日志保留，修复空间后重启先完成恢复，不能先派发新任务。同一原工作区反复导入不会重复插入任务。

回退时先安全退出并确认旧worker及锁已释放。将备份复制到独立恢复目录，用兼容版本打开；不要让旧程序直接覆盖新版数据库。源automation目录与PCAP不被迁移修改，可继续作为只读发现根。TaskStore升级也会保留*.before-v2-*.bak，并在DDL失败时回滚整个schema事务。

安装后的resources目录提供iot-exp.cmd或iot-exp.sh，使用同一私有解释器和核心CLI。
可显式指定--workspace、--app-state、--lock-root；源码CLI原有参数继续有效。

源码方式的独立工作区示例（工作区不能放在只读资源根内部）：

```text
python -m iot_exp.cli --workspace ../.iot-exp-workspace --resources . run --dry-run --repetitions 1
```

## 共享设备锁

GUI、CLI和固定采集脚本使用同OS用户的共享锁根，独立于output_root。Windows为LOCALAPPDATA/IoTExperimentWorkbench/locks，Linux为XDG_STATE_HOME或~/.local/state下iot-exp/locks。IOT_EXP_LOCK_ROOT可显式配置。

新版同时检查/维护本次输出目录的旧格式镜像锁；已知旧进程仍活跃时拒绝抢占。部分或无法解析的锁保留并报告诊断，不能手工删除后盲目运行。未升级且使用未登记输出根的旧程序不具备新协议保证；跨OS用户需由管理员配置共同锁空间后另行验收。

## 生命周期和恢复

正常退出先进入draining：拒绝新任务、取消排队任务、请求worker安全停止，等待抓包、报告、专属Appium和锁清理。至少60秒后才允许用户选择强制终止；其范围按PID创建身份和所属进程记录核实，并排除共享ADB。

renderer崩溃后重新加载界面，后台继续；主进程退出后私有管道/父身份监控请求清理；后台故障时旧worker可能仍在清理，新后台不自动重跑。进程异常结束后的部分产物保留为中断或待核查状态，不声明已完整完成。Windows/WSL各6项生命周期、实际窗口和Windowslocalhost Dumpcap正常停止已有验证；操作系统强制关机、注销或断电未实测，用户已明确取消专项验收，不将普通进程故障测试说成整机关机通过。

首次初始化显示准备状态；后台启动失败或退出后显示具体错误和“重试连接”。重试先等待当前所属后台退出，再核对旧worker；不会重新提交原实验。安全停止过程中可以取消退出或等待清理；60秒后的返回工作台会在清理完成后重新连接。界面、后台协议和各故障类型的完整验收仍以verification记录为准。

## 验证命令

```text
python -m pytest --basetemp runs/pytest-desktop
npm run web:check
npm run web:test
npm test --prefix desktop
node desktop/scripts/smoke_backend.cjs
python desktop/scripts/verify_relocation.py
```

IOT_EXP_INTEGRATION=1启用desktop/tests/backend.integration.test.cjs中的真实私有后台与父进程异常清理测试。隐藏Electron集成测试用electron desktop --smoke-test；结果写到所选测试appState的smoke-result.json，验证界面实际连上后台且模拟事件完成。

测试目录可通过IOT_EXP_DESKTOP_STATE、IOT_EXP_DESKTOP_WORKSPACE、IOT_EXP_DESKTOP_RESOURCES和IOT_EXP_SMOKE_ROOT隔离。这些变量用于维护者/测试，正常安装自动解析路径，不在URL、文件、日志或argv中保存后台认证凭证。

私有Python使用-I -B -X utf8，忽略当前目录和用户site包并避免向安装资源写pycache。源码模式保留原有PYTHONPATH行为。verify_relocation.py只临时移动指定公开构建资源到同级中文/空格目录，finally恢复；测试时请先退出使用该制品的桌面/CLI。它验证移除全局工具PATH后后台、worker、CLI与driver仍可用；用户已取消干净系统断网专项验收，该测试不被描述为干净系统已通过。运行时不在线安装、私有依赖自包含和核心离线运行契约继续保留。

## 平台与发布状态

Windows11x64和Ubuntu24.04x64为目标。当前开发/WSL预验证结果见OpenSpec verification目录；原生环境及真实实验项目尚未验收时保持未完成。

打包命令为npm run package / npm run make（在desktop目录）。2026-10-06用户取消安装升级/卸载深度检查、干净系统断网专项验收、规模化分发签名/维护及多入口并行完整演练，这些不再作为当前本机功能完成前置，也未计为通过。已有数据保留和安全实现不删除。组件许可/对应源码材料、基本安装与普通用户运行、工作台/生命周期验收及原生Ubuntu真实捕获要求继续保留；能力声明只依据实际验证。任务状态见OpenSpec tasks.md，历史结果见verification/implementation.md。

2026-10-07进一步按有限实验用途收敛：基础组件来源、版本、上游声明/既有通知、SBOM和外部工具边界审查已完成；全面对外分发材料、逐库源码缓存和重链接演练移出当前验收，已有材料与未核对事实保留。SDK使用已验证固定清单，不做最小集合穷尽试验；安装资源不写入按正常安装运行与散列核查，不追加Windows ACL专项。当前Windows本机部分已完成，剩余为原生Ubuntu依赖/USB权限、Deb安装运行及正式采集，WSL仅为预验证。
