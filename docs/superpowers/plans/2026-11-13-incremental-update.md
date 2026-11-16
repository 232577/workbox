# 文件级增量更新 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement each isolated task; preserve file ownership.

**Goal:** 下载并安全应用相对当前构建的程序文件差异，不能使用增量时保留完整包升级。

**Architecture:** 受信任 Release 资产描述承载固定 .wbdelta 容器。公共标准库校验器由客户端、构建器和独立冻结助手共用；替换助手在主程序安全停机后运行，使用 staging、受管文件备份及持久日志恢复。

**Tech Stack:** Python 3.11 / PySide6 / PyInstaller / 现有 Inno Setup 与 GitHub/Cloudflare 发布链。

**Spec:** `docs/INCREMENTAL-UPDATES.md`（协议、函数名、字节限制与责任边界）。用户已经同意该方案并明确要求现在实现、与 B 协作。

## Global Constraints

- 只在 current 的独立工作树开发；不动根私有历史和真实数据。
- 正式安装与公开 release 不属于本轮；旧发布资产不可变。
- 用户主动检查更新；无新增账户、监控或常驻服务。
- 来源仅既有 HTTPS 白名单，不绕信任或证书提示。
- 业务 ZIP 路径不变；本协议仅处理软件更新容器。

## Review Focus

- 损坏、缺失或人为改变的程序基线不能局部混装。
- 大小写/ADS/链接/目录身份替换不能越过程序路径边界。
- 取消不触发完整包下载，下载失败不能碰现有程序。
- 任一替换点异常、进程退出或探测失败不能丢失可恢复旧文件。
- 正式 data-dir 与匿名哨兵在升级、失败和恢复后保持一致。

## Tasks

1. **公共包格式**：A 子代理，`delta_format.py`、`tests/test_delta_format.py`。先验证格式缺失失败，再实现规范、校验和 staging；覆盖所有边界。
2. **增量生成及发布描述**：由当前 Concord 认领方负责，`scripts/build_incremental_update.py`、构建/安装脚本、网站可选资产及专用测试。按公共接口写匿名红测，实现确定性包并自验。交接使用精确 SHA 和 CI，未认领的工作按实时协作状态分配，不并行覆盖。
3. **下载选择与兜底**：A 子代理，`update_client.py`、`incremental_update.py`、`tests/test_incremental_update.py`。先验证现有完整流程兼容，再做增量/全量/取消分支。
4. **更新界面及停机契约**：A 子代理，`update_ui.py`、`desktop_controller.py`、`tests/test_incremental_ui.py`。复用现有单次后台作业和数据库恢复点，清楚显示增量及兜底结果。
5. **替换、恢复与助手**：A 根代理，`incremental_apply.py`、`update_helper_main.py`、`update_install.py`、`main.py` 及专用回归。先失败注入、恢复冲突与数据哨兵测试，再实现逐项替换及独立进程入口。
6. **集成验证**：核对 B 精确提交与格式兼容，独立审查路径/回滚边界，运行完整 CI 和隔离 Windows 助手进程验证；记录真实通过、失败、跳过及未安装发布的边界。

每个任务均按「红测 → 最小实现 → 相关回归 → 审查 → 独立提交」进行；整合后才执行完整回归。任何失败不降断言、不伪造验收。
