/* ---------------------------------------------------------------------------
   亮色 / 深色主题切换
   ---------------------------------------------------------------------------
   * 未手动选择过：跟随设备 prefers-color-scheme（识别用户系统设置）
   * 点过按钮：把选择写进 localStorage，之后不再被系统设置覆盖
   * 脚本在 <head> 同步执行，先落 data-theme 再渲染，避免首屏闪一下白/黑
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  var STORAGE_KEY = 'hypixel-theme';
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var NEXT_LABEL = { light: '🌙 深色', dark: '☀ 浅色' };

  /* 多语言助手：译文来自 #i18n-pack（脚本位于其之后执行，i18n 关闭时回退中文） */
  var I18N = (function () {
    try {
      var el = document.getElementById('i18n-pack');
      return el ? (JSON.parse(el.textContent) || {}) : {};
    } catch (e) { return {}; }
  })();

  function T(text) {
    return I18N[text] || text;
  }

  function storedTheme() {
    try {
      var value = localStorage.getItem(STORAGE_KEY);
      return value === 'dark' || value === 'light' ? value : null;
    } catch (e) {
      return null;
    }
  }

  function systemTheme() {
    return media && media.matches ? 'dark' : 'light';
  }

  function updateButtons(theme) {
    var label = T(NEXT_LABEL[theme] || NEXT_LABEL.light);
    var hint = theme === 'dark' ? T('切换到浅色主题') : T('切换到深色主题');
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.textContent = label;
      btn.setAttribute('aria-label', hint);
      btn.title = hint;
    });
  }

  function apply(theme, persist) {
    root.setAttribute('data-theme', theme);
    root.style.colorScheme = theme;
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, theme);
      } catch (e) {
        /* 隐私模式下写不进去也不影响本次切换 */
      }
    }
    updateButtons(theme);
  }

  apply(storedTheme() || systemTheme(), false);

  function mount() {
    var bar = document.querySelector('.version-bar');
    if (!bar) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'theme-toggle';
    btn.setAttribute('data-theme-toggle', '');
    btn.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      apply(next, true);
    });
    bar.appendChild(btn);
    updateButtons(root.getAttribute('data-theme'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

  /* 用户没手动选过时，系统切换深浅色要跟着变 */
  if (media && media.addEventListener) {
    media.addEventListener('change', function (event) {
      if (!storedTheme()) apply(event.matches ? 'dark' : 'light', false);
    });
  }
})();
