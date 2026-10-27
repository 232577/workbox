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
- Locale: zh-CN；日期 2026-10-26。
- Register: 直接说明能做什么，不编造使用量、评价或性能。
- Canonical owner: website/public/styles.css 的 :root 为网页运行时 token；本文件记录它，桌面应用 DESIGN.md 不受网页布局影响。
- Scope: 单页静态站，无登录、外部字体、追踪脚本、框架和构建依赖。

## Colors

主文字、次要文字和深海蓝操作继承桌面颜色。蓝绿用于文件夹与完成标识；状态文字同时写明含义。主要按钮白字深海蓝，焦点统一使用 3px 蓝色轮廓。浅蓝与浅薄荷绿区分使用说明和下载信息。

## Typography

标题用系统 Segoe UI Variable Display / 微软雅黑，正文用系统无衬线；代码和安装包信息使用等宽字体。不请求网络字体，避免加载位移。

## Layout

桌面首屏左侧说明和下载，右侧原生图形说明“收件—目录—版本”的流程。此图明确写“流程示意”，不用虚构软件界面。其后为三项能力、实际使用顺序、版本与校验、常见问题。700px 以下上下排列，380px 以下进一步缩小外边距；页面保持自然滚动。

## Components

- 主要按钮均为下载链接，使用相同名称和目标。
- GitHub 备用下载、源码、版本记录为普通链接。
- FAQ 使用原生 details / summary，支持键盘和触摸。
- 复制校验值使用原生 button；成功和权限失败通过固定高度 aria-live 状态区域告知，不移动按钮。不支持剪贴板时保留可选中的完整哈希。
- 所有链接、按钮和 summary 有可见焦点、hover 与 active 反馈；不使用浏览器弹窗。
- 无需加载、空态、会话、表单或 CRUD；下载结果由浏览器原生下载管理，不声称网页已确认下载成功。

## Content and data boundaries

固定版本 1.0.0、发布日期、大小和 SHA-256 以主代理给定的已验证安装包为准。公众可下载；干净安装不含业务数据。兼容性、未商业签名说明与升级备份要求明确呈现。工作流程说明保留主动收件、不读微信、保留源文件、首次有效交付统计、撤销退回待交付的边界。不使用客户资料或真实名字。

## References

- ../DESIGN.md：产品颜色、字体和表意风格。
- ../AGENTS.md：真实业务规则与数据安全边界。
- public/index.html：正式公开内容。
- public/styles.css：唯一网页 token 与响应式样式来源。

## Do's and Don'ts

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
