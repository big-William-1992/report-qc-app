# UI 规范对齐检查单（shadcn/ui → 星衍 web/static）

> 目标：在**不引入 React/Tailwind/构建链**的前提下，把 shadcn/ui 的核心工程规范
> （可访问性、语义色、组件语义、状态反馈）逐项落到现有静态 SPA
> （`web/static/index.html` + `css/style.css` + `js/app.js`）。
> 状态标记：✅ 已对齐 · 🔄 部分 · ⬜ 待做 · ⚠️ 需权衡

## 1. 可访问性（Accessibility）

| 规范（shadcn 侧） | 现状 | 状态 |
|---|---|---|
| Dialog 必须有标题 `DialogTitle`，供读屏器关联 | 5 个模态均有 `<h3>` 标题 + `aria-labelledby` | ✅ |
| Dialog 根节点 `role="dialog" aria-modal="true"` | 5 个 `.modal-overlay` 均已补 | ✅ |
| 关闭按钮有可读名（读屏器报"关闭"而非"×"） | 5 个 `.modal-close` 均加 `aria-label="关闭"` | ✅ |
| 临时通知用 `role="status"` + `aria-live` 播报 | `#toastContainer` 已加 | ✅ |
| 键盘焦点可见：仅 `:focus-visible` 显示 focus ring | 全局 + 按钮/导航/关闭钮已加，覆盖原有 `outline:none` | ✅ |
| 表单控件关联 label（`for`/`id` 或包裹） | 元信息/设置/规则表单已有显式 label，弹簧待查 | ✅ |
| 模态打开时焦点移入、关闭后归还（focus trap） | 未做（静态实现仅在 display 切换） | ⬜ |
| 键盘 Esc 关闭模态 | OCR/通用确认模态支持 Esc；设置/规则/导出/样本模态未统一 | 🔄 |
| 颜色对比度（正文 ≥ 4.5:1，小字 3:1） | 暗色模式下 `text-secondary` 对比度偏弱 | 🔄 |
| 区域导航语义（`<nav aria-label>`、`<main>`、标题层级） | `sidebar-nav` / `.content` 已有元素，缺 aria-label | ⬜ |

## 2. 语义色（Semantic Tokens）

| 规范 | 现状 | 状态 |
|---|---|---|
| 用语义变量而非硬编码色值 | `:root` 定义了 `--primary/--success/--warning/--danger/--info`，业务基本走变量 | ✅ |
| 状态一律走 token：成功绿、警告橙、错误红、信息石板灰 | 分数卡/发现列表/徽章均用 token | ✅ |
| 暗色主题只换 token，不逐层找补 | `[data-theme="dark"]` 集中覆盖一组变量 | ✅ |
| 残留硬编码色收敛到 token | 少量 `#667eea/#764ba2` 渐变、`#1e54c7` hover 深蓝 | 🔄 |

## 3. 组件语义（Composition）

| 规范 | 现状 | 状态 |
|---|---|---|
| 空状态用统一组件（shadcn `Empty`） | `.empty-state` 类已统一（📭 图标+文案） | ✅ |
| 提示用 Alert 语义（标题+描述+等级） | 未系统化；试用期横幅/提示条各自为政 | 🔄 |
| Toast 用统一队列（shadcn `sonner`） | `#toastContainer` 已有队列 + 上限 4 条 | ✅ |
| 按钮 loading 态：禁用 + 文案切换，不隐藏按钮 | `runQC`/`scanReportsForTypos`/OCR `setBusy` 均切换 disabled+文案 | ✅ |
| 图标进按钮用固定槽位，不随文字抖动 | 上轮已把操作按钮改为 SVG+span 结构，label 单独更新 | ✅ |
| 标签页/筛选器用 Tabs 语义 | `qctabList/qctabAnno` + 严重度筛选：`role="tablist/tab/tabpanel"`、`aria-selected`/`aria-controls`，JS 同步切换 | ✅ |
| 分割线/徽章有对应语义元素 | `Badge`/`Separator` 未组件化（有 `.badge` 类，缺语义） | ⬜ |

## 4. 状态反馈

| 规范 | 现状 | 状态 |
|---|---|---|
| 危险操作二次确认 | 统一 `confirmAction()` 页面内模态（Promise），8 处原生 `confirm()` 全部替换，危险操作红色按钮，Esc/取消/确定齐全 | ✅ |
| 长任务显示进度（扫描学习/批量质控） | 扫描有"扫描中…"；队列批量质控用进度条 | ✅ |
| 运行中禁止重复提交 | `_qcRunning` 锁已加 | ✅ |
| 表单校验错误就地提示（`aria-invalid`） | 登录/创建账号有 `.gate-err`，无 `aria-invalid` 关联 | ⬜ |
| 登录闸门多步骤（登录/注册/修改密码）步骤切换清理与聚焦 | 三步骤共用 `.gate-err` 就地报错，切换时清空错误并聚焦首字段 | ✅ |

## 5. 交互细节（沿用上轮建议）

- ✅ runQC 防重入 + busy 态
- ✅ 全局快捷键焦点过滤（输入框内不误触跨页动作）
- ✅ OCR 粘贴监听挂 window（模态打开即生效）
- ✅ toast 堆叠上限
- ✅ 导航三组收敛 + 线性 SVG 图标
- ✅ 顶部 🔔 通知按钮为占位且无数据源，已 `display:none` 隐藏（去红点）
- ✅ 登录闸门新增「注册账号」「修改密码」入口：注册始终为医生角色（空库拒绝，防抢先注册提权），改密须校验旧密码；成功后均回登录页并预填工号
- ⬜ 元信息卡默认折叠，焦点留给描述/诊断
- ⬜ 质控发现逐条"原文→修正"diff 预览（现仅全部采纳）
