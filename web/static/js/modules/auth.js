import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";
import { switchPage, loadUsers } from "./shell.js";

// ==================== 账号 / 授权（启动闸门） ====================
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

async function loadDisclaimer() {
  try {
    const r = await apiFetch('/api/v1/license/disclaimer');
    const d = await r.json();
    const t = document.getElementById('disclaimerText');
    if (t && d.data) t.textContent = d.data.text || '';
  } catch (e) {}
}

function fillMachineCode() {
  apiFetch('/api/v1/license/machine-code').then(r => r.json()).then(d => {
    const mid = d.data && d.data.machine_id;
    const g = document.getElementById('gateMid'); if (g && mid) g.textContent = mid;
    const s = document.getElementById('setMachineId'); if (s && mid) s.textContent = mid;
  }).catch(() => {});
}

async function gateAccept() {
  setGateErr('gaErr', '');
  try {
    await apiFetch('/api/v1/license/disclaimer', { method: 'POST' });
    bootstrapGate();
  } catch (e) { setGateErr('gaErr', '网络错误：' + e.message); }
}
function gateReject() {
  const t = document.getElementById('disclaimerText');
  if (t) t.innerHTML = '<p style="color:#c0392b">您未同意用户协议，无法使用本软件。请关闭窗口。</p>';
  const b1 = document.getElementById('btnAccept'); if (b1) b1.style.display = 'none';
  const b2 = document.getElementById('btnReject'); if (b2) { b2.textContent = '关闭'; b2.onclick = () => window.close(); }
}

async function gateCreate() {
  setGateErr('gaErr', '');
  const empId = document.getElementById('gaEmpId').value.trim();
  const name  = document.getElementById('gaName').value.trim();
  const pwd   = document.getElementById('gaPwd').value;
  const pwd2  = document.getElementById('gaPwd2').value;
  if (!empId) return setGateErr('gaErr', '请输入工号');
  if (pwd.length < 6) return setGateErr('gaErr', '密码至少 6 位');
  if (pwd !== pwd2) return setGateErr('gaErr', '两次密码不一致');
  try {
    // 登录页自助注册走 /accounts/register（始终 doctor、注册后回登录页）；
    // 首个账号引导仍走 /accounts（自动 admin、创建即进入）。
    const url = _gateRegMode ? '/api/v1/accounts/register' : '/api/v1/accounts';
    const res = await apiFetch(url, {
      method: 'POST', body: JSON.stringify({ emp_id: empId, name, password: pwd })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '创建失败');
    if (_gateRegMode) {
      _gateRegMode = false;
      gateToLogin();
      document.getElementById('glEmpId').value = empId;
      document.getElementById('glPwd').value = '';
      setGateErr('glErr', d.message || '注册成功，请用新账号登录');
      return;
    }
    AUTH.token = d.data.token; AUTH.empId = d.data.emp_id; AUTH.name = d.data.name || '';
    AUTH.role  = d.data.role || 'doctor';
    localStorage.setItem('xy-token', AUTH.token);
    localStorage.setItem('xy-emp', AUTH.empId);
    localStorage.setItem('xy-name', AUTH.name);
    localStorage.setItem('xy-role', AUTH.role);
    applyRoleUI(); enterApp(window.LICENSE_STATUS);
  } catch (e) { setGateErr('gaErr', e.message); }
}

// 登录页步骤切换：注册（复用 gate-account）/ 修改密码 / 返回登录
let _gateRegMode = false;   // true = 登录页自助注册（doctor），false = 首个账号引导

function gateToRegister() {
  _gateRegMode = true;
  setGateErr('gaErr', ''); setGateErr('glErr', '');
  const t = document.getElementById('gaTitle'); if (t) t.textContent = '注册账号';
  const btn = document.getElementById('btnCreate'); if (btn) btn.textContent = '注册';
  const hint = document.querySelector('#gate-account .gate-hint');
  if (hint) hint.textContent = '使用工号注册（默认医生角色），注册后请用新账号登录。';
  gateShow('account');
  const el = document.getElementById('gaEmpId'); if (el) el.focus();
}

function gateToPwd() {
  setGateErr('gpErr', ''); setGateErr('glErr', '');
  ['gpEmpId', 'gpOld', 'gpNew', 'gpNew2'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  gateShow('pwd');
  const el = document.getElementById('gpEmpId'); if (el) el.focus();
}

function gateToLogin() {
  _gateRegMode = false;
  setGateErr('gaErr', ''); setGateErr('gpErr', ''); setGateErr('glErr', '');
  gateShow('login');
  const el = document.getElementById('glEmpId'); if (el) el.focus();
}

async function gateChangePwd() {
  setGateErr('gpErr', '');
  const empId = document.getElementById('gpEmpId').value.trim();
  const oldPwd = document.getElementById('gpOld').value;
  const newPwd = document.getElementById('gpNew').value;
  const newPwd2 = document.getElementById('gpNew2').value;
  if (!empId || !oldPwd) return setGateErr('gpErr', '请输入工号和旧密码');
  if (newPwd.length < 6) return setGateErr('gpErr', '新密码至少 6 位');
  if (newPwd !== newPwd2) return setGateErr('gpErr', '两次新密码不一致');
  try {
    const res = await apiFetch('/api/v1/accounts/change-password', {
      method: 'POST', body: JSON.stringify({ emp_id: empId, old_password: oldPwd, new_password: newPwd })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '修改失败');
    gateToLogin();
    document.getElementById('glEmpId').value = empId;
    document.getElementById('glPwd').value = '';
    setGateErr('glErr', d.message || '密码已修改，请用新密码登录');
  } catch (e) { setGateErr('gpErr', e.message); }
}

async function gateLogin() {
  setGateErr('glErr', '');
  const empId = document.getElementById('glEmpId').value.trim();
  const pwd   = document.getElementById('glPwd').value;
  if (!empId || !pwd) return setGateErr('glErr', '请输入工号和密码');
  try {
    const res = await apiFetch('/api/v1/accounts/login', {
      method: 'POST', body: JSON.stringify({ emp_id: empId, password: pwd })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '登录失败');
    AUTH.token = d.data.token; AUTH.empId = d.data.emp_id; AUTH.name = d.data.name || '';
    AUTH.role  = d.data.role || 'doctor';
    localStorage.setItem('xy-token', AUTH.token);
    localStorage.setItem('xy-emp', AUTH.empId);
    localStorage.setItem('xy-name', AUTH.name);
    localStorage.setItem('xy-role', AUTH.role);
    applyRoleUI(); enterApp(window.LICENSE_STATUS);
  } catch (e) { setGateErr('glErr', e.message); }
}

async function gateActivate() {
  setGateErr('gActErr', '');
  const code = document.getElementById('gActCode').value.trim();
  if (!code) return setGateErr('gActErr', '请输入激活码');
  try {
    const res = await apiFetch('/api/v1/license/activate', {
      method: 'POST', body: JSON.stringify({ code })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '激活失败');
    window.LICENSE_STATUS = d.data;
    enterApp(d.data);
  } catch (e) { setGateErr('gActErr', e.message); }
}

function enterApp(status) {
  showGate(false);
  window.LICENSE_STATUS = status || window.LICENSE_STATUS;
  loadSettings();
  refreshUserUI();
  updateTrialBanner(window.LICENSE_STATUS);
  refreshFeedbackBadge();  // 登录后立即刷新反馈待审徽章
}

function updateTrialBanner(status) {
  const b = document.getElementById('trialBanner');
  if (!b) return;
  if (!status || status.activated) { b.style.display = 'none'; return; }
  if (status.trial_state === 'expired') {
    b.className = 'trial-banner expired';
    b.textContent = '⚠ 免费试用期已结束，请到「设置 → 授权与激活」输入激活码以继续使用。';
    b.style.display = 'block';
  } else if (status.trial_state === 'trial') {
    b.className = 'trial-banner';
    b.textContent = '🕒 免费试用期剩余 ' + status.trial_days_left + ' 天（共 ' + status.trial_days_total + ' 天）';
    b.style.display = status.trial_days_left <= 14 ? 'block' : 'none';
  } else {
    b.style.display = 'none';
  }
}

function refreshUserUI() {
  const av = document.getElementById('userAvatar');
  const nm = document.getElementById('userName');
  const mi = document.getElementById('userMenuName');
  const ms = document.getElementById('userMenuSub');
  const mr = document.getElementById('userMenuStatus');
  const label = AUTH.name || AUTH.empId || '未登录';
  if (av) av.textContent = (label[0] || '?').toUpperCase();
  if (nm) nm.textContent = AUTH.empId || '未登录';
  if (mi) mi.textContent = label;
  if (ms) ms.textContent = AUTH.empId ? '工号 ' + AUTH.empId : '';
  if (mr) {
    const st = window.LICENSE_STATUS;
    if (!st) mr.textContent = '';
    else if (st.activated) mr.textContent = '✅ 已激活';
    else if (st.trial_state === 'trial') mr.textContent = '🕒 试用期 ' + st.trial_days_left + ' 天';
    else mr.textContent = '⚠ 需激活';
  }
  if (AUTH.empId) {
    const u = document.getElementById('mUser'); if (u) u.value = AUTH.empId;
    if (APP_SETTINGS) APP_SETTINGS.emp_id = AUTH.empId;
  }
}

function populateLicenseSettings() {
  fillMachineCode();
  apiFetch('/api/v1/license/status').then(r => r.json()).then(d => {
    window.LICENSE_STATUS = d.data;
    const st = d.data, el = document.getElementById('setLicenseStatus');
    if (el && st) {
      if (st.activated) el.textContent = '✅ 已激活（永久）';
      else if (st.trial_state === 'trial') el.textContent = '🕒 试用期 · 剩余 ' + st.trial_days_left + ' 天';
      else el.textContent = '⚠ 试用期已结束，需激活';
    }
    refreshUserUI();
    updateTrialBanner(st);
  }).catch(() => {});
}

async function bootstrapGate() {
  loadSettings();   // 修复：进入前先加载设置（此前从未在启动期调用）
  _gateRegMode = false;
  const gTitle = document.getElementById('gaTitle');
  if (gTitle) gTitle.textContent = '创建首个账号';
  const gBtn = document.getElementById('btnCreate');
  if (gBtn) gBtn.textContent = '创建并进入';
  const gHint = document.querySelector('#gate-account .gate-hint');
  if (gHint) gHint.textContent = '质控责任到人，请使用您的工号作为登录名。';
  let status = null;
  try {
    const r = await apiFetch('/api/v1/license/status');
    const d = await r.json();
    status = d.data; window.LICENSE_STATUS = status;
  } catch (e) {
    // 后端不可达（开发态）：直接放行，避免锁死界面
    showGate(false); refreshUserUI(); applyRoleUI();
    maybeShowOnboarding();
    return;
  }
  // 已有登录态：跳过登录步骤直接进入
  if (AUTH.token && status.account_count > 0) {
    showGate(false); refreshUserUI(); applyRoleUI(); updateTrialBanner(status);
    maybeShowOnboarding();
    return;
  }
  showGate(true);
  if (!status.disclaimer_accepted) { gateShow('disclaimer'); loadDisclaimer(); return; }
  if (status.trial_state === 'expired' && !status.activated) { gateShow('activation'); fillMachineCode(); return; }
  gateShow(status.account_count === 0 ? 'account' : 'login');
}

// ==================== 首次使用引导 Onboarding ====================
const ONBOARDING_KEY = 'xy-onboarding-done';

function maybeShowOnboarding() {
  if (localStorage.getItem(ONBOARDING_KEY) === '1') return;
  // 延迟到主界面渲染完成
  setTimeout(() => {
    const ov = document.getElementById('onboardingOverlay');
    if (ov) { showOnboarding(); }
  }, 350);
}

function showOnboarding() {
  const ov = document.getElementById('onboardingOverlay');
  if (!ov) return;
  _onbStep = 1;
  ov.style.display = 'flex';
  _renderOnboarding();
}

function closeOnboarding() {
  localStorage.setItem(ONBOARDING_KEY, '1');
  const ov = document.getElementById('onboardingOverlay');
  if (ov) ov.style.display = 'none';
}

// 从设置页重新打开引导
function openOnboardingFromSettings() {
  showOnboarding();
}

let _onbStep = 1;

function _renderOnboarding() {
  document.querySelectorAll('.onboarding-step').forEach(el =>
    el.classList.toggle('active', parseInt(el.dataset.step, 10) === _onbStep));
  document.querySelectorAll('.onb-dot').forEach(el =>
    el.classList.toggle('active', parseInt(el.dataset.dot, 10) === _onbStep));
  const next = document.getElementById('onbNext');
  if (next) {
    if (_onbStep === 3) { next.textContent = '完成'; }
    else { next.textContent = '下一步'; }
  }
}

// 示例报告：加载一份含典型问题的演示报告并直接质控
function _loadOnbDemo() {
  const findings =
    '检查所见：胸廓对称，双肺纹理清晰。右肺上叶见一磨玻离影，大小约8mm，边界清。\n' +
    '左肺下叶见一小结结，直径约4mm。纵隔居中，未见肿大淋巴結。心脏大小正常。\n' +
    '双侧胸腔未见积液。';
  const impression = '右肺上叶磨玻璃影，建议随访。左肺下叶小结节，建议定期复查。';
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (fEl) fEl.value = findings;
  if (iEl) iEl.value = impression;
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = findings.length + ' 字';
  if (ic) ic.textContent = impression.length + ' 字';
  closeOnboarding();
  setTimeout(() => { if (typeof runQC === 'function') runQC(); }, 120);
}

// app.js 在 body 末尾加载，DOM 已就绪，直接绑定（勿用 DOMContentLoaded——此时早已触发）
(function _bindOnboarding() {
  const next = document.getElementById('onbNext');
  const skip = document.getElementById('onbSkip');
  const demo = document.getElementById('onbTryDemo');
  if (next) next.addEventListener('click', function() {
    if (_onbStep >= 3) { closeOnboarding(); }
    else { _onbStep++; _renderOnboarding(); }
  });
  if (skip) skip.addEventListener('click', closeOnboarding);
  if (demo) demo.addEventListener('click', _loadOnbDemo);
})();

function toggleUserMenu(e) {
  e.stopPropagation();
  const m = document.getElementById('userMenu');
  if (m) m.style.display = m.style.display === 'block' ? 'none' : 'block';
}
document.addEventListener('click', () => {
  const m = document.getElementById('userMenu');
  if (m) m.style.display = 'none';
});

function logout() {
  localStorage.removeItem('xy-token');
  localStorage.removeItem('xy-emp');
  localStorage.removeItem('xy-name');
  AUTH.token = ''; AUTH.empId = ''; AUTH.name = '';
  location.reload();
}
function openActivateFromSettings() {
  closeSettings();
  showGate(true); gateShow('activation'); fillMachineCode();
}
function copyMachineId() {
  const el = document.getElementById('setMachineId');
  const txt = el ? el.textContent : '';
  if (navigator.clipboard && txt) {
    navigator.clipboard.writeText(txt).then(
      () => toast('机器码已复制', 'success'),
      () => toast('复制失败，请手动复制', 'error'));
  }
}

export { bootstrapGate };

Object.assign(window, { gateAccept, gateReject, gateCreate, gateToLogin, gateToRegister, gateToPwd, gateChangePwd, gateLogin, gateActivate, toggleUserMenu, logout, copyMachineId, openActivateFromSettings, openOnboardingFromSettings, showGate, gateShow, refreshUserUI, updateTrialBanner, populateLicenseSettings, bootstrapGate, maybeShowOnboarding, showOnboarding, closeOnboarding });
