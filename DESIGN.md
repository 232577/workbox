---
version: alpha
name: WorkBox 工作箱
description: Windows 原生项目、文件与交付工作台
colors:
  primary: "#096C65"
  primary-hover: "#075D57"
  primary-pressed: "#054B47"
  on-primary: "#FFFFFF"
  window: "#F1F3F5"
  surface: "#FCFDFE"
  subtle-surface: "#F6F8FA"
  raised: "#E7EBEF"
  ink: "#222B35"
  muted: "#46515E"
  border: "#D7DEE5"
  control-border: "#88939E"
  button-border: "#C5CED7"
  hover: "#EDF1F5"
  row-selected: "#E5EBF1"
  row-selected-border: "#BECBD7"
  notice-background: "#E4F0EF"
  navigation-background: "#E7EBEF"
  navigation-selected: "#F8FAFB"
  navigation-accent: "#085E58"
  working: "#195BA3"
  pending-delivery: "#8A4A0C"
  error: "#AF303B"
  primary-dark: "#76CFC1"
  primary-hover-dark: "#91DACE"
  primary-pressed-dark: "#62B9AD"
  on-primary-dark: "#17282B"
  window-dark: "#181A1C"
  surface-dark: "#23292F"
  subtle-surface-dark: "#20262D"
  raised-dark: "#303941"
  ink-dark: "#EEF1F5"
  muted-dark: "#C4CCD5"
  border-dark: "#38424D"
  control-border-dark: "#737F8D"
  button-border-dark: "#4A5663"
  hover-dark: "#2C343D"
  row-selected-dark: "#323D47"
  row-selected-border-dark: "#4E606F"
  notice-background-dark: "#28413F"
  navigation-background-dark: "#1C2229"
  navigation-selected-dark: "#303941"
  navigation-accent-dark: "#91DACF"
  working-dark: "#9BC9FA"
  pending-delivery-dark: "#F2BD73"
  error-dark: "#FFACB4"
typography:
  shell-title:
    fontSize: "17px"
    fontWeight: 600
  shell-title-compact:
    fontSize: "16px"
    fontWeight: 600
  brand:
    fontSize: "15px"
    fontWeight: 600
  object-title:
    fontSize: "18px"
    fontWeight: 600
  queue-title:
    fontSize: "15px"
    fontWeight: 600
  body:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, PingFang SC, Noto Sans SC, Noto Sans CJK SC, Source Han Sans SC, SimHei, WenQuanYi Micro Hei, Segoe UI, Noto Sans, DejaVu Sans"
    fontSize: "14px"
  primary-action:
    fontSize: "14px"
    fontWeight: 600
  row-title:
    fontSize: "14px"
    fontWeight: 500
  metadata:
    fontSize: "12px"
  batch-mark:
    fontSize: "11px"
rounded:
  row: "4px"
  compact-control: "5px"
  control: "6px"
  utility-surface: "8px"
spacing:
  navigation-gap: "4px"
  navigation-inset: "14px"
  navigation-inset-narrow: "10px"
  command-gap: "12px"
  split-handle: "8px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.primary-action}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-primary-dark:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.on-primary-dark}"
    typography: "{typography.primary-action}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-standard:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  text-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0px 10px"
    height: "32px"
  top-navigation-item-selected:
    backgroundColor: "{colors.navigation-selected}"
    textColor: "{colors.navigation-accent}"
    rounded: "{rounded.compact-control}"
    padding: "5px 10px"
  project-row-selected:
    backgroundColor: "{colors.row-selected}"
    textColor: "{colors.ink}"
    rounded: "{rounded.row}"
  filter-chip:
    backgroundColor: "{colors.notice-background}"
    textColor: "{colors.navigation-accent}"
    typography: "{typography.metadata}"
    rounded: "{rounded.compact-control}"
    padding: "3px 7px"
---

# WorkBox 桌面设计系统

## Overview

**Creative North Star: "原生横向工作台"**

WorkBox 是 Windows 原生桌面工作区。原生标题栏显示完整构建号，横向导航带着 WorkBox 图标连接项目、交付记录和异常入口。内容从当前项目、文件和操作展开，常用命令与操作对象就近摆放。

冷灰构成窗口和工作平面，青绿用于主操作、焦点与选中导航。界面以项目与文件的可读性为先，沿用 Qt Widgets 和锁定的 QFluentWidgets。具体页面与验证范围见[横向工作台改版记录](docs/UI-DESKTOP-SHELL.md)。

## Colors

浅色、深色及跟随系统共用同一组语义。主按钮使用青绿填色并反转文字色，普通按钮使用内容面色。导航用独立的底色、选中面与底部强调线，清单选中行使用中性色面和轮廓，键盘焦点换成青绿轮廓。

正文和元数据各用独立的前景色。开发中、待交付、已交付和错误提示有相应语义色，状态文字始终可读。批次颜色是批次关联的辅助线索，批次编号及数量提供完整语义。颜色与状态源见 [theme.py](theme.py) 和 [control_styles.py](control_styles.py)。

## Typography

正文为 14px。Qt 与 Fluent 共用本机已安装的字体序列，以微软雅黑 UI 及中文无衬线回退优先。项目清单名称为 14px 中等字重，下一层是 12px 状态和元数据，批次标记用 11px。长名称按字词换行，行高随实际内容变化。

所选项目名称为 18px 半粗，项目清单标题和品牌文字为 15px 半粗。常规壳标题 17px，窗口高度不足 560px 时切为 16px。字号以 Qt 逻辑像素计。

## Layout

- 原生标题栏下依次为横向工作区导航、当前页命令区、工作平面和底部操作区。项目和今日工作量常驻。待核对有事项或正在查看时出现，ZIP 有待核对、错误、状态不可读或正在查看时可直接到达。高级设置在导航末端，继续使用原生菜单。
- 窄窗口的 ZIP 多状态入口使用短标题，不可读状态优先显示；完整计数仍保留在悬停和辅助阅读说明中。超过 99 条的待核对显示 99+，不改变实际数量。
- 项目和 ZIP 页共享搜索与收件命令。项目清单常驻记录日期和带件数的状态分段；单日和时间段共用原生日历，范围包含首尾日期。批次、类型、归档范围、截止日期和加急条件放在「更多筛选」原生浮层，关闭后仍能看见、清除活动条件。浮层不挤压项目列表，离开项目页或隐藏主窗时同步关闭。
- 件数按项目条目统计，日期取首次记录时间并换算为北京时间，各状态显示当前状态。统计与清单共用同一读取快照及其他筛选条件；状态切换不会缩小其他状态的件数。默认范围明确标为日常项目，未知日期不猜测，合并后只计保留项目。不将这些数字称为剩余工作量或历史状态统计；今日工作量的首次有效交付规则保持独立。
- 主窗默认 1200×780，最小 660×480。工作区宽度达到 900px 时，项目清单默认 360px 并尊重控件最小宽度，文件区使用剩余空间。分隔条可拖动，来回切换窗口宽度时保留手动比例。
- 工作区不足 900px 时，清单与项目详情各占全宽。点击项目或用键盘激活进入详情，使用「返回项目清单」恢复滚动锚点和列表焦点。批量选择保持清单操作。窄窗导航保留品牌图标和可访问的设置名称。
- 嵌入交付页在宿主高度不足 620px 或宽度不足 820px 时，使用「项目记录」和「交付明细」两页，窄窗明细显示所属项目全名。宽窗保留分栏及明细展开，表格沿用原生像素滚动和手动列宽。

## 品牌与悬浮入口

采用「立方工作台」：青蓝顶面、蓝色左面、蓝紫右面和白色矢量 W。
`branding.py` 是唯一图形源，应用、托盘、标题栏、工作台和网站 SVG 保持同源；
Windows ICO 包含 16/24/32/48/64/128/256 七档原生帧，另验证 512px 品牌图。
这次只更新品牌与悬浮入口，不替换当前工作台布局或业务颜色语义。

悬浮入口默认 48×48 Qt 逻辑像素，原生右键菜单支持 32、40、48、56、64 五档。
尺寸偏好保存成功才调整窗口，并约束在屏幕可用区域内；历史异常设置只回退显示，不擅自改写。
悬停不改变点击区域。真实处理进度使用外圈，项目计数使用角标；32px 紧凑模式使用小点，
完整数量保留在辅助阅读说明中。角标不是虚构通知。支持键盘打开、拖动、拖放和托盘恢复。
详细约束与实际 Qt 验证见[立方品牌记录](docs/UI-CUBE-BRAND.md)。

## Elevation & Depth

主壳、清单与文件面采用平整的色面。导航和底栏用细分隔线收边，命令区与清单使用次级表面，文档区使用内容表面。分隔条在悬停时提高线条对比。选中与焦点由行面色和边线区分，信息无需依赖阴影。

## Shapes

主壳和分栏采用直角边界。通用按钮和输入框使用 6px 圆角，顶栏项及筛选标记用 5px，列表轮廓和批次标记用 4px。状态横幅和指标带使用 8px 圆角。保留真实 Qt 滚动条、菜单和窗体边界。

## Components

### 命令与字段

常用控件约 32px，公共 Fluent 单行输入框固定为 32px，命令区搜索在紧凑模式下最小高度为 28px。按钮和字段使用不同的悬停、按下、禁用与焦点样式，主按钮以高对比边线表达焦点。项目详情中仍可收进文件，该入口采用普通按钮样式，为所选项目动作让出视觉优先级。

### 项目列表与文件区

一个项目对应一个列表项，名称与状态、类型等元数据通常分两行。批次标记能放下时与元数据同排，否则换行。主清单最多并列两个批次编号，额外批次用「+N批」，不常驻显示批次待完成数。极窄时可省略编号中段，工具提示及无障碍说明保留完整编号与进度；主动选择批次后，可在更多筛选中查看整批进度。批次关联只读，保持项目排序、原始批次和目录。

所选项目的完整名称、状态与项目动作集中在文件面上方。常用的工作目录与交付动作靠近对象，项目及文件的其他操作走各自的原生菜单。空选择收起不适用的操作，文件读取错误提供重试。未知、部分和上次读取分别显示。

多选后右击已选项目，菜单顶部明确显示所选数量，并提供「标记加急」「取消加急」。两项操作应用于打开菜单时的选择；下方「当前项目」动作仍对应右击项目。右击未选项目或单选时保留单项目加急操作。加急与非加急混选时使用明确的设置/取消命令，不按某一行的状态反转整批。

### 反馈与导出

普通收件成功提示只在收件页面短暂显示，异常、待核对和忙碌提示持续可见。后台任务提供取消入口，状态由结构化结果决定。

「待开发」清单提供「导出名单」，只读导出全部未归档、未删除的待开发项目，合并后按保留项目计一行。搜索、批次和筛选只影响界面清单。工作簿只有「待开发项目」工作表与「项目名称」一列，独立项目重名时各占一行，空名单仍包含表头。交付页另保留全量有效交付项目导出与交付历史。

两种导出复用原生保存对话框和后台任务，最终路径已有文件时显式确认。发布前核对文件身份和当前受管路径。Windows 使用已核对的文件句柄发布，新文件和恢复文件使用不覆盖改名。成功反馈显示数量与保存位置，旧件保留时同时告知恢复位置。取消或失败保留业务数据与既有文件。两次改名之间若进程异常退出，旧件留在恢复名下。

## Do's and Don'ts

- **Do** 让命令靠近当前对象，保留键盘可达、清晰焦点及完整的无障碍文本。
- **Do** 保留手动分栏与列宽、选中项和阅读位置，按窗口空间调整同一工作流程。
- **Do** 用文字保留批次、状态、已知数量和错误的完整含义。
- **Don't** 用装饰性卡片、图表或渐变替代真实工作内容。
- **Don't** 用颜色替代批次编号或状态文字，也不要把未知数据写成零。
- **Don't** 通过筛选、导出、改版或目录显示改变项目身份、原件、原始批次、交付历史和工作量规则。

早期布局和业务决策保留于[原桌面设计记录](docs/UI-DESIGN-SYSTEM.md)、[成熟工作台改版](docs/UI-MATURE-WORKSPACE.md)、[批次标记](docs/PROJECT-BATCH-MARKERS.md)、[历史任务记录](docs/TASK-GROUPS.md)及[数量分类](docs/UI-CLEANUP-EXACT16.md)。站点设计单独记录于[网站设计记录](website/DESIGN.md)。
