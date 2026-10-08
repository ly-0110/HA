# 实施前基线

日期：2026-10-03。源码HEAD：`2318fd0323e9c57e2876464bbe3ec2b33094329f`。
当前工作树包括本会话上一阶段的前端、启动器和外部发现改进；HEAD不是这些未提交修改的完整快照，文件散列另见baseline.json。

## 已执行验证

| 检查 | 实际结果 |
|---|---|
| 完整Python测试 | 138 passed，1个第三方DeprecationWarning；JUnit保存在automation/runs/electron-baseline/python-tests.xml |
| Ruff src/tests/console_bootstrap.py | 通过 |
| npm run web:check | 通过 |
| npm run web:test | 12 passed；Node模块格式警告不影响结果 |
| npm run web:build | 通过；Vite 6.4.3，已构建前端资产 |
| 原CLI无硬件干运行 | electron_baseline_20261003，2个事件，禁用抓包 |
| validate-session | ok=true，session_completed=true，planned_events_complete=true，无开放事件 |

本机环境为Windows11 x64（10.0.26100）、Python3.13.2、Node24.20.0、npm11.19.0。
当前锁文件的Appium为3.7.0、UiAutomator2为6.9.3。

已只读确认本机WSL2发行版为Ubuntu24.04.4。WSL不作为原生桌面、物理USB或正式抓包验收替代。
此次基线没有连接真实手机、启动真实事件或生成PCAP。

## 回归边界

后续实施必须保留现有CLI参数、实验YAML解析、参数化目标身份、正式预检和会话产物读取。
事件结果仍区分App页面回执与独立确认；厂商App触发和HA被动观测的边界保持不变。
