# Studio 与名单核对实施计划

> 使用 superpowers:executing-plans 在当前独立任务分支逐项实施。

**Goal:** 集成外部名单只读核对与材料缺项汇总，更新整体及共享组件风格。
**Architecture:** 纯读取解析器 + 只读项目/材料适配层 + 可取消Qt工作台；Studio样式复用既有主题入口。
**Tech Stack:** Python 3.11、PySide6 6.8.3、QFluentWidgets 1.11.3、现有openpyxl依赖；不新增依赖。
**Spec:** ../specs/2026-11-09-studio-checklist-design.md

## 全局约束与风险
只在current任务分支修改，不向run顶层增加工作副本。匿名夹具，不读取真实业务库做测试。保护原文件/项目身份/交付历史；无数据库迁移。读表有大小、展开大小、行列和名称数限制；拒绝公式、不猜缺失年份。异步关闭先取消并等待，不能销毁运行线程。UI有实际截图，源码集成不冒充已安装。

## 任务
- [x] 1. tests/test_studio_design.py、tests/test_checklist_reader.py 先观察红灯；studio_design.py、checklist_reader.py 实现纯规则与完整组件语义样式。
- [x] 2. tests/test_checklist_review.py 先复现未实现行为；checklist_review.py 连接主线完整名称、受保护状态、当前材料API与已保存规则。校验没有任何写操作。
- [x] 3. tests/test_checklist_ui.py 先红；checklist_ui.py 后台导入/核对/取消，compact_ui.py 新入口，desktop_controller.py 安全退出。保留#66文件处理链。
- [ ] 4. 浅深/宽窄/键盘/组件画廊实际Qt验证，全部关联与完整Windows/网站/仓库检查；保留失败及修复记录。
- [ ] 5. 准确提交推送PR，复核审查和六项CI，显式同步main处理重叠，符合保护要求后合入；清楚记录未发布/未安装范围。
