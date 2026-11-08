/* ---------------------------------------------------------------------------
   多语言切换（仅 ENABLE_I18N=1 时由模板引入）
   ---------------------------------------------------------------------------
   译文通过 <head> 里 <script type="application/json" id="i18n-pack">
   （不可执行的数据块）注入，切换语言时写入 Cookie 后刷新页面，
   由服务端按 Cookie 渲染对应语言；URL ?lang= 参数同样受支持。
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  var COOKIE_NAME = 'hypixel-lang';
  var COOKIE_MAX_AGE = 31536000; /* 一年 */

  function setCookie(code) {
    /* 语言代码已在服务端白名单内（选项由模板渲染），这里再做一次防御性校验 */
    if (!/^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(code)) return;
    document.cookie = COOKIE_NAME + '=' + encodeURIComponent(code) +
      '; path=/; max-age=' + COOKIE_MAX_AGE + '; samesite=lax';
  }

  function mount() {
    var select = document.querySelector('[data-lang-select]');
    if (!select) return;
    select.addEventListener('change', function () {
      var code = select.value;
      setCookie(code);
      /* 保留 ?lang= 以外的查询参数，去掉旧的 lang 覆盖，交给 Cookie 生效 */
      var url = new URL(window.location.href);
      url.searchParams.delete('lang');
      window.location.replace(url.toString());
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
