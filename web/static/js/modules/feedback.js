import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";

// ===================== 反馈审核（R19 闭环） =====================
function escHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

async function loadFeedback() {
  const body = document.getElementById('feedbackBody');
  const applyBtn = document.getElementById('fbApplyBtn');
  body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px;">加载中...</td></tr>';
  try {
    const st = await (await apiFetch('/api/v1/feedback/stats')).json();
    const stats = (st && st.data) || {};
    document.getElementById('fbStats').textContent =
      `待审 ${stats.pending || 0} · 已审 ${stats.reviewed || 0}（真错字 ${stats.true_typo || 0} / 误报 ${stats.false_positive || 0} / 跳过 ${stats.skipped || 0}）`;
    refreshFeedbackBadge();
    // 应用按钮：有 delta 才显示（先查 stats，apply dry 不做——直接查接口可用性简化）
    applyBtn.style.display = 'none';

    const res = await (await apiFetch('/api/v1/feedback/pending?limit=0')).json();
    const items = (res && res.data) || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px;">🎉 暂无待审核反馈</td></tr>';
      return;
    }
    body.innerHTML = items.map(it => {
      const ctx = escHtml((it.report_snippet || '').slice(0, 60));
      const explainItems = Array.isArray(it.explain) ? it.explain.map(x => String(x || '')).filter(Boolean) : [];
      const explainText = explainItems.join('\n');
      const explainCell = explainItems.length
        ? `<div class="fb-explain" title="${escHtml(explainText)}">
            <div class="fb-explain-preview">${escHtml(explainItems[0])}</div>
            ${explainItems.length > 1 ? `<details class="fb-explain-more">
              <summary>+${explainItems.length - 1} 条依据</summary>
              <div class="fb-explain-detail">${escHtml(explainItems.slice(1).join('\n'))}</div>
            </details>` : ''}
          </div>`
        : '<span style="color:var(--text-muted)">—</span>';
      return `<tr>
        <td style="white-space:nowrap">${escHtml(it.rule_id)}</td>
        <td><b>「${escHtml(it.seg)}」</b></td>
        <td>${it.suggestion ? escHtml(it.suggestion) : '<span style="color:var(--text-muted)">—</span>'}</td>
        <td style="font-size:12px;color:var(--text-secondary);max-width:260px;">${explainCell}</td>
        <td style="font-size:12px;color:var(--text-muted);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${ctx}">${ctx}</td>
        <td style="white-space:nowrap;font-size:12px;color:var(--text-muted)">${escHtml((it.timestamp || '').slice(5, 16))}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm btn-primary" onclick="reviewFeedback('${escHtml(it.id)}','y')">✓ 真错字</button>
          <button class="btn btn-sm btn-outline" onclick="reviewFeedback('${escHtml(it.id)}','n')">✗ 误报</button>
          <button class="btn btn-sm btn-outline" onclick="reviewFeedback('${escHtml(it.id)}','s')">? 跳过</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#c0392b;padding:24px;">加载失败：${escHtml(e.message)}</td></tr>`;
  }
}

async function reviewFeedback(itemId, verdict) {
  if (!itemId) return;
  try {
    const res = await (await apiFetch('/api/v1/feedback/review', {
      method: 'POST',
      body: JSON.stringify({ item_id: itemId, verdict, note: '网页审核' }),
    })).json();
    if (!res || !res.ok) {
      alert((res && res.message) || '审核失败');
      return;
    }
    loadFeedback();  // 刷新列表
  } catch (e) {
    alert('审核请求失败：' + e.message);
  }
}

async function applyFeedbackDelta() {
  if (!(await confirmAction('应用误报词', '把已确认的误报词写入 user_whitelist.json（即时生效）？', false, '应用'))) return;
  try {
    const res = await (await apiFetch('/api/v1/feedback/apply?dry_run=false', { method: 'POST' })).json();
    if (!res || !res.ok) { alert((res && res.message) || '应用失败'); return; }
    const d = (res && res.data) || {};
    alert(`已应用：补入 ${(d.added || []).length} 条、移除 ${(d.removed || []).length} 条`);
    loadFeedback();
  } catch (e) {
    alert('应用失败：' + e.message);
  }
}

async function refreshFeedbackBadge() {
  try {
    const res = await (await apiFetch('/api/v1/feedback/stats')).json();
    const n = (res && res.data && res.data.pending) || 0;
    const badge = document.getElementById('navFeedbackBadge');
    if (badge) { badge.textContent = n; badge.style.display = n > 0 ? '' : 'none'; }
  } catch (e) { /* 静默 */ }
}

Object.assign(window, { loadFeedback, applyFeedbackDelta, reviewFeedback });
