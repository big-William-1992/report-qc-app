// 星衍AI放射质控 · Web 版前端入口（ES 模块）
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
