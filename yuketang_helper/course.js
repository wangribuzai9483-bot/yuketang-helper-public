// Directory navigation only; progress and submission remain owned by the website.
(() => {
  if (window.__yktCourse?.version === 1) return;
  const text = el => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0 &&
    el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const disabled = el => !!el && (el.disabled || el.getAttribute('aria-disabled') === 'true' ||
    !!el.closest('.is-disabled,.is-locked,[aria-disabled="true"]'));
  const items = () => [...document.querySelectorAll('.leaf-item')].map((el,index) => {
    const label = text(el.querySelector('.leaf-item-tag'));
    const title = text(el.querySelector('.leaf-item-title'));
    return {el,index,key:JSON.stringify([index,label,title]),title,label,
      kind:label==='视频'?'video':label==='作业'?'homework':'unsupported',
      locked:disabled(el),active:el.classList.contains('is-active')};
  });
  const publicItem = ({el,...item}) => item;
  const blocked = () => window.__yktHomework?.securityState().required ||
    [...document.querySelectorAll('[role="dialog"],[aria-modal="true"],.el-dialog,.el-message-box,.v-modal')].some(visible);
  let lastClick = '';
  const expanded = new WeakSet();
  window.__yktCourse = {
    version:1,
    resetNavigation() { lastClick=''; },
    snapshot() {
      const all=items(), selected=all.filter(item=>item.active);
      return {course:text(document.querySelector('.learning-space-header__primary')),
        items:all.map(publicItem),current:selected.length===1?publicItem(selected[0]):null,
        ambiguous:selected.length>1};
    },
    expandOne() {
      const el=[...document.querySelectorAll('.nav-item-title:not(.is-expand)')]
        .find(el=>visible(el) && !expanded.has(el));
      if (!el || blocked()) return false;
      expanded.add(el); el.click(); return true;
    },
    advance(expected) {
      const all=items(), selected=all.filter(item=>item.active);
      if (selected.length!==1 || selected[0].key!==expected) return {state:'directory-changed'};
      if (blocked()) return {state:'blocked-dialog'};
      const next=all[selected[0].index+1];
      if (!next) return {state:'complete'};
      if (next.kind==='unsupported') return {state:'unsupported',item:publicItem(next)};
      if (next.locked) return {state:'locked',item:publicItem(next)};
      if (lastClick===expected) return {state:'already-clicked'};
      lastClick=expected; next.el.click();
      return {state:'clicked',item:publicItem(next)};
    },
    first() {
      if (blocked()) return {state:'blocked-dialog'};
      const all=items();
      if (all.some(item=>item.active)) return {state:'directory-changed'};
      const first=all[0];
      if (!first) return {state:'missing-directory'};
      if (first.kind==='unsupported' || first.locked) return {state:'unsupported',item:publicItem(first)};
      if (lastClick==='first') return {state:'already-clicked'};
      lastClick='first'; first.el.click(); return {state:'clicked',item:publicItem(first)};
    },
    firstQuestion() {
      if (blocked()) return {state:'blocked-dialog'};
      const q=window.__yktHomework?.read();
      if (!q) return {state:'loading'};
      const menu=[...document.querySelectorAll('.problems-aside .subject-item')].filter(visible);
      const first=menu.find(el=>Number(el.dataset.order || text(el).match(/^\d+/)?.[0])===1);
      if (q.index===1 || first?.classList.contains('active')) return {state:'ready'};
      if (!first || disabled(first)) return {state:'missing-first-question'};
      first.click(); return {state:'clicked-first',fingerprint:q.fingerprint};
    },
  };
})();
