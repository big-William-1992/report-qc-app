/**
 * ESLint 配置 — 星衍放射质控软件前端
 *
 * 零依赖（ESLint flat config + 仅标准规则），不引入 airbnb / prettier 等重量级配置。
 * 目标：抓语法错误、未使用变量、重复声明——单人项目，风格自由度留给作者。
 *
 * 运行：npx eslint web/static/js/
 */

// @ts-check
export default [
  {
    ignores: [
      'web/static/js/app.js.bak',
      'node_modules/**',
      'coverage_html/**',
      'playwright-report/**',
    ],
  },
  {
    files: ['web/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: {
          impliedStrict: true,
        },
      },
      globals: {
        // 浏览器全局
        window: 'readonly',
        document: 'readonly',
        console: 'readonly',
        localStorage: 'readonly',
        navigator: 'readonly',
        fetch: 'readonly',
        setTimeout: 'readonly',
        setInterval: 'readonly',
        clearTimeout: 'readonly',
        clearInterval: 'readonly',
        URL: 'readonly',
        Blob: 'readonly',
        FileReader: 'readonly',
        FormData: 'readonly',
        Image: 'readonly',
        HTMLElement: 'readonly',
        Node: 'readonly',
        Event: 'readonly',
        MouseEvent: 'readonly',
        KeyboardEvent: 'readonly',
        DOMParser: 'readonly',
        TextDecoder: 'readonly',
        TextEncoder: 'readonly',
        crypto: 'readonly',
        performance: 'readonly',
        structuredClone: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
        ResizeObserver: 'readonly',
        IntersectionObserver: 'readonly',
        WebSocket: 'readonly',
        Notification: 'readonly',
        URLSearchParams: 'readonly',
        URLPattern: 'readonly',
        AbortController: 'readonly',
        AbortSignal: 'readonly',
        Headers: 'readonly',
        Request: 'readonly',
        Response: 'readonly',
        EventSource: 'readonly',
        ReadableStream: 'readonly',
        WritableStream: 'readonly',
        TransformStream: 'readonly',
        BlobEvent: 'readonly',
        CompositionEvent: 'readonly',
        DragEvent: 'readonly',
        FocusEvent: 'readonly',
        InputEvent: 'readonly',
        WheelEvent: 'readonly',
        PointerEvent: 'readonly',
        TouchEvent: 'readonly',
        CutEvent: 'readonly',
        CopyEvent: 'readonly',
        PasteEvent: 'readonly',
        AnimationEvent: 'readonly',
        TransitionEvent: 'readonly',
        MessageEvent: 'readonly',
        ProgressEvent: 'readonly',
        StorageEvent: 'readonly',
        PopStateEvent: 'readonly',
        MediaEvent: 'readonly',
        OfflineAudioCompletionEvent: 'readonly',
        BeforeInstallPromptEvent: 'readonly',
        BeforeUnloadEvent: 'readonly',
        CustomEvent: 'readonly',
        UIEvent: 'readonly',
        WheelEvent: 'readonly',
        // pywebview 原生桥
        pywebview: 'readonly',
        // 第三方库（可选集成）
        Chart: 'readonly',
        echarts: 'readonly',
      },
    },
    rules: {
      // ── 语法级错误（致命）──
      'no-undef': 'error',                // 未定义变量
      'no-undef-init': 'error',           // 初始化为 undefined
      'no-dupe-keys': 'error',            // 重复对象键
      'no-dupe-args': 'error',            // 重复参数
      'no-dupe-class-members': 'error',   // 重复类成员
      'no-duplicate-case': 'error',       // switch 重复 case
      'no-redeclare': 'error',            // 重复声明
      'no-const-assign': 'error',         // const 赋值
      'no-func-assign': 'error',          // 函数赋值
      'no-import-assign': 'error',        // 导入赋值
      'no-class-assign': 'error',         // class 赋值
      'no-obj-calls': 'error',            // 对象调用
      'no-setter-return': 'error',        // setter 返回值
      'no-throw-literal': 'error',        // 抛字面量
      'use-isnan': 'error',               // NaN 比较
      'valid-typeof': 'error',            // typeof 结果
      'no-unreachable': 'error',          // 不可达代码
      'no-unexpected-multiline': 'error', // 多行意外
      'no-sparse-arrays': 'error',        // 稀疏数组
      'no-var': 'error',                  // 强制 let/const

      // ── 未使用（F401 等价）──
      'no-unused-vars': ['warn', {
        args: 'after-used',
        varsIgnorePattern: '^_$',
      }],

      // ── 比较与赋值（F63 等价）──
      'eqeqeq': ['error', 'smart'],        // === 智能检查
      'no-eq-null': 'off',                 // 允许 x == null（null 检查惯例）
      'no-self-compare': 'error',          // 自比较
      'no-negated-condition': 'error',     // 否定条件
      'no-constant-condition': ['error', { checkLoops: false }], // 常量条件
      'no-case-declarations': 'error',     // case 内声明

      // ── 类型安全 ──
      'no-implicit-coercion': 'error',     // 隐式类型转换
      'no-implicit-globals': 'error',      // 隐式全局
      'no-prototype-builtins': 'error',    // Object.prototype 方法
      'no-new-native-nonconstructor': 'error', // new 非构造原生

      // ── 控制流 ──
      'no-constant-binary-expression': 'error', // 常量二元表达式
      'no-fallthrough': 'error',                  // switch fallthrough
      'no-unsafe-optional-chaining': 'error',     // 不安全可选链
      'no-unsafe-optional-chaining': 'error',     // 不安全可选链

      // ── Promise ──
      'no-promise-executor-return': 'error',      // promise executor 返回值

      // ── 关闭（单人项目风格自由）──
      'semi': 'off',
      'quotes': 'off',
      'indent': 'off',
      'comma-dangle': 'off',
      'curly': 'off',
      'space-before-function-paren': 'off',
      'prefer-const': 'off',
      'no-plusplus': 'off',
      'no-bitwise': 'off',
      'prefer-arrow-callback': 'off',
      'no-use-before-define': 'off',
      'prefer-template': 'off',
      'object-shorthand': 'off',
      'no-mixed-operators': 'off',
      'no-param-reassign': 'off',
      'class-methods-use-this': 'off',
      'no-else-return': 'off',
      'implicit-arrow-linebreak': 'off',
      'eol-last': 'off',
      'camelcase': 'off',
      'no-empty': 'off',
    },
  },
];
