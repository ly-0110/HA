# 正式实验结果

每个实验编号对应本目录下一个归档，原始操作日志和抓包放在同一实验中。

| 实验 | 内容 | 仓库发布范围 |
|---|---|---|
| [lamp_pu_20260927_2139](lamp_pu_20260927_2139/README.md) | 台灯亮度、色温、专注模式、六种情景；四组连续抓包 | 四组原始操作日志、PCAP、计划、报告、配置和采集源码 |
| [speaker_formal_retries_20260923](speaker_formal_retries_20260923/full_chain_review.md) | 已发布的音箱播放/暂停实验 | 保持原有 12 个证据文件，增加迁移清单 |
| speaker_formal_20260923 | 音箱正式采集 | 原有完整目录保留在采集机本地 |
| speaker_formal_fast_20260923 | 音箱正式采集 | 原有完整目录保留在采集机本地 |
| speaker_formal_full_20260923 | 音箱正式采集，包括未完成记录 | 原有完整目录保留在采集机本地 |

每个已发布实验的 `archive_manifest.json` 提供发布文件的 SHA-256、大小及原目录到归档目录的映射。
[organization.json](organization.json) 记录本次迁移及删除的开发产物。迁移未改写原始日志、配置、
报告或 PCAP；其中历史绝对路径/相对路径仍指向原采集位置，按迁移清单映射读取。

截图、页面 XML、Appium 日志及其他本机证据留在对应实验目录，未纳入本次发布。
开发临时产物在 `automation/runs/`，不与正式归档混放；测试源码在 `automation/tests/`。
