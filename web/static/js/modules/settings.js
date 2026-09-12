import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, fmtShortcut, comboFromEvent, comboEquals } from "./core.js";

// ==================== 系统设置（真实持久化） ====================
async function loadSettings(applyUI = true) {
  try {
    const res = await apiFetch('/api/v1/settings');
    const data = await res.json();
    if (data.ok) Object.assign(APP_SETTINGS, data.data || {});
  } catch (e) { /* 离线兜底：沿用默认值 */ }
  if (applyUI) {
    // 工号与默认模态回填到质控页
    const u = document.getElementById('mUser');
    if (u && APP_SETTINGS.emp_id) u.value = APP_SETTINGS.emp_id;
    const m = document.getElementById('mModality');
    if (m && !m.value && APP_SETTINGS.default_modality) m.value = APP_SETTINGS.default_modality;
    // 主题
    if (APP_SETTINGS.theme && !localStorage.getItem('xy-theme')) {
      document.documentElement.setAttribute('data-theme', APP_SETTINGS.theme);
    }
    const rf = document.getElementById('ocrRefresh');
    if (rf) rf.checked = !!APP_SETTINGS.screen_refresh_on_ocr;
    updateShortcutHints();
    // 启动时同步已保存的自定义快捷键到全局后台热键（持久化后跨重启生效）
    if (window.pywebview && window.pywebview.api && window.pywebview.api.applyGlobalHotkeys
        && APP_SETTINGS.shortcuts) {
      window.pywebview.api.applyGlobalHotkeys(APP_SETTINGS.shortcuts).catch(function () {});
    }
  }
  return APP_SETTINGS;
}

function openSettings() {
  const s = APP_SETTINGS;
  document.getElementById('setEmpId').value       = s.emp_id || '';
  document.getElementById('setModality').value    = s.default_modality || '';
  document.getElementById('setOcrScore').value    = s.ocr_min_score ?? 0.55;
  document.getElementById('setTheme').value       = document.documentElement.getAttribute('data-theme') || s.theme || 'light';
  document.getElementById('setAutoQC').checked        = !!s.auto_qc_on_ocr;
  document.getElementById('setAutoEnqueue').checked   = !!s.auto_enqueue;
  document.getElementById('setScreenRefresh').checked = !!s.screen_refresh_on_ocr;
  document.getElementById('setAnonymize').checked     = !!s.anonymize;
  document.getElementById('setOcrDynamic').checked    = !!s.ocr_dynamic;
  document.getElementById('setOcrSilent').checked     = !!s.ocr_silent;
  syncClipWatchUI();           // 同步桌面壳「监听剪贴板」开关状态
  renderShortcuts();
  populateLicenseSettings();   // 填授权状态 + 机器码
  if (typeof refreshLicenseInfo === 'function') refreshLicenseInfo();
  if (typeof loadChangelog === 'function') loadChangelog();
  document.getElementById('settingsModal').style.display = 'flex';
}

// 把当前 APP_SETTINGS.shortcuts 渲染到设置页各行
function renderShortcuts() {
  for (const action in SHORTCUT_ACTIONS) _renderShortcutRow(action);
}

function resetShortcuts() {
  APP_SETTINGS.shortcuts = {
    run_qc:       { mods: ['ctrl'], key: 'Enter' },
    save_sample:  { mods: ['ctrl'], key: 's' },
    ocr_capture:  { mods: ['ctrl', 'shift'], key: 'o' },
    toggle_theme: { mods: ['ctrl'], key: 't' },
  };
  renderShortcuts();
  toast('快捷键已恢复默认（保存后生效）', 'info');
}

// 侧边栏速查卡 + 运行按钮 跟随当前配置刷新
function updateShortcutHints() {
  const sc = APP_SETTINGS.shortcuts || {};
  document.querySelectorAll('.tips-key[data-action]').forEach(el => {
    el.textContent = fmtShortcut(sc[el.dataset.action]);
  });
  const run = document.getElementById('btnRunQc');
  const runLabel = document.getElementById('btnRunQcLabel') || run;
  if (runLabel) runLabel.textContent = '运行质控 ' + fmtShortcut(sc.run_qc);
}
function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }

async function saveSettings() {
  const payload = {
    emp_id:                document.getElementById('setEmpId').value.trim() || 'demo01',
    default_modality:      document.getElementById('setModality').value,
    ocr_min_score:         parseFloat(document.getElementById('setOcrScore').value) || 0.55,
    theme:                 document.getElementById('setTheme').value,
    auto_qc_on_ocr:        document.getElementById('setAutoQC').checked,
    auto_enqueue:          document.getElementById('setAutoEnqueue').checked,
    screen_refresh_on_ocr: document.getElementById('setScreenRefresh').checked,
    anonymize:             document.getElementById('setAnonymize').checked,
    ocr_dynamic:           document.getElementById('setOcrDynamic').checked,
    ocr_silent:            document.getElementById('setOcrSilent').checked,
    shortcuts:            APP_SETTINGS.shortcuts || {},
  };
  try {
    const res = await apiFetch('/api/v1/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    Object.assign(APP_SETTINGS, data.data || payload);
    // 立即生效：主题 / 工号 / 抓屏开关
    document.documentElement.setAttribute('data-theme', payload.theme);
    localStorage.setItem('xy-theme', payload.theme);
    const t = document.getElementById('themeToggle');
    if (t) t.textContent = payload.theme === 'dark' ? '☀️' : '🌙';
    const u = document.getElementById('mUser'); if (u) u.value = payload.emp_id;
    const rf = document.getElementById('ocrRefresh'); if (rf) rf.checked = payload.screen_refresh_on_ocr;
    updateShortcutHints();
    // 同步自定义快捷键到桌面壳全局后台热键（PACS 聚焦时也能用新组合）
    if (window.pywebview && window.pywebview.api && window.pywebview.api.applyGlobalHotkeys) {
      window.pywebview.api.applyGlobalHotkeys(payload.shortcuts || {}).catch(function () {});
    }
    closeSettings();
    toast('设置已保存并生效', 'success');
  } catch (e) { toast('保存设置失败: ' + e.message, 'error'); }
}

// ==================== 全局快捷键（可配置，默认 Windows Ctrl+ 风） ====================
const SHORTCUT_ACTIONS = {
  run_qc:       { label: '运行质控',     run: () => runQC() },
  save_sample:  { label: '存入样本库',   run: () => saveToLibrary() },
  paste_split:  { label: '粘贴全文并分栏', run: () => pasteAndSplit() },
  ocr_capture:  { label: '识别并质控',   run: () => {
    if (document.getElementById('ocrModal').style.display === 'flex') ocrPipeline();
    else ocrOneClick();
  } },
  toggle_theme: { label: '明暗主题切换', run: () => toggleTheme() },
};


let _capturingShortcut = null;   // 设置页“按下新快捷键”捕获中

document.addEventListener('keydown', function(e) {
  // 设置页正在捕获：记录组合键并写回该动作，不触发业务动作
  if (_capturingShortcut) {
    e.preventDefault();
    if (e.key === 'Escape') { _cancelCapture(); return; }
    const sc = comboFromEvent(e);
    APP_SETTINGS.shortcuts = APP_SETTINGS.shortcuts || {};
    APP_SETTINGS.shortcuts[_capturingShortcut] = sc;
    _renderShortcutRow(_capturingShortcut);
    _endCapture();
    return;
  }
  const evt = comboFromEvent(e);
  const sc = APP_SETTINGS.shortcuts || {};
  // 焦点过滤：在输入框/文本域内，仅当动作归属当前页面才触发，
  // 防止设置页、规则维护页输入时误触「运行质控/入库/粘贴分栏」。
  const t = e.target;
  const inField = !!(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable));
  const qcActive = !!(document.getElementById('page-qc') && document.getElementById('page-qc').classList.contains('active'));
  const QC_ONLY_ACTIONS = { run_qc: 1, save_sample: 1, paste_split: 1 };
  for (const action in SHORTCUT_ACTIONS) {
    if (!comboEquals(sc[action], evt)) continue;
    // 焦点在输入框、且动作是质控页专属、且当前不在质控页 → 不吞事件、不触发
    if (inField && QC_ONLY_ACTIONS[action] && !qcActive) continue;
    e.preventDefault();
    SHORTCUT_ACTIONS[action].run();
    return;
  }
  // Esc 为固定行为：先关 OCR 模态，否则在工作区清空录入
  if (e.key === 'Escape') {
    const cm = document.getElementById('confirmModal');
    if (cm && cm.style.display === 'flex') { e.preventDefault(); confirmModalResolve(false); return; }
    if (document.getElementById('ocrModal').style.display === 'flex') closeOcrModal();
    else if (document.getElementById('page-qc').classList.contains('active')) clearInput();
  }
});

function _startCapture(action, btn) {
  _capturingShortcut = action;
  document.querySelectorAll('.sc-rebind').forEach(b => b.classList.remove('capturing'));
  btn.classList.add('capturing');
  btn.textContent = '按下新快捷键…';
}
function _endCapture() {
  _capturingShortcut = null;
  document.querySelectorAll('.sc-rebind').forEach(b => {
    b.classList.remove('capturing');
    const a = b.dataset.action;
    b.textContent = '重绑';
  });
}
function _cancelCapture() {
  const btn = document.querySelector('.sc-rebind.capturing');
  _endCapture();
}
function _renderShortcutRow(action) {
  const cell = document.querySelector('.sc-key[data-action="' + action + '"]');
  if (cell) cell.textContent = fmtShortcut((APP_SETTINGS.shortcuts || {})[action]);
}

Object.assign(window, { openSettings, closeSettings, saveSettings, resetShortcuts, _startCapture, loadSettings, renderShortcuts, updateShortcutHints });
