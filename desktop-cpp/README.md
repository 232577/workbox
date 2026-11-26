# WorkBox C++ 桌面端原型

此目录是迁移分支上的独立 Windows 原型。客户端使用 C++20、Qt Widgets 和 SQLite，程序运行时无需 Python。Python 用于生成测试数据库，确保原型面对的是现有 WorkBox 实际创建的表结构。

原型覆盖项目清单、按名称、原批次、备注或关联 ZIP 搜索、按状态筛选、状态计数、Ctrl/Shift 多选，以及整批改为待开发、开发中或待交付。已交付项目回退前需要确认。目录恢复锁定、归档、已删除和已合并项目受原有写入限制。批量更新只修改当前状态、当前交付标记和更新时间。

启动时选取一个明确的匿名数据库，SQLite 以只读方式打开并通过备份 API 将一致的快照复制到内存。原型关闭后，演示中的状态修改随内存副本释放。请使用下面生成的测试数据或另行准备的脱敏副本。

## Windows 构建和试用

安装 Visual Studio C++ x64 工具、Windows SDK 和 Qt 6.8 或更新的 MSVC x64 开发包。在仓库根目录运行以下命令，并按本机安装位置修改 Qt 路径。

```powershell
$qt = 'C:/Qt/6.8.3/msvc2022_64'
$env:Path = "$qt/bin;$env:Path"
cmake -S desktop-cpp -B .local/cpp-build -G "Visual Studio 18 2026" -A x64 -DCMAKE_TRY_COMPILE_CONFIGURATION=Release "-DCMAKE_PREFIX_PATH=$qt"
cmake --build .local/cpp-build --config Release --parallel 1
ctest --test-dir .local/cpp-build -C Release --output-on-failure

python desktop-cpp/tests/create_fixture.py .local/cpp-build/anonymous.db --extra-projects 695
& .local/cpp-build/Release/WorkBoxCppPrototype.exe .local/cpp-build/anonymous.db
```

Windows 原型链接系统提供的 SQLite。使用 `-DWORKBOX_CPP_BUILD_GUI=OFF` 可构建数据层及其测试。使用 `-DBUILD_TESTING=OFF` 可在无 Python 的环境构建程序。

## 首轮验证

涉及原型的 Pull Request 会运行独立的 Windows C++ 构建和两组原生回归。

Windows x64 Release 使用 MSVC 19.51 和 Qt 6.8.3 编译。两组 CTest 通过。数据层验证了无效成员导致整批回滚，Qt 界面验证了 Ctrl/Shift 多选、右侧摘要、筛选和批量改状态。两个测试都在退出时核对了匿名输入数据库的 SHA-256。

在 700 个匿名项目的离屏 Qt 测试中，更新 120 项并刷新清单的六次运行中位数约 3.7 毫秒。Qt 应用初始化后创建窗口并完成首次渲染约 19 毫秒。[多选及更新后的 Qt 实际渲染](docs/project-window.png)

## 切换主分支前的验证

1. 在相同硬件上，用相同数据和操作脚本对比现有稳定版。记录冷启动、内存、滚动、查询和批量响应，分别标注数据层及端到端的耗时。
2. 补齐原批次标记、日期和项目类型筛选、文档预览等清单行为。迁移交付、归档、导入、托盘、更新及原站同步，逐项复用旧版行为测试和现有匿名数据库。
3. 在数据库副本上完成会话锁、目录恢复、历史交付、文件校验及中断恢复验收。以原安装目录升级和卸载验收覆盖安装器、旧数据及回退边界。
4. 经独立原生测试、完整 Windows 回归与正式安装验收后，使用 Pull Request 将迁移分支整合到现有 `main`。使用准确主线提交生产安装包。

当前原型使用内存快照。若把它的查询耗时直接与稳定版的磁盘数据库和完整界面操作时间相比，结果同时包含数据范围和存储条件的差异。
