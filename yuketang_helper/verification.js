// Detect visible verification headings only. No challenge content or answers are read.
(() => {
  if (window.__yktVerification?.version === 1) return;
  const matches = value => /^(?:请(?:先)?(?:完成|进行|通过))?(?:安全(?:核验|校验|验证)|人机(?:验证|校验))[：:！!。.]?$/.test((value||'').replace(/\s+/g,''));
  const visible = el => !!el && el.getClientRects().length>0 &&
    el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  function scan(root) {
    for (const el of root.querySelectorAll('*')) {
      if (matches(el.textContent) && visible(el)) return true;
      if (el.shadowRoot && visible(el) && scan(el.shadowRoot)) return true;
    }
    return false;
  }
  window.__yktVerification = {version:1,read() {
    return {required:scan(document),source:'visible-heading'};
  }};
})();
