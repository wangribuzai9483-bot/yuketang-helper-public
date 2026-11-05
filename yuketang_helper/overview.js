// Enter a video through ordinary visible overview controls. Never open tests.
() => {
  const text = el => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el?.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden';
  const disabled = el => !!el.closest('.content-box-disable, .activity__wrap-disable, .leaf-detail-disable, .is-disabled, [aria-disabled="true"]');
  if ([...document.querySelectorAll('[role="dialog"], [aria-modal="true"], .el-dialog__wrapper, .v-modal')].some(visible)) {
    return {state: 'overview-blocked', title: '课程入口有弹窗，请恢复浏览器处理后继续'};
  }
  const state = window.__yktOverviewEntry ||= {expanded: new WeakSet(), pending: null};
  const now = performance.now();
  state.waitingSince ??= now;
  const classroom = location.pathname.match(/\/(?:studentLog|lms-graph)\/(\d+)(?:\/|$)/)?.[1] ||
    new URL(location.href).searchParams.get('classroom_id');
  const videoRows = () => [...document.querySelectorAll('.leaf_list__wrap .activity__wrap, .leaf-item, .leaf-detail, a[href]')]
    .filter(el => visible(el) && (
      text(el.querySelector('.leaf-item-tag')) === '视频' ||
      !!el.querySelector('.leaf-title .icon--shipin') ||
      /^(视频|片段|Video)$/i.test(text(el.querySelector('.leaf-title .unit-tag'))) ||
      [...el.querySelectorAll('.tag use')].some(icon => /#icon-shipin(?:-E1)?$/.test(icon.getAttribute('href') || icon.getAttribute('xlink:href') || '')) ||
      (el.matches('a[href]') && (() => {
        const url = new URL(el.href, location.href);
        const videoPath = url.pathname.match(/\/lms-graph\/(\d+)\/video\/\d+/);
        return url.origin === location.origin && videoPath && videoPath[1] === classroom;
      })())
    ));
  // Chapter contents arrive asynchronously. Do not expand another chapter while
  // the first is loading, or reopen a video while its popup is being created.
  if (state.pending?.kind === 'video') {
    return {state: now - state.pending.at > 20000 ? 'overview-attention' : 'overview-loading',
      title: '已点击视频入口，等待播放器打开；若未打开，请检查浏览器弹窗提示'};
  }
  if (state.pending?.kind === 'chapter') {
    const region = state.pending.region;
    if (region?.isConnected && visible(region) && region.querySelector('.activity__wrap')) {
      state.pending = null;
    } else if (now - state.pending.at < 20000) {
      return {state: 'overview-loading', title: '正在加载章节中的学习单元'};
    } else {
      return {state: 'overview-attention', title: '章节内容尚未加载，请恢复浏览器检查网络或展开章节'};
    }
  }
  const candidates = videoRows();
  const rows = candidates.filter(el => !disabled(el));
  if (rows.length) {
    const row = rows.find(el => !/已完成/.test(text(el.querySelector('.statistics-box')))) || rows[0];
    state.pending = {kind: 'video', at: now};
    if (row.classList.contains('leaf-detail')) {
      return {state: 'overview-video-ready', title: '正在从学习内容打开视频',
        index: [...document.querySelectorAll('.leaf-detail')].indexOf(row)};
    }
    row.click();
    return {state: 'overview-video-clicked', title: '正在从课程大纲打开视频'};
  }
  if (/\/studentLog\//i.test(location.pathname)) {
    const expanders = [...document.querySelectorAll('.activity__wrap .sub-info span')]
      .filter(el => visible(el) && !disabled(el) && /^(展开|Expand)$/i.test(text(el)) && !state.expanded.has(el));
    const expander = expanders.find(el => !/已完成/.test(text(el.closest('.activity__wrap')?.querySelector('.statistics-box')))) || expanders[0];
    if (expander) {
      const region = expander.closest('.content-box')?.querySelector('.leaf_list__wrap');
      state.expanded.add(expander);
      state.pending = {kind: 'chapter', at: now, region};
      expander.click();
      return {state: 'overview-expanded', title: '正在展开课程章节，查找视频入口'};
    }
    const tab = [...document.querySelectorAll('.rain-tabs__nav-item')]
      .find(el => visible(el) && text(el.querySelector('.rain-tabs__nav-item-text')) === '学习内容');
    if (tab && !tab.classList.contains('rain-tabs__nav-item--active')) {
      tab.click();
      return {state: 'overview-tab-clicked', title: '正在打开课程学习内容'};
    }
  }
  if (candidates.length && !rows.length) {
    const date = text(candidates[0].querySelector('.sub-info')).match(/\d{4}-\d{2}-\d{2}/)?.[0];
    return {state: 'overview-locked', title: `网页将当前目录中的视频全部标为不可继续学习${date ? `（考核截止时间 ${date}）` : ''}；请检查课程开放状态`};
  }
  if (now - state.waitingSince > 20000) {
    return {state: 'overview-attention', title: '学习内容已打开，但未识别到可操作的视频入口；请检查网页中的视频是否仍可打开'};
  }
  return {state: 'overview-frame', title: '正在等待学习内容目录加载'};
}
