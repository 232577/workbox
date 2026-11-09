# Pending Project Names Export Implementation Plan

**Goal:** 在待开发项目页一键导出全部日常待开发项目名称，每个项目一行，只有一个Excel名称列。

**Architecture:** 复用既有SQLite快照、openpyxl、原生保存对话框及安全发布边界；导出不调用交付/分类/状态修改。与当前main的新工作台布局协同，保留另一端PR #52的窄窗详情行为。

**Tech Stack:** Python、SQLite、PySide6/QFluentWidgets、openpyxl既有锁定版本。

## 约定

- 用户已确认“全部待开发项目导出表格，每行一个项目名”。输出.xlsx，仅一个工作表“待开发项目”，仅A列“项目名称”；不含序号、状态、日期、批次、汇总、其他工作表或隐藏业务字段。
- 使用日常项目：status=待开发，未删除、未归档，以canonical项目去重，保留既有全局项目排序。不受当前搜索、类型、批次或其他附加筛选影响；相同名称但不同有效项目不能按名称悄悄去重。
- 名称作为Excel纯文本写入，保留完整名称；超长或非法控制字符明确失败，不截断。空数据生成只有表头的有效工作簿，反馈0个项目。
- 界面按钮为“导出名单”，无障碍名称“导出全部待开发项目名称”，仅在待开发列表呈现；说明明确范围。使用原有保存/覆盖对话框，取消不生成文件。
- 沿用原有安全目标检查、覆盖时文件身份核对、当前受管目录复核、同目录临时文件与原子发布、取消/失败仅清理自身临时文件。
- 后台执行、阻止重复操作、支持取消、忙碌/关闭/更新安全；不改变用户筛选、选中项、滚动、数据、文件、工作量或交付历史。

## 任务

1. 后端：在delivery_export.py新增collect_pending_project_names(db, *, cancel_event=None, progress=None)及export_pending_projects(db, destination, *, cancel_event=None, progress=None, replace_existing=False, expected_target_identity=None)，返回至少path/project_count。最小提取现有安全保存代码复用，不复制或弱化保护，不改export_delivered_projects既有API/输出。
2. UI：在现有待开发清单适当位置添加按钮，复用choose_workbook_destination(prefix='待开发项目名单')和FileJob；若单独控制器，business_busy不得把外部busy反馈回自身造成死锁，须纳入排队/取消/停机及恢复链。
3. 先写失败回归：状态/归档/删除/canonical、同名项目、空表、恶意公式字符串、只读单快照、完整名称、取消、占用/覆盖竞争、受管目录改变；UI范围不随筛选、重复点击、成功失败取消、浅深660窄窗、关闭/更新。
4. 独立审查，受影响及全套回归、准确当前提交CI。与main显式整合，不覆盖其他分支。
5. 从已审main构建新日期/序号正式包，匿名安装和实际Qt选择保存导出验证，重开XLSX核对仅名称列。保留旧包，官网稳定清单同步后进行实际本机升级与数据/配置保留验证。
6. 既有CAS机制创建新恢复点，不清理旧点；公开交接不含私有资料，正式数据仅用于只读保留核对，测试用匿名夹具。
