/**
 * 星衍AI放射质控 · Web 版前端逻辑
 * SPA 路由 / API 交互 / 结果渲染
 */

// ==================== SPA 页面切换 ====================
const PAGE_TITLES = {
  qc:        { title: '报告质控',     sub: 'AI 驱动的放射报告质量检测引擎' },
  queue:     { title: '待质控队列',   sub: '排队中的报告，逐份质控并入库后自动出队' },
  dashboard: { title: '质控看板',     sub: '数据统计与质量趋势分析' },
  ris:       { title: '数据接入',     sub: 'PACS 推送接收 / 数据库直连' },
  samples:   { title: '样本库',       sub: '已质控报告的存储与管理' },
  rules:     { title: '规则维护',     sub: '查看和管理质控规则' },
  feedback:  { title: '反馈审核',     sub: 'R19 误报/漏报人工审核，驱动词表闭环' },
  audit:     { title: '审计日志',     sub: '操作审计记录，供管理员事后审查' },
};

// 全局应用设置（从 /api/v1/settings 载入，影响 OCR/入库/自动化行为）
let APP_SETTINGS = {
  emp_id: 'demo01', default_modality: '', auto_qc_on_ocr: true, auto_enqueue: true,
  ocr_min_score: 0.55, screen_refresh_on_ocr: false, anonymize: false, theme: 'light',
  ocr_dynamic: true,   // 动态语义识别（整屏OCR按标题切分，PACS滚动不变形）
  ocr_silent: false,   // 静默模式：一键识别质控完成后不强制弹窗，保持后台运行
  // 默认 Windows 风 Ctrl+；设置页可逐条重绑（保存后持久化到 web_settings.json）
  shortcuts: {
    run_qc:       { mods: ['ctrl'], key: 'Enter' },
    save_sample:  { mods: ['ctrl'], key: 's' },
    paste_split:  { mods: ['ctrl', 'shift'], key: 'v' },
    ocr_capture:  { mods: ['ctrl', 'shift'], key: 'o' },
    toggle_theme: { mods: ['ctrl'], key: 't' },
  },
};

// ==================== 账号 / 授权上下文 ====================
// 登录态在 localStorage 持久化：刷新页面不必重复登录
let AUTH = {
  token: localStorage.getItem('xy-token') || '',
  empId: localStorage.getItem('xy-emp') || '',
  name:  localStorage.getItem('xy-name') || '',
  role:  localStorage.getItem('xy-role') || '',
};
window.LICENSE_STATUS = null;   // 最近一次授权状态聚合

// 统一 API 封装：自动附加鉴权头（授权相关请求使用；业务请求沿用原 fetch 不受影响）
async function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers || {});
  opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
  if (AUTH.token)    opts.headers['Authorization'] = 'Bearer ' + AUTH.token;
  if (AUTH.empId)    opts.headers['X-Emp-Id'] = AUTH.empId;
  return fetch(url, opts);
}

// ==================== Toast 通知 ====================
let _toastCount = 0;            // 当前存活 toast 数（防堆叠上限）
const _TOAST_MAX = 4;          // 同时最多显示 4 条，超出剔除最旧的
function toast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  if (!c) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  _toastCount++;
  while (c.children.length > _TOAST_MAX) c.removeChild(c.firstChild);
  setTimeout(() => { el.remove(); _toastCount = Math.max(0, _toastCount - 1); }, 3500);
}

// ==================== 通用确认模态 ====================
let _confirmResolve = null;   // 当前确认模态的 Promise 回调

// 替代原生 confirm()：桌面壳/pywebview 下原生对话框体验不稳定，统一走页面内模态
// 返回 Promise<boolean>，可在 async 函数中 await
function confirmAction(title, msg, danger, okText) {
  return new Promise(resolve => {
    const overlay = document.getElementById('confirmModal');
    const titleEl = document.getElementById('confirmModalTitle');
    const msgEl = document.getElementById('confirmModalMsg');
    const okBtn = document.getElementById('confirmModalOk');
    if (!overlay || !msgEl) { resolve(false); return; }
    _confirmResolve = resolve;
    if (titleEl) titleEl.textContent = title || '请确认';
    msgEl.textContent = msg || '';
    okBtn.textContent = okText || '确定';
    okBtn.classList.toggle('btn-danger', !!danger);
    okBtn.classList.toggle('btn-primary', !danger);
    overlay.style.display = 'flex';
    okBtn.focus();
  });
}

function confirmModalResolve(val) {
  const overlay = document.getElementById('confirmModal');
  if (!overlay) return;
  overlay.style.display = 'none';
  if (_confirmResolve) {
    const r = _confirmResolve;
    _confirmResolve = null;
    r(val);
  }
}

function animateNumber(elId, target) {
  const el = document.getElementById(elId);
  const start = parseInt(el.textContent) || 0;
  const duration = 600;
  const startTime = performance.now();

  function step(now) {
    const p = Math.min((now - startTime) / duration, 1);
    el.textContent = Math.round(start + (target - start) * easeOutCubic(p));
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

// ==================== 工具函数 ====================
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ==================== 明暗主题切换 ====================
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('xy-theme', next);
  _renderThemeIcon(next);
}
(function initTheme() {
  const saved = localStorage.getItem('xy-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
    _renderThemeIcon(saved);
  }
})();
function _renderThemeIcon(theme) {
  const moon = document.querySelector('.btn-theme-moon');
  const sun = document.querySelector('.btn-theme-sun');
  if (theme === 'dark') {
    if (moon) moon.style.display = 'none';
    if (sun) sun.style.display = '';
  } else {
    if (moon) moon.style.display = '';
    if (sun) sun.style.display = 'none';
  }
}

function setVal(id, v) {
  const el = document.getElementById(id);
  if (!el || !v) return;
  el.value = v;
  el.dispatchEvent(new Event('input'));
}

// 把 {mods:[...], key:"..."} 渲染成展示串，如 "Ctrl+Shift+O"
function fmtShortcut(sc) {
  if (!sc || !sc.key) return '未设置';
  const names = { ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', meta: 'Win' };
  const mods = (sc.mods || []).map(m => names[m] || m).join('+');
  const k = sc.key.length === 1 ? sc.key.toUpperCase() : sc.key;
  return mods ? (mods + '+' + k) : k;
}

// 从 keydown 事件解析出组合键描述
function comboFromEvent(e) {
  const mods = [];
  if (e.ctrlKey)  mods.push('ctrl');
  if (e.shiftKey) mods.push('shift');
  if (e.altKey)   mods.push('alt');
  if (e.metaKey)  mods.push('meta');
  let key = e.key;
  if (key === ' ') key = 'Space';
  else if (key.length === 1) key = key.toLowerCase();   // 字母统一小写
  return { mods, key };
}

// 组合键精确匹配（修饰键集合 + 主键 都要一致）
function comboEquals(sc, evt) {
  if (!sc || !sc.key || !evt) return false;
  const a = (sc.mods || []).slice().sort().join(',');
  const b = evt.mods.slice().sort().join(',');
  if (a !== b) return false;
  const k = sc.key.length === 1 ? sc.key.toLowerCase() : sc.key;
  return k === evt.key;
}

export { apiFetch, toast, confirmAction, confirmModalResolve, escapeHtml, setVal, fmtShortcut, comboFromEvent, comboEquals, animateNumber, easeOutCubic, toggleTheme, APP_SETTINGS, AUTH, PAGE_TITLES };

Object.assign(window, { confirmModalResolve, toggleTheme, APP_SETTINGS, AUTH });
