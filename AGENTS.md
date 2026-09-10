# 星衍放射质控软件 · Agent 工作备忘

## 1. 前端加载：永远不要用 ES 模块（type="module"）

**不要犯的错**：把 app.js 改成 ES 模块入口 `type="module"` 并让 index.html 引用。

**原因**：
- 桌面应用有两条加载路径：
  - `file://` 协议（用户双击 HTML / 本地预览）— ES 模块被浏览器 CORS 拦截（`Cross-Origin Request Blocked`）
  - `http://` 协议（FastAPI 服务 + pywebview）— ES 模块正常
- 同一条 `<script>` 无法同时兼容两种协议。

**正确做法**：
- 所有前端 JS 保持 **经典脚本**（无 `import`/`export`），合并为单个 `app.bundle.js`
- 模块源文件（`modules/*.js`）保留作代码组织参考，不直接加载
- 用 `split_modules.py` 做一次提取后，集成到 bundle 中

## 2. 静态资源路径：绝对路径 vs 相对路径

**不要犯的错**：用 `/static/css/style.css` 这种绝对路径。

**原因**：
- `file://` 下，`/static/...` 指向 `file:///static/...`（文件系统根目录），不存在
- `http://` 下，FastAPI 通过 `app.mount("/static", ...)` 正确映射

**正确做法**：使用**相对路径**（相对于 `index.html` 所在目录）：
```html
<link rel="stylesheet" href="css/style.css?v=..." />
<script src="js/app.bundle.js?v=..."></script>
```
然后在服务端 `spa_fallback` 函数中，对根级路径（`/css/...`、`/js/...`）检查 `_STATIC_DIR` 下是否存在真实文件，存在则直接返回。

## 3. Git 合并冲突常丢的类

- `server/schemas.py` 中的 `RegisterReq`、`ChangePwdReq`
- 合并后必须检查这些 schema 类是否存在，否则登录/注册/改密 422 错误

## 4. 模块合并注意事项

将 ES 模块（各自独立作用域）合并为单一经典脚本时：
- 检查顶层 `const`/`let` 重复声明（SyntaxError）
- 检查 `function` 重复声明（后覆盖前，一般无害）
- `Object.assign(window, ...)` 保留，每个模块把自己的函数挂到 window

## 5. 密码策略变更后要同步更新测试

`src/accounts.py` 密码长度要求改动后（6→8 位），以下测试断言需要同步：
- `tests/test_accounts.py::TestAccounts::test_password_min_length`
- `tests/test_auth_security.py::TestPasswordPolicy::test_min_password_length`

## 6. GitHub 推送

远程仓库使用 SSH 协议（`git@github.com:big-William-1992/report-qc-app.git`），如果 SSH 超时需要：
- 确认 VPN 连接
- `ssh -T git@github.com` 测试连通性
- 重试 `git push github main`
