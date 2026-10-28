---
version: alpha
name: WorkBox public download website
description: WorkBox 公开下载页，沿用桌面产品的深海蓝和清楚直接的中文，提供安装、使用与校验信息。
colors:
  ink: "#172A3A"
  ocean: "#155970"
  ocean-hover: "#10465A"
  teal: "#237C79"
  muted: "#3D5266"
  canvas: "#FFFFFF"
  soft: "#F1F5F8"
  mint: "#E9F4F2"
  line: "#C5D4DD"
  focus: "#005FCC"
typography:
  sans:
    fontFamily: "Segoe UI, Microsoft YaHei UI, PingFang SC, sans-serif"
  display:
    fontFamily: "Segoe UI Variable Display, Segoe UI, Microsoft YaHei UI, sans-serif"
  mono:
    fontFamily: "Consolas, SFMono-Regular, monospace"
rounded:
  md: "7px"
  lg: "12px"
spacing:
  desktop-page: "40px"
  mobile-page: "24px"
omitted:
  - section: components
    reason: "单页原生 HTML，无组件框架；共享操作由 styles.css 的 button、text-link 与 copy-button 类管理。"
---

# WorkBox 下载页设计记录

## Overview

### Creative North Star

把下载页做成一个清楚的文件收纳说明：用一个文件夹贯穿消息、工作文件、版本与交付，以深海蓝操作和安静的浅蓝绿表面回应桌面产品。

### Product context

- Audience and primary job: 使用 Windows 的个人项目管理者；了解用途、确认兼容性并下载安装包。
- Locale: zh-CN；公开日期与版本按发布目录；首页改版于 2026-10-28。
- Register: 直接说明能做什么，不编造使用量、评价或性能。
- Canonical owner: public/styles.css 的 :root 管理基础字体与公共色；public/homepage.css 的 .home-page 管理营销首页独立布局和 --wb-* 色，统计页不引入该文件。桌面应用 DESIGN.md 不受网页布局影响。
- Scope: 公开首页无需登录，不请求外部字体，不依赖框架。匿名站点计数沿用同域 /api/events；统计页单独登录。

## Colors

主文字、次要文字和深海蓝操作继承桌面颜色。蓝绿用于文件夹与完成标识；状态文字同时写明含义。主要按钮白字深海蓝，焦点统一使用 3px 蓝色轮廓。浅蓝与浅薄荷绿区分使用说明和下载信息。

## Typography

标题用系统 Segoe UI Variable Display / 微软雅黑，正文用系统无衬线；代码和安装包信息使用等宽字体。不请求网络字体，避免加载位移。

## Layout

桌面首屏左侧以两行大标题直接说明项目与交付，右侧用有层次的文件夹、消息、文档和 ZIP 构成“功能流程”。图形不冒充软件截图。浅色正文与深蓝下载区交替，下载入口保留在页头和首屏。

其后为四步流程切换、六项项目管理能力、五项默认工作规则、下载与校验、常见问题。流程以真实顺序编号，但四种项目状态不是强制线性流转。850px 以下首屏与流程纵排，560px 以下功能列表单列；320px 宽保持内容可读，无横向页面溢出。页面自然滚动，页头固定且锚点与键盘焦点留出空间。

首页 token：--wb-navy #102D40（标题、主操作、下载底色）、--wb-green #167364（动作与规则）、--wb-mint #D4F1E7（轻强调）、--wb-paper #F5F8F9（功能区）、--wb-line #D9E3E7、--wb-muted #526471、--wb-aqua #8AE7DA（深底标记）。系统字体继承公共 token；正文 14–17px，关键标题 28–62px。图中注释是辅助信息，完整规则在正文重复可读。

## Components

- 主要按钮均为下载链接，使用相同名称和目标。
- GitHub 备用下载、源码、版本记录为普通链接。
- FAQ 使用原生 details / summary，支持键盘和触摸。
- 工作流程使用原生 button 渐进增强为 tabs，左右方向键环绕，Home/End 到首尾；选择状态、焦点与关联面板同步。无脚本或面板缺失时不启用控件，四块正文全部可读。不得用自动轮播隐藏说明。
- 工作设置是默认值说明，采用 dl，不使用不能操作的开关或输入框；需在桌面软件修改。
- 复制校验值使用原生 button；成功和权限失败通过固定高度 aria-live 状态区域告知，不移动按钮。不支持剪贴板时保留可选中的完整哈希。
- 所有链接、按钮和 summary 有可见焦点、hover 与 active 反馈；不使用浏览器弹窗。
- 公开首页无业务表单或 CRUD；下载结果由浏览器原生下载管理，不声称网页已确认下载成功。

## Content and data boundaries

固定版本 1.0.0、发布日期、大小和 SHA-256 以主代理给定的已验证安装包为准。公众可下载；干净安装不含业务数据。兼容性、未商业签名说明与升级备份要求明确呈现。工作流程说明保留主动收件、不读微信、保留源文件、首次有效交付统计、撤销退回待交付的边界。不使用客户资料或真实名字。

## References

- ../DESIGN.md：产品颜色、字体和表意风格。
- ../AGENTS.md：真实业务规则与数据安全边界。
- public/index.html：正式公开内容。
- public/styles.css：既有共享 token 与统计页基础样式。
- public/homepage.css：2026-10-28 官网首页专用 token、响应式与可访问样式。
- https://www.raycast.com/ ：按用户动作组织桌面工具能力，保留直接下载。
- https://linear.app/ ：按收件到跟进的流程逐段说明功能。
- https://obsidian.md/ ：读取官网并查看首屏，先清楚定位与下载，再展开产品能力。借鉴信息层次，未复制其黑色视觉或品牌素材。

Mobbin 检索因账户计划限制未返回图片，不作为设计证据。

## Do's and Don'ts

品牌标志为深蓝底的折叠盒/W矢量，复用桌面端 `branding.py` 的同源图形。
底色 `#102D40`，青绿色折盖 `#8AE7DA/#4DCAB9`，盒身 `#53D6C2/#25B5A4`。
官网、统计页及浏览器favicon统一使用此标志，保留统计访问边界。

- Do: 首屏突出下载，完整呈现兼容性与备用来源。
- Do: 清楚区分流程示意与应用截图。
- Don't: 编造用户数、下载量、测评或集成。
- Don't: 把复制失败隐藏，或引导用户关闭系统安全防护。

## 所有者统计页

`/stats/` 为所有者统计入口，属于管理工具。公开下载页仍沿用前述营销布局；此页增加登录、读取和会话恢复状态，原有“无登录、无需会话”的描述仅适用于公开下载页。

- 运行时颜色与字体继续由 `public/styles.css` 的 `:root` 管理；`public/stats.css` 只引用现有 token，不复制品牌色。今日用深海蓝顶线，累计用蓝绿顶线；文字标签始终保留含义。
- 主体按今日／累计摘要、每日趋势、每日明细和统计口径排列。趋势实线／虚线同时区分数据系列，明细表保留完整日期和数值。窄屏摘要纵向排列，明细可横向滚动且有可见提示。
- 原生 HTML 表单、按钮和操作系统下拉菜单为本页控件所有者，无依赖框架。密码默认遮罩、允许粘贴和密码管理器，提供显示／隐藏按钮；不进入 URL 或持久化浏览器存储。认证依赖服务器会话，静态 HTML 不含统计值。
- 读取失败显示错误及重试，首次错误不显示虚构零值；已有数据刷新失败时保留上次数据并明确时间。401 清空当前数据并返回登录；退出失败保留重试入口，不声称已退出。请求设超时并防止失效响应覆盖新状态。
- 日期按服务器返回的 Asia/Shanghai 日历日显示，更新时间明确为北京时间。CSV 只导出当前已取得的每日数据。下载表示成功开始传输，续传去重，不代表下载完成或安装；GitHub 备用仅计点击并另列；启用前历史不补造。
