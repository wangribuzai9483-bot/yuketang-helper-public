options => {
  const normalize = value => (value || "").normalize("NFKC").replace(/\s+/g, "").trim();
  const target = normalize(options.courseName);
  const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
  if ([...document.querySelectorAll('[role="dialog"], .el-dialog__wrapper')].some(visible)) {
    return { state: "navigation-blocked" };
  }
  // Prefer title attributes: some campus cards truncate or append class/term labels.
  const titled = [...document.querySelectorAll('[title], [aria-label]')].filter(el =>
    visible(el) && [el.getAttribute('title'), el.getAttribute('aria-label')].some(v => normalize(v) === target));
  const exact = [...document.querySelectorAll('a, button, span, div, p, h2, h3, h4')].filter(el => {
    if (!visible(el) || normalize(el.textContent) !== target) return false;
    return ![...el.children].some(child => normalize(child.textContent) === target);
  });
  const elements = [...new Set([...titled, ...exact])];
  const clicked = new Set();
  for (const el of elements) {
    const card = el.closest('a, [role="button"], .course-card, .course-item, .classroom-card, .lesson-item, li') || el;
    clicked.add(card);
  }
  if (clicked.size === 1) {
    [...clicked][0].click();
    return { state: "course-clicked" };
  }
  if (clicked.size > 1) return { state: "ambiguous-course", count: clicked.size };
  // New campus portals can land on an overview page before the actual player.
  if (/\/studentLog\//i.test(location.pathname)) {
    const labels = ['进入学习', '开始学习', '视频学习', '课件学习', '学习空间'];
    const action = [...document.querySelectorAll('a, button, [role="button"]')]
      .find(el => visible(el) && labels.includes((el.textContent || '').trim()));
    if (action) { action.click(); return { state: "learning-clicked" }; }
  }
  return { state: "course-not-found" };
}
