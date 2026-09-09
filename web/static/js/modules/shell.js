import { toast, confirmAction, escapeHtml, APP_SETTINGS, AUTH, PAGE_TITLES } from "./core.js";

// 严重度元数据：图标 + 文字（色盲可用）
const SEV_META = {
  high:   { icon: '⛔', label: '严重', cls: 'danger' },
  medium: { icon: '⚠', label: '警告', cls: 'warning' },
  low:    { icon: 'ℹ', label: '提示', cls: 'info' },
};

// 窄屏（≤1024px）侧边栏抽屉：展开/收起 + 遮罩
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
  if (pageName === 'ris') loadRisPage();  // 数据接入页加载
  if (pageName === 'feedback') loadFeedback();  // 反馈审核页加载
  if (pageName === 'audit') loadAudit();  // 审计日志页加载
}

// ==================== 角色 UI 适配 + 用户管理 ====================
function applyRoleUI() {
  const isAdmin = AUTH.role === 'admin';
  document.querySelectorAll('[data-admin-only]').forEach(el => {
    el.style.display = isAdmin ? '' : 'none';
  });
  // 侧边宫格：按分组独立计算「可见项」为奇数 → 该组最后一项跨满整行。
  // 不能用 DOM :last-child:nth-child(odd)，隐藏的「用户管理」会干扰判断。
  document.querySelectorAll('#sidebar .nav-cell').forEach(el => el.classList.remove('span-full'));
  document.querySelectorAll('#sidebar .nav-grid').forEach(grid => {
    const cells = [...grid.querySelectorAll('.nav-cell')].filter(el => el.offsetParent !== null);
    if (cells.length % 2 === 1 && cells.length > 1) {
      cells[cells.length - 1].classList.add('span-full');
    }
  });
  const sub = document.getElementById('userMenuSub');
  if (sub) sub.textContent = (isAdmin ? '系统管理员' : '医生') + ' · ' + (AUTH.empId || '');
}

async function loadUsers() {
  try {
    const res = await apiFetch('/api/v1/accounts');
    const d = await res.json();
    const users = d.data || [];
    // 拉取科室列表填充下拉
    let depts = [];
    try {
      const dr = await apiFetch('/api/v1/departments');
      const dd = await dr.json();
      depts = dd.data || [];
    } catch (e) { console.warn('load departments failed', e); }
    const tbody = document.getElementById('usersBody');
    tbody.innerHTML = users.map(u => {
      const opts = ['<option value="">-- 未分配 --</option>']
        .concat(depts.map(dp =>
          `<option value="${dp.id}" ${u.dept_id === dp.id ? 'selected' : ''}>${escapeHtml(dp.name)}</option>`))
        .join('');
      return `
      <tr>
        <td>${escapeHtml(u.emp_id)}</td>
        <td>${escapeHtml(u.name || '--')}</td>
        <td>
          <select onchange="changeUserRole('${escapeHtml(u.emp_id)}', this.value)" ${u.emp_id === AUTH.empId ? 'disabled' : ''}>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>管理员</option>
            <option value="doctor" ${u.role === 'doctor' ? 'selected' : ''}>医生</option>
          </select>
        </td>
        <td>
          <select onchange="changeUserDept('${escapeHtml(u.emp_id)}', this.value ? parseInt(this.value, 10) : null)">
            ${opts}
          </select>
        </td>
        <td style="white-space:nowrap;">
          <button class="btn btn-outline btn-sm" onclick="resetUserPwd('${escapeHtml(u.emp_id)}')">🔑 重置密码</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) { console.error(e); }
}

async function changeUserRole(empId, role) {
  const roleName = role === 'admin' ? '管理员' : '医生';
  if (!(await confirmAction('修改角色', `确认将 ${empId} 的角色改为 ${roleName}？`))) return;
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/role', {
    method: 'POST', body: JSON.stringify({ role })
  });
  const d = await res.json();
  toast(d.ok ? '角色已更新' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

async function changeUserDept(empId, deptId) {
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/dept', {
    method: 'POST', body: JSON.stringify({ dept_id: deptId })
  });
  const d = await res.json();
  toast(d.ok ? '科室已更新' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

async function resetUserPwd(empId) {
  const pw = prompt('输入新密码（至少 6 位）：');
  if (!pw || pw.length < 6) { toast('密码至少 6 位', 'error'); return; }
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/password', {
    method: 'POST', body: JSON.stringify({ password: pw })
  });
  const d = await res.json();
  toast(d.ok ? '密码已重置' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
}

async function addDepartment() {
  const name = prompt('输入新科室名称：');
  if (!name || !name.trim()) return;
  const res = await apiFetch('/api/v1/departments', {
    method: 'POST', body: JSON.stringify({ name: name.trim() })
  });
  const d = await res.json();
  toast(d.ok ? '科室已创建' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

// ==================== 审计日志 ====================
let AUTH_PAGE = 1;
const ACTION_LABELS = {
  login_success: '登录成功', login_failed: '登录失败', login_locked: '账号锁定',
  account_created: '创建账号', role_changed: '角色变更', password_reset: '密码重置',
  dept_changed: '科室变更', department_created: '创建科室',
};

async function loadAudit(page) {
  if (page === undefined) page = 1;
  if (page < 1) page = 1;
  AUTH_PAGE = page;
  const action = document.getElementById('auditFilterAction').value;
  const empId = document.getElementById('auditFilterEmpId').value.trim();
  const start = document.getElementById('auditFilterStart')?.value.trim() || '';
  const end = document.getElementById('auditFilterEnd')?.value.trim() || '';
  const params = new URLSearchParams({ page: String(page), page_size: '50' });
  if (action) params.set('action', action);
  if (empId) params.set('emp_id', empId);
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  try {
    const res = await apiFetch('/api/v1/admin/audit-logs?' + params.toString());
    const d = await res.json();
    const data = d.data || {};
    const items = data.items || [];
    const tbody = document.getElementById('auditBody');
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:32px;">暂无记录</td></tr>';
    } else {
      tbody.innerHTML = items.map(it => {
        const label = ACTION_LABELS[it.action] || it.action;
        const detail = it.detail || '';
        const ts = it.ts ? it.ts.replace('T', ' ').substring(0, 19) : '';
        return `<tr>
          <td style="white-space:nowrap;font-size:12px;">${escapeHtml(ts)}</td>
          <td style="white-space:nowrap;">${escapeHtml(it.emp_id)}</td>
          <td style="white-space:nowrap;"><span class="badge badge-info">${escapeHtml(label)}</span></td>
          <td style="font-size:12px;color:var(--text-secondary);max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(detail)}">${escapeHtml(detail) || '--'}</td>
          <td style="white-space:nowrap;font-size:12px;">${escapeHtml(it.ip || '--')}</td>
        </tr>`;
      }).join('');
    }
    // 分页信息
    const pages = data.pages || 1;
    const total = data.total || 0;
    document.getElementById('auditTotalInfo').textContent = '共 ' + total + ' 条';
    document.getElementById('auditPageInfo').textContent = '第 ' + page + ' / ' + pages + ' 页';
    document.getElementById('auditPrevBtn').disabled = page <= 1;
    document.getElementById('auditNextBtn').disabled = page >= pages;
  } catch (e) { console.error('loadAudit failed', e); }
}

function gotoPage(pageName) {
  switchPage(pageName, document.querySelector(`.nav-cell[data-page="${pageName}"]`));
}

export { SEV_META, switchPage, gotoPage, loadUsers, loadAudit };

Object.assign(window, { switchPage, closeSidebar, toggleSidebar, loadUsers, changeUserRole, changeUserDept, resetUserPwd, addDepartment, loadAudit, gotoPage });
