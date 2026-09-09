#!/usr/bin/env python3
"""
split_modules.py — 把 3410 行的 app.js 拆成 ES 模块。

运行: python3 split_modules.py
输出: web/static/js/modules/*.js  +  web/static/js/app.js (入口)
"""
import os, re, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
JS_DIR = os.path.join(BASE, 'web', 'static', 'js')
MODULES_DIR = os.path.join(JS_DIR, 'modules')
APP_JS = os.path.join(JS_DIR, 'app.js')
BACKUP_JS = os.path.join(JS_DIR, 'app.js.bak')

# ── 1. 读入源文件 ─────────────────────────────────────────────
with open(APP_JS, 'r', encoding='utf-8') as f:
    SRC = f.readlines()  # 0-indexed list of lines

def extract(start, end):
    """提取 1-indexed inclusive 行区间，返回拼接字符串。"""
    return ''.join(SRC[start - 1 : end])

def blank():
    return '\n'

# ── 2. 模块定义 ────────────────────────────────────────────────

MODULES = [
    {
        'name': 'core',
        'sections': [(1, 50), (205, 252), (1291, 1304), (2274, 2305), (2677, 2682), (2857, 2887)],
        'imports': [],
        'exports': ['apiFetch', 'toast', 'confirmAction', 'confirmModalResolve',
                     'escapeHtml', 'setVal', 'fmtShortcut', 'comboFromEvent',
                     'comboEquals', 'animateNumber', 'easeOutCubic', 'toggleTheme',
                     'APP_SETTINGS', 'AUTH', 'PAGE_TITLES'],
        'window': ['confirmModalResolve', 'toggleTheme', 'APP_SETTINGS', 'AUTH'],
        'transforms': [
            ('let LICENSE_STATUS = null;', 'window.LICENSE_STATUS = null;'),
        ],
    },
    {
        'name': 'shell',
        'sections': [(52, 203)],
        'imports': ['import { toast, confirmAction, escapeHtml, APP_SETTINGS, AUTH, PAGE_TITLES } from "./core.js";'],
        'exports': ['SEV_META', 'switchPage', 'gotoPage', 'loadUsers'],
        'window': ['switchPage', 'closeSidebar', 'toggleSidebar', 'loadUsers',
                    'changeUserRole', 'changeUserDept', 'resetUserPwd',
                    'addDepartment', 'gotoPage'],
        'transforms': [],
    },
    {
        'name': 'qc',
        'sections': [(254, 295), (297, 688), (918, 1031)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal, fmtShortcut, comboFromEvent, comboEquals } from "./core.js";',
            'import { SEV_META } from "./shell.js";',
        ],
        'exports': ['effectiveLaterality', 'splitReportSections', 'runQC',
                     'saveToLibrary', '_qcAllFindings'],
        'window': ['runQC', 'clearInput', 'pasteAndSplit', 'saveToLibrary',
                    'showQcTab', 'setSevFilter', 'toggleClipWatch',
                    'syncClipWatchUI', 'onClipboardCopy'],
        'transforms': [],
    },
    {
        'name': 'data',
        # (1089,1290)+(1305,1577) 跳过 core.js 的 animateNumber/easeOutCubic(1291-1304)
        'sections': [(804, 916), (1033, 1087), (1089, 1290), (1305, 1577), (1588, 1607), (2048, 2069)],
        'imports': [
            'import { toast, apiFetch, confirmAction, escapeHtml, APP_SETTINGS, AUTH, animateNumber, setVal } from "./core.js";',
            'import { SEV_META, gotoPage } from "./shell.js";',
            'import { effectiveLaterality, splitReportSections, _qcAllFindings } from "./qc.js";',
            'import { currentQcMeta } from "./rules.js";',
        ],
        'exports': ['loadQueue', 'enqueueCurrent', 'enqueueText'],
        'window': ['loadQueue', 'queueRunAll', 'queueClear', 'queueLoad',
                    'queueRemove', 'enqueueCurrent', 'loadSamples',
                    'loadStatsReport', 'viewSample', 'closeSampleModal',
                    'deleteSample', 'exportSamples', 'importSamples',
                    'closeExportFmtModal', 'pickExportFmt', 'exportQcReport',
                    'loadDashboard', 'exportSampleReport', 'downloadExportedFile',
                    'loadErrorTypes', 'loadTrend', 'loadSampleToWorkspace',
                    'renderStatsReport', 'renderModalityChart', 'renderRecentTable'],
        'transforms': [
            ('let ACTIVE_QUEUE_ID = null;', 'window.ACTIVE_QUEUE_ID = null;'),
        ],
    },
    {
        'name': 'rules',
        'sections': [(1609, 2046), (2249, 2272)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";',
            'import { _qcAllFindings } from "./qc.js";',
        ],
        'exports': ['currentQcMeta', 'applyFindingFix', 'applyAllFixes'],
        'window': ['applyAllFixes', 'applyFindingFix', 'learnTypoFromFinding',
                    'loadRules', 'loadRulesConfig', 'updateCfgStats',
                    'revertRulesConfig', 'saveRulesConfig', 'resetRulesConfig',
                    'renderTypoTable', 'openTypoAddModal', 'closeTypoAddModal',
                    'addTypoItem', 'toggleTypoItem', 'deleteTypoItem',
                    'openTypoImportModal', 'closeTypoImportModal',
                    'importTypoItems', 'scanReportsForTypos', 'adoptScanCandidate'],
        'transforms': [],
    },
    {
        'name': 'ocr',
        'sections': [(2308, 2675), (2684, 2842)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal } from "./core.js";',
            'import { splitReportSections } from "./qc.js";',
        ],
        'exports': ['ocrOneClick', 'ocrPipeline'],
        'window': ['openOcrModal', 'closeOcrModal', 'ocrLoadFile',
                    'ocrGrabScreen', 'ocrSaveRegions', 'ocrRecognize',
                    'ocrResetBoxes', 'ocrPipeline', 'ocrOneClick', 'ocrHotkey'],
        'transforms': [],
    },
    {
        'name': 'settings',
        # (2845,2856)=SHORTCUT_ACTIONS  (2889,2949)=热键监听+捕获  (2857-2887 属于 core.js)
        'sections': [(690, 802), (2845, 2856), (2889, 2949)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, fmtShortcut, comboFromEvent, comboEquals } from "./core.js";',
        ],
        'exports': [],
        'window': ['openSettings', 'closeSettings', 'saveSettings',
                    'resetShortcuts', '_startCapture', 'loadSettings',
                    'renderShortcuts', 'updateShortcutHints'],
        'transforms': [
            # Remove APP_SETTINGS reassignment
            ('APP_SETTINGS = Object.assign(APP_SETTINGS,', 'Object.assign(APP_SETTINGS,'),
        ],
    },
    {
        'name': 'ris',
        'sections': [(2071, 2247)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal } from "./core.js";',
            'import { gotoPage } from "./shell.js";',
            'import { splitReportSections, runQC } from "./qc.js";',
            'import { enqueueCurrent } from "./data.js";',
        ],
        'exports': ['loadRisPage'],
        'window': ['testRisConnection', 'fetchRisReports', 'cancelRis',
                    'risEnqueueAll', 'sendToQC', 'batchQC', 'loadRisPage'],
        'transforms': [],
    },
    {
        'name': 'feedback',
        'sections': [(3328, 3410)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";',
        ],
        'exports': [],
        'window': ['loadFeedback', 'applyFeedbackDelta', 'reviewFeedback'],
        'transforms': [],
    },
    {
        'name': 'auth',
        'sections': [(2951, 3321)],
        'imports': [
            'import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH } from "./core.js";',
            'import { switchPage, loadUsers } from "./shell.js";',
        ],
        'exports': ['bootstrapGate'],
        'window': ['gateAccept', 'gateReject', 'gateCreate', 'gateToLogin',
                    'gateToRegister', 'gateToPwd', 'gateChangePwd',
                    'gateLogin', 'gateActivate', 'toggleUserMenu', 'logout',
                    'copyMachineId', 'openActivateFromSettings',
                    'openOnboardingFromSettings', 'showGate', 'gateShow',
                    'refreshUserUI', 'updateTrialBanner', 'populateLicenseSettings',
                    'bootstrapGate', 'maybeShowOnboarding', 'showOnboarding',
                    'closeOnboarding'],
        'transforms': [
            # LICENSE_STATUS → window.LICENSE_STATUS (reads and writes)
            ('LICENSE_STATUS = d.data;', 'window.LICENSE_STATUS = d.data;'),
            ('LICENSE_STATUS = status || LICENSE_STATUS;', 'window.LICENSE_STATUS = status || window.LICENSE_STATUS;'),
            ('status = d.data; LICENSE_STATUS = status;', 'status = d.data; window.LICENSE_STATUS = status;'),
            # Reads
            ('applyRoleUI(); enterApp(LICENSE_STATUS);', 'applyRoleUI(); enterApp(window.LICENSE_STATUS);'),
            ('updateTrialBanner(LICENSE_STATUS);', 'updateTrialBanner(window.LICENSE_STATUS);'),
            ('const st = LICENSE_STATUS;', 'const st = window.LICENSE_STATUS;'),
        ],
    },
]

# ── 3. 全局文本变换（跨模块生效）─────────────────────────────────
# 声明已在各模块 transforms 中处理（core.js: LICENSE_STATUS, data.js: ACTIVE_QUEUE_ID）
# 剩余引用通过 build_module 中的正则统一加 window. 前缀

GLOBAL_TRANSFORMS = []

# 正则变换：声明已转 window.X，剩余裸引用加 window.
GLOBAL_REGEX = [
    (r'(?<!window\.)ACTIVE_QUEUE_ID', 'window.ACTIVE_QUEUE_ID'),
    (r'(?<!window\.)LICENSE_STATUS',  'window.LICENSE_STATUS'),
]

# ── 4. 组装每个模块 ────────────────────────────────────────────

def build_module(mod):
    """构建单个模块的完整源码。"""
    parts = []

    # 4a. Imports
    for imp in mod['imports']:
        parts.append(imp + '\n')

    if mod['imports']:
        parts.append('\n')

    # 4b. 代码段
    for (start, end) in mod['sections']:
        chunk = extract(start, end)
        parts.append(chunk)
        parts.append('\n')

    # 4c. Exports
    if mod['exports']:
        names = ', '.join(mod['exports'])
        parts.append(f'export {{ {names} }};\n\n')

    # 4d. Window 赋值
    if mod['window']:
        names = ', '.join(mod['window'])
        parts.append(f'Object.assign(window, {{ {names} }});\n')

    return ''.join(parts)

# ── 5. 主流程 ──────────────────────────────────────────────────

def main():
    # 备份
    shutil.copy2(APP_JS, BACKUP_JS)
    print(f'✓ 备份 app.js → app.js.bak')

    # 创建模块目录
    os.makedirs(MODULES_DIR, exist_ok=True)
    print(f'✓ 创建 {MODULES_DIR}')

    # 生成每个模块
    for mod in MODULES:
        source = build_module(mod)

        # 应用模块级变换
        for old, new in mod['transforms']:
            source = source.replace(old, new)

        # 应用全局字符串变换
        for old, new in GLOBAL_TRANSFORMS:
            source = source.replace(old, new)

        # 正则变换：跨模块共享状态加 window. 前缀（声明已在 transforms 中处理）
        for pattern, replacement in GLOBAL_REGEX:
            source = re.sub(pattern, replacement, source)

        out_path = os.path.join(MODULES_DIR, mod['name'] + '.js')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(source)
        line_count = source.count('\n')
        print(f'✓ {mod["name"]}.js  ({line_count} 行)')

    # 6. 新入口 app.js
    entry = '''// 星衍AI放射质控 · Web 版前端入口（ES 模块）
// 导入顺序保证依赖先于使用者加载

import './modules/core.js';
import './modules/shell.js';
import './modules/qc.js';
import './modules/rules.js';
import './modules/data.js';
import './modules/ocr.js';
import './modules/settings.js';
import './modules/ris.js';
import './modules/feedback.js';
import { bootstrapGate } from './modules/auth.js';

// 启动
bootstrapGate();
console.log('星衍AI放射质控 · Web 版 v1.0 已加载');
'''
    with open(APP_JS, 'w', encoding='utf-8') as f:
        f.write(entry)
    print(f'✓ app.js (入口, {entry.count(chr(10))} 行)')

    print('\n完成！')

if __name__ == '__main__':
    main()
