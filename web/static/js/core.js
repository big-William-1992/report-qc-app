const PAGE_TITLES = {
  qc:        { title: '报告质控',     sub: 'AI 驱动的放射报告质量检测引擎' },
  queue:     { title: '待质控队列',   sub: '排队中的报告，逐份质控并入库后自动出队' },
  dashboard: { title: '质控看板',     sub: '数据统计与质量趋势分析' },
  ris:       { title: 'RIS 直连',     sub: '连接 PACS/RIS 数据库获取报告' },
  samples:   { title: '样本库',       sub: '已质控报告的存储与管理' },
  users:     { title: '用户管理',     sub: '账号角色与科室分配' },
  rules:     { title: '规则维护',     sub: '查看和管理质控规则' },
};

let AUTH = {
  token: localStorage.getItem('xy-token') || '',
  empId: localStorage.getItem('xy-emp') || '',
  name:  localStorage.getItem('xy-name') || '',
  role:  localStorage.getItem('xy-role') || '',
};

async function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers || {});
  // FormData 上传不强制 Content-Type（保住 multipart boundary）；
  // 非 FormData 且未显式指定时才默认 JSON。
  const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  if (!isForm) opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
  if (AUTH.token) opts.headers['Authorization'] = 'Bearer ' + AUTH.token;
  if (AUTH.empId) opts.headers['X-Emp-Id'] = AUTH.empId;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 20000); // 20s 超时防挂起
  try {
    const resp = await fetch(url, Object.assign({}, opts, { signal: ctrl.signal }));
    if (resp.status === 401 && !url.includes('/accounts/login') && !url.includes('/accounts')) {
      // token 失效/被拒：清凭证回登录闸门（远程部署 401 不再"全部报错却无法重登"）
      localStorage.removeItem('xy-token'); localStorage.removeItem('xy-emp');
      localStorage.removeItem('xy-name'); localStorage.removeItem('xy-role');
      AUTH.token = ''; AUTH.empId = ''; AUTH.name = ''; AUTH.role = '';
      if (typeof showGate === 'function') { showGate(true); gateShow('login'); }
    }
    return resp;
  } finally {
    clearTimeout(timer);
  }
}

const SEV_META = {
  high:   { icon: '⛔', label: '严重', cls: 'danger' },
  medium: { icon: '⚠', label: '警告', cls: 'warning' },
  low:    { icon: 'ℹ', label: '提示', cls: 'info' },
};

function toggleSidebar() {
  const sb = document.querySelector('.sidebar');
  const bd = document.querySelector('.sidebar-backdrop');
  const open = sb.classList.toggle('open');
  if (bd) bd.classList.toggle('show', open);
}

function closeSidebar() {
  const sb = document.querySelector('.sidebar');
  const bd = document.querySelector('.sidebar-backdrop');
  if (sb) sb.classList.remove('open');
  if (bd) bd.classList.remove('show');
}

function switchPage(pageName, navEl) {
  // 窄屏折叠下切换页面后自动收起侧边栏
  closeSidebar();
  // 切换导航高亮
  document.querySelectorAll('.nav-cell').forEach(el => el.classList.remove('active'));
  if (navEl) navEl.classList.add('active');

  // 切换页面显示
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('page-' + pageName);
  if (target) target.classList.add('active');

  // 更新标题
  const info = PAGE_TITLES[pageName] || {};
  document.getElementById('pageTitle').textContent = info.title || '';
  document.getElementById('pageSubtitle').textContent = info.sub || '';

  // 页面加载时自动拉取数据
  if (pageName === 'dashboard') loadDashboard();
  if (pageName === 'samples') loadSamples();
  if (pageName === 'rules') { loadRules(); loadRulesConfig(true); }
  if (pageName === 'queue') loadQueue();
  if (pageName === 'users') loadUsers();
  if (pageName === 'ris') loadPollStatus();  // P0：轮询状态
}

function applyRoleUI() {
  const isAdmin = AUTH.role === 'admin';
  document.querySelectorAll('[data-admin-only]').forEach(el => {
    el.style.display = isAdmin ? '' : 'none';
  });
  // 侧边宫格：基于「可见项」判断奇数项 → 最后一项跨满整行。
  // 不能用 DOM :last-child:nth-child(odd)，隐藏的「用户管理」会干扰判断。
  document.querySelectorAll('#sidebar .nav-cell').forEach(el => el.classList.remove('span-full'));
  const cells = [...document.querySelectorAll('#sidebar .nav-cell')].filter(el => el.offsetParent !== null);
  if (cells.length % 2 === 1 && cells.length > 1) {
    cells[cells.length - 1].classList.add('span-full');
  }
  const sub = document.getElementById('userMenuSub');
  if (sub) sub.textContent = (isAdmin ? '系统管理员' : '医生') + ' · ' + (AUTH.empId || '');
}

function toast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('xy-theme', next);
  const t = document.getElementById('themeToggle');
  if (t) t.textContent = next === 'dark' ? '☀️' : '🌙';
}

(function initTheme() {
  const saved = localStorage.getItem('xy-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
    const t = document.getElementById('themeToggle');
    if (t) t.textContent = saved === 'dark' ? '☀️' : '🌙';
  }
})();

function showGate(v) {
  const g = document.getElementById('gate');
  if (g) g.style.display = v ? 'flex' : 'none';
}

function gateShow(step) {
  document.querySelectorAll('.gate-step').forEach(el => el.style.display = 'none');
  const el = document.getElementById('gate-' + step);
  if (el) el.style.display = 'block';
}
function setGateErr(id, msg) { const e = document.getElementById(id); if (e) e.textContent = msg || ''; }
