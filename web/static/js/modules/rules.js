import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";
import { _qcAllFindings } from "./qc.js";

// ==================== 规则词表维护（R8 错别字 / R9 矛盾对 / 忽略词 / R10 模板） ====================

/** P0 一键采纳修正闭环：把某条确定性错别字的 suggestion 应用进报告文本并重新质控。
 *  实现：报告全文 = 描述+结论 拼接（与 /api/v1/qc/check 的 report 一致），span 直接定位替换，
 *  替换后按段重新分栏填入输入框，再自动跑一遍质控刷新结果。
 *  注意：其他发现（矛盾/缺失类）无安全替换值，不做自动改动。 */
async function applyFindingFix(findIdx) {
  const f = (_qcAllFindings || [])[findIdx];
  if (!f) { toast('未找到该发现', 'error'); return; }
  const span = Array.isArray(f.span) ? f.span : [-1, -1];
  const sug = (f.suggestion || '').trim();
  const wrong = (f.snippet || '').trim();
  if (span[0] < 0 || span[1] <= span[0] || !sug || !wrong) {
    toast('该问题无安全修正值，请人工修改', 'warn');
    return;
  }
  // 报告全文必须以最新输入框内容为准（用户可能改过）
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) return;
  const full = [fEl.value, iEl.value].filter(Boolean).join('\n');
  if (span[1] > full.length) { toast('原文已变化，请重新运行质控', 'warn'); return; }
  const fixedText = full.slice(0, span[0]) + sug + full.slice(span[1]);
  // 分栏回填：splitReportSections 兼容描述在前/诊断在后两种顺序
  const parts = splitReportSections(fixedText);
  fEl.value = parts.findings;
  iEl.value = parts.impression;
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = parts.findings.length + ' 字';
  if (ic) ic.textContent = parts.impression.length + ' 字';
  toast(`已应用修正：「${wrong}」→「${sug}」，正在重新质控...`, 'success');
  await runQC();
}

/** 一键采纳全部修正：调用引擎 auto_fix 批量应用所有确定性错别字修正并重新质控。 */
async function applyAllFixes() {
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) return;
  const report = [fEl.value, iEl.value].filter(Boolean).join('\n');
  if (!report.trim()) { toast('请输入报告内容', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/check', {
      method: 'POST',
      body: JSON.stringify({ report, meta: currentQcMeta(), auto_fix: true })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '执行失败');
    const fixed = data.data.fixed;
    if (!fixed || !fixed.fixed_text || fixed.fixed_text === report) {
      toast('没有可自动修正的错别字', 'info');
      return;
    }
    const parts = splitReportSections(fixed.fixed_text);
    fEl.value = parts.findings;
    iEl.value = parts.impression;
    const fc = document.getElementById('findingsCount');
    const ic = document.getElementById('impressionCount');
    if (fc) fc.textContent = parts.findings.length + ' 字';
    if (ic) ic.textContent = parts.impression.length + ' 字';
    toast(`已自动修正 ${fixed.n_fixed} 处错别字${fixed.n_manual ? '，另有 ' + fixed.n_manual + ' 处需人工确认' : ''}，正在重新质控...`, 'success');
    await runQC();
  } catch (e) {
    toast('应用修正失败: ' + e.message, 'error');
  }
}

/** 收集当前元信息（供 auto_fix 请求复用，与 runQC 的 meta 保持一致） */
function currentQcMeta() {
  const el = id => { const e = document.getElementById(id); return e ? e.value : ''; };
  return {
    patient: el('mPatient'), gender: el('mGender'), age: el('mAge'),
    modality: el('mModality'), applied_site: el('mSite'),
    laterality: effectiveLaterality(), user_id: el('mUser') || APP_SETTINGS.emp_id,
  };
}

/** P0 修正反馈闭环：QC 结果里点「采纳修正」→ 错词→正确词写入规则库 */
async function learnTypoFromFinding(btn) {
  const wrong = (btn && btn.dataset.wrong || '').trim();
  const correct = (btn && btn.dataset.correct || '').trim();
  if (!wrong || !correct) { toast('缺少错词/正确词，无法学习', 'error'); return; }
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/v1/qc/rules/learn-typo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '学习失败');
    btn.textContent = '✓ 已学习';
    btn.classList.add('done');
    toast(`已学习：${wrong} → ${correct}，下次自动识别`, 'success');
  } catch (err) {
    btn.disabled = false;
    toast('学习失败：' + (err.message || err), 'error');
  }
}

/** P0 历史报告词频学习：扫描样本库发现候选错字并展示，一键采纳 */
let _scanCandidates = [];
async function scanReportsForTypos() {
  const box = document.getElementById('scanResult');
  const btn = document.getElementById('scanBtn');
  if (!box) return;
  btn && (btn.disabled = true);
  btn && (btn.textContent = '扫描中…（读取最近 200 份报告）');
  box.innerHTML = '<div class="muted">正在扫描样本库并统计词频…</div>';
  try {
    const res = await apiFetch('/api/v1/qc/rules/scan-reports', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '扫描失败');
    _scanCandidates = (data.data && data.data.candidates) || [];
    if (_scanCandidates.length === 0) {
      box.innerHTML = '<div class="muted">未发现候选错字（样本库为空或均为标准写法）。入库的报告越多，学习越准。</div>';
    } else {
      box.innerHTML = `<div class="scan-head">发现 ${_scanCandidates.length} 个候选错字（来自历史报告词频+读音比对）</div>` +
        `<div class="scan-list">` + _scanCandidates.map((c, i) => `
          <div class="scan-item">
            <span class="scan-word">${escapeHtml(c.wrong)}</span>
            <span class="scan-arrow">→</span>
            <span class="scan-word ok">${escapeHtml(c.correct)}</span>
            <span class="scan-count">${c.count}次</span>
            <span class="scan-reason" title="${escapeHtml(c.reason)}">${escapeHtml(c.reason)}</span>
            <button class="btn btn-xs learn-btn" onclick="adoptScanCandidate(this, ${i})">采纳</button>
          </div>`).join('') + `</div>`;
    }
  } catch (err) {
    box.innerHTML = '<div class="muted">扫描失败：' + escapeHtml(err.message || err) + '</div>';
  } finally {
    btn && (btn.disabled = false);
    btn && (btn.textContent = '🔄 扫描历史报告学习');
  }
}

/** 采纳一个扫描候选错字 → 写入规则库 */
async function adoptScanCandidate(btn, idx) {
  const c = _scanCandidates[idx];
  if (!c) return;
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/v1/qc/rules/learn-typo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong: c.wrong, correct: c.correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '学习失败');
    btn.textContent = '✓';
    btn.classList.add('done');
    toast(`已学习：${c.wrong} → ${c.correct}`, 'success');
  } catch (err) {
    btn.disabled = false;
    toast('采纳失败：' + (err.message || err), 'error');
  }
}

// 规则词表维护界面的数据快照（用于恢复默认/取消编辑）
let _cfgSnapshot = null;

async function loadRulesConfig(silent = false) {
  try {
    const res = await apiFetch('/api/v1/qc/rules/config');
    const data = await res.json();
    const cfg = (data.data || {});
    const typos = cfg.typos || {};
    // 错字表按错词排序，长表更易查
    document.getElementById('cfgTypos').value =
      Object.keys(typos).sort((a, b) => a.localeCompare(b, 'zh'))
        .map(k => `${k}=${typos[k]}`).join('\n');
    const conflicts = cfg.conflicts || [];
    document.getElementById('cfgConflicts').value =
      conflicts
        .map(c => {
          const a = Array.isArray(c) ? c[0] : (c.a || '').trim();
          const b = Array.isArray(c) ? c[1] : (c.b || '').trim();
          const scope = (!Array.isArray(c) && c.scope) || '正文';
          if (!a || !b || a === b) return '';
          // 范围默认正文时省略前缀，保持简洁；非正文显示 [范围]
          return scope === '正文' ? `${a}|${b}` : `[${scope}] ${a}|${b}`;
        })
        .filter(Boolean)
        .sort((x, y) => x.localeCompare(y, 'zh'))
        .join('\n');
    const ignores = cfg.ignores || [];
    document.getElementById('cfgIgnores').value = ignores.map(String).sort((a, b) => a.localeCompare(b, 'zh')).join('\n');
    const tpl = cfg.template || {};
    document.getElementById('cfgTplFollowup').checked = !!tpl.require_followup;
    // R19 错字检测（读音相似 + 形近字，高频词组锚定）；默认开启
    const r19El = document.getElementById('cfgR19');
    if (r19El) r19El.checked = cfg.enable_r19 !== false;
    // R19 灵敏度（低/中/高）
    const sensEl = document.getElementById('cfgR19Sens');
    if (sensEl) sensEl.value = ['low', 'medium', 'high'].includes(cfg.r19_sensitivity) ? cfg.r19_sensitivity : 'medium';
    // 快照：供「恢复默认/撤销编辑」使用
    _cfgSnapshot = {
      typos: document.getElementById('cfgTypos').value,
      conflicts: document.getElementById('cfgConflicts').value,
      ignores: document.getElementById('cfgIgnores').value,
    };
    _typoCache = Object.assign({}, typos);
    _typoDisabled = new Set(cfg.disabled_typos || []);
    updateCfgStats();   // 统计规则条数
    renderTypoTable();  // 可视化词库表
    if (!silent) toast('规则配置已载入', 'success');
  } catch (e) {
    if (!silent) toast('载入规则配置失败: ' + e.message, 'error');
  }
}

// 实时统计各词表条数（供界面展示）
function updateCfgStats() {
  const count = (txt) => txt.split('\n').map(s => s.trim()).filter(Boolean).length;
  const el = (id) => document.getElementById(id);
  const nTypos = count(el('cfgTypos').value || '');
  const nConf = count(el('cfgConflicts').value || '');
  const nIg = count(el('cfgIgnores').value || '');
  const stat = el('cfgStats');
  if (stat) stat.textContent = `共 ${nTypos} 条错字 · ${nConf} 条矛盾对 · ${nIg} 条忽略词`;
}

// 撤销本次编辑，回到上次载入的配置
function revertRulesConfig() {
  if (!_cfgSnapshot) { toast('尚无快照，请先载入配置', 'warn'); return; }
  document.getElementById('cfgTypos').value = _cfgSnapshot.typos;
  document.getElementById('cfgConflicts').value = _cfgSnapshot.conflicts;
  document.getElementById('cfgIgnores').value = _cfgSnapshot.ignores;
  updateCfgStats();
  toast('已撤销本次编辑', 'info');
}

async function saveRulesConfig() {
  // 错字表解析：每行「错词=正词」；格式错误（缺=号、空值）行计数并提示
  const typoLines = document.getElementById('cfgTypos').value.split('\n').map(s => s.trim()).filter(Boolean);
  const typos = {};
  let nTypoBad = 0;
  for (const line of typoLines) {
    const i = line.indexOf('=');
    const k = line.slice(0, i).trim(), v = line.slice(i + 1).trim();
    if (i <= 0 || !k || !v || k === v) { nTypoBad++; continue; }
    typos[k] = v;
  }
  if (nTypoBad > 0) toast(`有 ${nTypoBad} 行错字格式无效已跳过（应为 错词=正词 且错≠正）`, 'warn');
  // R9 矛盾对解析。支持两种格式：
  //   词A|词B                                  → 范围=正文（默认，整篇互斥）
  //   [范围] 词A|词B                          → 指定范围，范围 ∈ {正文, 描述段, 结论段, 同一句, 描述vs结论}
  // A≠B 且都非空；空行/只有一列/自反(A==B)跳过，避免生成『A 与 A 互斥』的误报规则。
  const _SCOPES = ['正文', '描述段', '结论段', '同一句', '描述vs结论'];
  const rawLines = document.getElementById('cfgConflicts').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const conflicts = [];
  let nBad = 0;
  for (const ln of rawLines) {
    let scope = '正文', body = ln;
    const m = ln.match(/^\[(.+?)\]\s*(.*)$/);
    if (m) {
      scope = m[1].trim();
      body = m[2].trim();
      if (!_SCOPES.includes(scope)) { console.warn('未知范围，已跳过：' + ln); nBad++; continue; }
    }
    const p = body.split('|').map(s => s.trim()).filter(Boolean);
    if (p.length < 2) { console.warn('矛盾对格式应为 词A|词B，已跳过：' + ln); nBad++; continue; }
    const a = p[0], b = p[1];
    if (a === b) { console.warn('矛盾对 A 与 B 相同，已跳过：' + ln); nBad++; continue; }
    conflicts.push({ a, b, scope });
  }
  if (nBad > 0) {
    toast(`有 ${nBad} 行矛盾对格式无效已跳过（每行应为 [范围] 词A|词B，范围∈{${_SCOPES.join('/')}}）`, 'warn');
  }
  const ignores = document.getElementById('cfgIgnores').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const tpl = { require_followup: document.getElementById('cfgTplFollowup').checked };
  const r19El = document.getElementById('cfgR19');
  const enable_r19 = r19El ? r19El.checked : true;
  const sensEl = document.getElementById('cfgR19Sens');
  const r19_sensitivity = (sensEl && ['low', 'medium', 'high'].includes(sensEl.value)) ? sensEl.value : 'medium';
  try {
    const res = await apiFetch('/api/v1/qc/rules/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ typos, conflicts, ignores, template: tpl, enable_r19, r19_sensitivity,
                             disabled_typos: Array.from(_typoDisabled) })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    // 保存成功后刷新快照与统计（保持「撤销」基准与已保存一致）
    _cfgSnapshot = {
      typos: document.getElementById('cfgTypos').value,
      conflicts: document.getElementById('cfgConflicts').value,
      ignores: document.getElementById('cfgIgnores').value,
    };
    updateCfgStats();
    toast(`规则配置已保存并生效（错字 ${Object.keys(typos).length} 条 / 矛盾对 ${conflicts.length} 条 / 忽略词 ${ignores.length} 条）`, 'success');
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  }
}

// ==================== 错别字词库可视化维护（R8：搜索/新增/启停/删除/批量导入） ====================
let _typoDisabled = new Set();
let _typoCache = {};   // {wrong: correct}

function renderTypoTable() {
  const tbody = document.getElementById('typoTableBody');
  const cntEl = document.getElementById('typoCount');
  if (!tbody) return;
  const kw = (document.getElementById('typoSearch').value || '').trim();
  const keys = Object.keys(_typoCache).sort((a, b) => a.localeCompare(b, 'zh'));
  const rows = keys.filter(k => !kw || k.includes(kw) || _typoCache[k].includes(kw));
  if (cntEl) cntEl.textContent = `${rows.length} / ${keys.length} 条`;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:16px;">'
      + (keys.length ? '没有匹配的词条' : '词库为空，点「＋ 新增」或「📥 批量导入」添加') + '</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(k => {
    const on = !_typoDisabled.has(k);
    return `<tr>
      <td><span class="typo-status ${on ? 'on' : 'off'}" title="${on ? '已启用' : '已停用'}"></span></td>
      <td>${escapeHtml(k)}</td>
      <td>${escapeHtml(_typoCache[k] || '')}</td>
      <td style="white-space:nowrap;">
        <button class="btn btn-xs ${on ? 'typoff-btn' : 'apply-fix-btn'}" onclick="toggleTypoItem('${escapeHtml(k)}', ${on})">${on ? '停用' : '启用'}</button>
        <button class="btn btn-xs danger-outline" onclick="deleteTypoItem('${escapeHtml(k)}')">删除</button>
      </td>
    </tr>`;
  }).join('');
}

function openTypoAddModal() {
  document.getElementById('typoAddWrong').value = '';
  document.getElementById('typoAddCorrect').value = '';
  document.getElementById('typoAddModal').style.display = 'flex';
}
function closeTypoAddModal() { document.getElementById('typoAddModal').style.display = 'none'; }

async function addTypoItem() {
  const wrong = document.getElementById('typoAddWrong').value.trim();
  const correct = document.getElementById('typoAddCorrect').value.trim();
  if (!wrong || !correct || wrong === correct) { toast('错词与正词均必填且不能相同', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '新增失败');
    _typoCache[wrong] = correct;
    _typoDisabled.delete(wrong);
    renderTypoTable();
    closeTypoAddModal();
    _refreshCfgTextarea();
    toast(data.message || '已新增', 'success');
  } catch (e) { toast('新增失败: ' + e.message, 'error'); }
}

async function toggleTypoItem(wrong, currentOn) {
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/toggle', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct: currentOn ? '0' : '1' })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '操作失败');
    if (currentOn) _typoDisabled.add(wrong); else _typoDisabled.delete(wrong);
    renderTypoTable();
    toast(data.message || '已更新', 'success');
  } catch (e) { toast('操作失败: ' + e.message, 'error'); }
}

async function deleteTypoItem(wrong) {
  if (!(await confirmAction('删除错字词条', `确定删除错字词条「${wrong}」吗？`, true, '删除'))) return;
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '删除失败');
    delete _typoCache[wrong];
    _typoDisabled.delete(wrong);
    renderTypoTable();
    _refreshCfgTextarea();
    toast(data.message || '已删除', 'success');
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

function openTypoImportModal() {
  document.getElementById('typoImportText').value = '';
  document.getElementById('typoImportModal').style.display = 'flex';
}
function closeTypoImportModal() { document.getElementById('typoImportModal').style.display = 'none'; }

async function importTypoItems() {
  const raw = document.getElementById('typoImportText').value;
  const items = raw.split('\n').map(s => s.trim()).filter(Boolean).map(line => {
    const i = line.indexOf('=');
    if (i <= 0) return null;
    return [line.slice(0, i).trim(), line.slice(i + 1).trim()];
  }).filter(Boolean);
  if (!items.length) { toast('请输入至少一条 错词=正词', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/batch-import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '导入失败');
    for (const [w, c] of items) { if (c) { _typoCache[w] = c; _typoDisabled.delete(w); } }
    renderTypoTable();
    closeTypoImportModal();
    _refreshCfgTextarea();
    toast(data.message || `成功 ${data.data.ok} 条`, 'success');
  } catch (e) { toast('导入失败: ' + e.message, 'error'); }
}

/** 词库变更后，同步更新左侧 textarea（保持两种维护方式一致） */
function _refreshCfgTextarea() {
  const ta = document.getElementById('cfgTypos');
  if (!ta) return;
  ta.value = Object.keys(_typoCache).sort((a, b) => a.localeCompare(b, 'zh'))
    .map(k => `${k}=${_typoCache[k]}`).join('\n');
  updateCfgStats();
}

// 恢复默认规则库（内置出厂配置），操作前需确认
async function resetRulesConfig() {
  if (!(await confirmAction('恢复默认规则库', '确定恢复默认规则库吗？当前所有自定义规则将被出厂配置覆盖。', true, '恢复默认'))) return;
  try {
    const res = await apiFetch('/api/v1/qc/rules/config/reset', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '恢复失败');
    _cfgSnapshot = null;
    await loadRulesConfig();
    toast('已恢复默认规则库', 'success');
  } catch (e) {
    toast('恢复失败: ' + e.message, 'error');
  }
}

// ==================== 规则维护 ====================
async function loadRules() {
  try {
    const res = await apiFetch('/api/v1/qc/rules');
    const data = await res.json();
    const rules = (data.data||[]);

    const tbody = document.getElementById('rulesBody');
    if (!rules.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:32px;">无规则数据</td></tr>';
      return;
    }

    tbody.innerHTML = rules.map(r => `
      <tr>
        <td style="font-family:monospace;font-size:12px;">${r.rule_id||'--'}</td>
        <td>${r.name||'--'}</td>
        <td><span class="tag info">${r.category||'--'}</span></td>
        <td><span class="tag ${r.severity==='critical'?'danger':r.severity==='warning'?'warning':'info'}">${r.severity||'info'}</span></td>
        <td><span class="tag ${r.enabled!==false?'success':'warning'}">${r.enabled!==false?'启用':'禁用'}</span></td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

export { currentQcMeta, applyFindingFix, applyAllFixes };

Object.assign(window, { applyAllFixes, applyFindingFix, learnTypoFromFinding, loadRules, loadRulesConfig, updateCfgStats, revertRulesConfig, saveRulesConfig, resetRulesConfig, renderTypoTable, openTypoAddModal, closeTypoAddModal, addTypoItem, toggleTypoItem, deleteTypoItem, openTypoImportModal, closeTypoImportModal, importTypoItems, scanReportsForTypos, adoptScanCandidate });
