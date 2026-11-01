// Structural probe for the platform security check (雨课堂 安全核验 / 腾讯 TCaptcha).
// Only layout information is collected: no image content, no answers, no cookies.
(() => {
  if (window.__yktBypass && window.__yktBypass.version === 4) return;
  const HEADING = /^(?:请(?:先)?(?:完成|进行|通过))?(?:安全(?:核验|校验|验证)|人机(?:验证|校验))[：:！!。.]?$/;
  const HINT = /tcaptcha|captcha|geetest|nc_1|nc_2|verify|slider|slide-?block|challenge|security|safety|risk-?check|check-dialog|modal/i;
  const CONFIRM = ['确定', '确认', '继续', '完成', '开始验证', '开始核验', '验证', '我知道了', '同意', '关闭', '继续访问'];
  const visible = el => {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) < 0.05) return false;
    return el.getClientRects().length > 0;
  };
  const text = el => (el?.textContent || '').replace(/\s+/g, '');
  const describe = el => el ? {
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    cls: typeof el.className === 'string' ? el.className.slice(0, 120) : '',
    rect: (() => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })(),
    size: (() => {
      const r = el.getBoundingClientRect();
      return {width: Math.round(r.width), height: Math.round(r.height)};
    })(),
    natural: {width: el.naturalWidth || el.width || 0, height: el.naturalHeight || el.height || 0},
    text: text(el).slice(0, 40),
    disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
  } : null;

  function roots() {
    const out = [];
    const seen = new Set();
    const walk = root => {
      if (!root || seen.has(root)) return;
      seen.add(root);
      out.push(root);
      let all;
      try { all = root.querySelectorAll('*'); } catch (error) { return; }
      for (const el of all) {
        if (el.shadowRoot) walk(el.shadowRoot);
        if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
          try { if (el.contentDocument) walk(el.contentDocument); } catch (error) { /* cross-origin */ }
        }
      }
    };
    try { walk(document); } catch (error) { /* detached document */ }
    return out;
  }

  const firstVisible = (list) => (list || []).find(visible) || null;
  function query(root, selectors) {
    for (const selector of selectors) {
      let found = [];
      try { found = [...root.querySelectorAll(selector)]; } catch (error) { continue; }
      const hit = firstVisible(found);
      if (hit) return hit;
    }
    return null;
  }

  function findHeadingIn(root) {
    let nodes = [];
    try { nodes = [...root.querySelectorAll('*')]; } catch (error) { return null; }
    // The modal title may live in a custom element, so no tag whitelist is used.
    // Leaves first: reading textContent of every ancestor is needlessly quadratic.
    const ordered = [...nodes.filter(el => !el.children.length), ...nodes.filter(el => el.children.length)];
    for (const el of ordered) {
      if (el.children.length > 3) continue;
      const value = text(el);
      if (value.length > 24 || !HEADING.test(value)) continue;
      if (visible(el)) return el;
    }
    return null;
  }

  function findHintRootIn(root) {
    let nodes = [];
    try { nodes = [...scope.querySelectorAll('[id],[class]')]; } catch (error) { return null; }
    for (const el of nodes) {
      const key = `${el.id || ''} ${typeof el.className === 'string' ? el.className : ''}`;
      if (!HINT.test(key)) continue;
      if (!visible(el)) continue;
      const box = el.getBoundingClientRect();
      if (box.width < 120 || box.height < 40) continue;
      if (box.width > innerWidth * 1.6 || box.height > innerHeight * 1.6) continue;
      return el;
    }
    return null;
  }

  function container(scope, anchor) {
    if (!anchor) return scope === document ? document.body : scope;
    const anchorBox = anchor.getBoundingClientRect();
    const anchorArea = Math.max(1, anchorBox.width * anchorBox.height);
    let node = anchor;
    for (let depth = 0; node && depth < 10; depth += 1) {
      const key = `${node.id || ''} ${typeof node.className === 'string' ? node.className : ''}`;
      const role = node.getAttribute && node.getAttribute('role');
      if (role === 'dialog' || role === 'alertdialog' || node.tagName === 'DIALOG' || HINT.test(key)) return node;
      const box = node.getBoundingClientRect();
      // The heading row alone is too small to hold the challenge; keep climbing
      // until an ancestor is clearly bigger than the text it contains.
      if (box.width >= 200 && box.height >= 140 && box.width * box.height >= anchorArea * 3.5) return node;
      node = node.parentElement;
    }
    return anchor.parentElement || scope === document ? document.body : scope;
  }

  function findInRoots(finder) {
    for (const root of roots()) {
      const hit = finder(root);
      if (hit) return hit;
    }
    return null;
  }

  const findHeading = () => findInRoots(findHeadingIn);
  const findHintRoot = () => findInRoots(findHintRootIn);

  const ROLES = {
    thumb: ['#tcaptcha_drag_thumb', '#tcaptcha_drag_button', '#nc_1_n1t', '.tcaptcha-drag-thumb',
      '.tcaptcha-drag-button', '.tcaptcha-slider-btn', '.geetest_slider_button', '.geetest_slide_button',
      '[class*="drag"][class*="btn"]', '[class*="drag"][class*="thumb"]', '[class*="slider"] [class*="button"]',
      '[class*="slider"] [class*="btn"]', '[role="slider"]'],
    bg: ['#slideBg', '#tcaptcha_bg', '.tcaptcha-bg', '.geetest_canvas_bg', '.geetest_canvas_fullbg',
      'img[src*="cap_union"]', 'img[src*="captcha"]', 'canvas[class*="bg"]', 'img[class*="bg"]',
      '[class*="puzzle"][class*="bg"]', '[class*="puzzle"][class*="image"]'],
    piece: ['#slideBlock', '#tcaptcha_slice', '.geetest_canvas_slice', '[class*="slice"]',
      '[class*="puzzle"][class*="block"]', '[class*="slide-block"]', '[class*="puzzleblock"]'],
    track: ['#tcaptcha_drag', '.tcaptcha-drag', '[class*="slider"][class*="track"]', '.geetest_slider',
      '[class*="slider"][class*="wrap"]', '[class*="track"]'],
  };

  function tag(el, role) {
    if (!el) return null;
    el.setAttribute('data-ykt-bypass', role);
    return describe(el);
  }

  function cleanTags() {
    for (const root of roots()) {
      try {
        for (const el of root.querySelectorAll('[data-ykt-bypass]')) el.removeAttribute('data-ykt-bypass');
      } catch (error) { /* detached root */ }
    }
  }

  // 雨课堂自研的"连线/点选路径"核验：
  // 按住“起”点，依次经过 1、2、3，最后到“终”点松开。
  const LABEL_START = /^(起|始|起点|开始)$/;
  const LABEL_END = /^(终|止|终点|结束)$/;
  const CN_NUM = {一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9};

  function filled(el) {
    const style = getComputedStyle(el);
    if (style.backgroundImage && style.backgroundImage !== 'none') return true;
    const match = (style.backgroundColor || '').match(/rgba?\(([^)]+)\)/);
    if (!match) return false;
    const parts = match[1].split(',').map(Number);
    const alpha = parts.length > 3 ? parts[3] : 1;
    return alpha >= 0.2 && !(parts[0] > 250 && parts[1] > 250 && parts[2] > 250);
  }

  // A dot is a small filled shape; the instruction sentence is plain text, so it
  // must never be treated as a point (otherwise the drag selects the paragraph).
  function dotLike(el) {
    let node = el;
    for (let depth = 0; node && depth < 3; depth += 1, node = node.parentElement) {
      if (node.tagName === 'svg') return true;
      if (filled(node)) {
        const box = node.getBoundingClientRect();
        if (box.width >= 12 && box.height >= 12 && box.width <= 120 && box.height <= 120) return true;
      }
    }
    return false;
  }

  function labelNodes(host) {
    const out = [];
    let nodes = [];
    try { nodes = [...host.querySelectorAll('div,span,i,b,em,strong,button,li,p,text')]; }
    catch (error) { nodes = []; }
    for (const el of nodes) {
      const value = text(el);
      if (!value || value.length > 2) continue;
      if (!(LABEL_START.test(value) || LABEL_END.test(value) || /^[0-9]{1,2}$/.test(value) || value in CN_NUM)) continue;
      if (!visible(el)) continue;
      const rect = el.getBoundingClientRect();
      // Single glyphs are narrow: 8×10 is already a rendered character.
      if (rect.width < 8 || rect.height < 10 || rect.width > 90 || rect.height > 90) continue;
      // Labels wrapped in a larger container (the hint sentence) are not dots.
      if (el.children.length && ![...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim())) continue;
      // Only glyphs sitting on a filled dot (or an SVG shape) count.
      if (!dotLike(el)) continue;
      out.push({el, value, rect});
    }
    return out;
  }

  function pathChallenge(host) {
    const labels = labelNodes(host);
    if (!labels.length) return null;
    const start = labels.find(item => LABEL_START.test(item.value));
    const end = labels.find(item => LABEL_END.test(item.value));
    if (!start || !end) return null;
    const block = start.el.closest('div,section,ul') || host;
    const near = item => Math.abs(item.rect.x - start.rect.x) < 900 && Math.abs(item.rect.y - start.rect.y) < 700;
    const steps = labels
      .filter(item => /^[0-9]{1,2}$/.test(item.value) || item.value in CN_NUM)
      .map(item => ({...item, order: /^[0-9]{1,2}$/.test(item.value) ? Number(item.value) : CN_NUM[item.value]}))
      .sort((left, right) => left.order - right.order);
    const inside = steps.filter(item => block.contains(item.el) && near(item));
    const ordered = [start, ...(inside.length ? inside : steps.filter(near)), end];
    if (ordered.length < 2) return null;
    ordered.forEach((item, index) => item.el.setAttribute('data-ykt-bypass', `path-${index}`));
    return {
      count: ordered.length,
      labels: ordered.map(item => item.value),
      hint: (host.textContent || '').replace(/\s+/g, ' ').slice(0, 80),
    };
  }

  function detect() {
    const scope = document;
    cleanTags();
    const heading = findHeading();
    const hint = findHintRoot();
    // Vendor frames (turing.captcha.gtimg.com, geetest, …) often carry no Chinese
    // heading and no self-describing id, so a known captcha host is treated as a
    // challenge container on its own.
    const vendor = /turing\.captcha|geetest|sensebot|aliyun|hcaptcha|qq\.com\/cap|captcha/i
      .test(location.host + location.pathname);
    if (!heading && !hint && !vendor) return {present: false, kind: 'none', source: '', buttons: [], slider: null, board: null};
    const host = heading || hint ? container(scope, heading || hint) : document.body;
    try { host.setAttribute('data-ykt-bypass', 'panel'); } catch (error) { /* detached */ }
    const board = tag(query(host, ['canvas']), 'board');
    const slider = {
      thumb: tag(query(host, ROLES.thumb) || query(document, ROLES.thumb), 'thumb'),
      bg: tag(query(host, ROLES.bg) || query(document, ROLES.bg), 'bg'),
      piece: tag(query(host, ROLES.piece) || query(document, ROLES.piece), 'piece'),
      track: tag(query(host, ROLES.track) || query(document, ROLES.track), 'track'),
    };
    const clickable = [];
    let nodes = [];
    try { nodes = [...host.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"],[class*="btn"],[class*="button"]')]; }
    catch (error) { nodes = []; }
    for (const el of nodes) {
      if (!visible(el)) continue;
      const label = text(el) || el.value || '';
      if (!label) continue;
      if (!CONFIRM.some(item => label === item || label.includes(item))) continue;
      clickable.push({...describe(el), role: 'confirm', label});
      el.setAttribute('data-ykt-bypass', `confirm-${clickable.length - 1}`);
    }
    const hasSlider = !!(slider.thumb && (slider.bg || slider.piece || slider.track));
    const path = pathChallenge(host);
    if (!heading && !hint && !hasSlider && !clickable.length && !path) {
      cleanTags();
      return {present: false, kind: 'none', source: '', buttons: [], slider: null, path: null, board: null};
    }
    const kind = path ? 'path' : hasSlider ? 'slider' : (clickable.length ? 'confirm' : 'unknown');
    return {
      present: true,
      kind,
      source: heading && hint ? 'heading+root' : (heading ? 'heading' : (hint ? 'root' : 'vendor')),
      heading: heading ? text(heading).slice(0, 60) : '',
      container: describe(host),
      slider: hasSlider ? slider : null,
      path,
      board,
      buttons: clickable,
      viewport: [innerWidth, innerHeight],
      url: location.href.slice(0, 200),
    };
  }

  function probe(limit = 260) {
    const scope = document;
    const heading = findHeading();
    const hint = findHintRoot();
    const host = heading || hint ? container(scope, heading || hint) : document.body;
    const dump = [];
    const walk = (el, depth) => {
      if (dump.length >= limit || !el) return;
      if (depth > 7) return;
      const box = el.getBoundingClientRect();
      if (box.width < 3 || box.height < 3) return;
      dump.push({depth, ...describe(el), shadow: !!el.shadowRoot,
        attrs: el.getAttributeNames().filter(name => !/^data-ykt/.test(name)).slice(0, 8)
          .reduce((acc, name) => ({...acc, [name]: (el.getAttribute(name) || '').slice(0, 40)}), {})});
      for (const child of el.children) walk(child, depth + 1);
    };
    walk(host, 0);
    return {
      url: location.href.slice(0, 200),
      heading: heading ? text(heading).slice(0, 60) : '',
      host: describe(host),
      frames: [...document.querySelectorAll('iframe')].map(el => ({src: (el.getAttribute('src') || '').slice(0, 160),
        size: describe(el).size})),
      nodes: dump,
    };
  }


  // The check is drawn on a canvas inside a shadow root, so the glyphs are not in
  // the DOM at all. Read the canvas pixels, find the coloured dots and match each
  // glyph against templates rendered with the same browser.
  function boardCanvas() {
    const tagged = findInRoots(root => root.querySelector('[data-ykt-bypass="board"]'));
    return tagged || findInRoots(root => root.querySelector('canvas'));
  }

  function readCanvas() {
    const canvas = boardCanvas();
    if (!canvas) return null;
    const width = canvas.width, height = canvas.height;
    if (!width || !height) return null;
    const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
    const rgb = (x, y) => { const i = (y * width + x) * 4; return [data[i], data[i + 1], data[i + 2]]; };
    const luma = (x, y) => { const c = rgb(x, y); return (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000; };

    const coloured = new Uint8Array(width * height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const [r, g, b] = rgb(x, y);
        const high = Math.max(r, g, b), low = Math.min(r, g, b);
        if (high >= 70 && high - low >= 55) coloured[y * width + x] = 1;
      }
    }
    const spots = [];
    const seenPixel = new Uint8Array(width * height);
    for (let index = 0; index < coloured.length; index += 1) {
      if (!coloured[index] || seenPixel[index]) continue;
      const stack = [index];
      seenPixel[index] = 1;
      let count = 0, sx = 0, sy = 0, minX = width, maxX = -1, minY = height, maxY = -1;
      while (stack.length) {
        const current = stack.pop();
        const y = Math.floor(current / width), x = current % width;
        count += 1; sx += x; sy += y;
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          const next = ny * width + nx;
          if (coloured[next] && !seenPixel[next]) { seenPixel[next] = 1; stack.push(next); }
        }
      }
      if (count < 120 || count > 9000) continue;
      const boxW = maxX - minX + 1, boxH = maxY - minY + 1;
      if (boxH === 0 || boxW / boxH < 0.45 || boxW / boxH > 2.2) continue;
      if (count / (boxW * boxH) < 0.45) continue;
      spots.push({cx: sx / count, cy: sy / count, r: Math.min(boxW, boxH) / 2});
    }
    if (spots.length < 2) return {width, height, spots: []};

    const GRID = 24;
    const gridOf = (read, box) => {
      const [left, top, right, bottom] = box;
      const grid = new Uint8Array(GRID * GRID);
      for (let gy = 0; gy < GRID; gy += 1) for (let gx = 0; gx < GRID; gx += 1) {
        const x = Math.round(left + (right - left) * (gx + 0.5) / GRID);
        const y = Math.round(top + (bottom - top) * (gy + 0.5) / GRID);
        grid[gy * GRID + gx] = read(x, y) ? 1 : 0;
      }
      return grid;
    };
    const bboxOf = (read, left, top, right, bottom) => {
      let minX = right, maxX = left, minY = bottom, maxY = top, found = false;
      for (let y = top; y <= bottom; y += 1) for (let x = left; x <= right; x += 1) {
        if (!read(x, y)) continue;
        found = true;
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
      return found ? [minX, minY, maxX, maxY] : null;
    };
    const iou = (a, b) => {
      let inter = 0, union = 0;
      for (let i = 0; i < a.length; i += 1) {
        if (a[i] && b[i]) inter += 1;
        if (a[i] || b[i]) union += 1;
      }
      return union ? inter / union : 0;
    };

    const canvasTemplate = document.createElement('canvas');
    canvasTemplate.width = 64; canvasTemplate.height = 64;
    const tctx = canvasTemplate.getContext('2d');
    const templates = [];
    const labels = ['1', '2', '3', '4', '5', '起', '终'];
    for (const label of labels) {
      for (const size of [13, 14, 15, 16, 17]) {
        for (const family of ['Arial', '"Microsoft YaHei"', 'sans-serif']) {
          for (const weight of ['bold ', '']) {
            const font = `${weight}${size}px ${family}`;
            tctx.fillStyle = '#000'; tctx.fillRect(0, 0, 64, 64);
            tctx.fillStyle = '#fff'; tctx.font = font; tctx.textAlign = 'center'; tctx.textBaseline = 'middle';
            tctx.fillText(label, 32, 32);
            const pixels = tctx.getImageData(0, 0, 64, 64).data;
            const read = (x, y) => pixels[(y * 64 + x) * 4] > 140;
            const box = bboxOf(read, 0, 0, 63, 63);
            if (box) templates.push({label, grid: gridOf(read, box)});
          }
        }
      }
    }

    const result = spots.map(spot => {
      let best = {label: null, score: 0};
      for (const threshold of [190, 205, 220]) {
        const inside = (x, y) => ((x - spot.cx) ** 2 + (y - spot.cy) ** 2) <= (spot.r * 0.8) ** 2;
        const read = (x, y) => x >= 0 && y >= 0 && x < width && y < height
          && inside(x, y) && luma(x, y) > threshold;
        const box = bboxOf(read, Math.floor(spot.cx - spot.r), Math.floor(spot.cy - spot.r),
                           Math.ceil(spot.cx + spot.r), Math.ceil(spot.cy + spot.r));
        if (!box) continue;
        const grid = gridOf(read, box);
        for (const template of templates) {
          const score = iou(grid, template.grid);
          if (score > best.score) best = {label: template.label, score};
        }
      }
      return {x: spot.cx, y: spot.cy, r: spot.r,
              label: best.label, score: Math.round(best.score * 100) / 100};
    });
    return {width, height, spots: result};
  }

  window.__yktBypass = {
    version: 4,
    visible,
    text,
    detect,
    probe,
    readCanvas,
    dotLike,
    dotAt(x, y) {
      try {
        let el = document.elementFromPoint(x, y);
        for (let depth = 0; el && el.shadowRoot && depth < 6; depth += 1) {
          const inner = el.shadowRoot.elementFromPoint(x, y);
          if (!inner || inner === el) break;
          el = inner;
        }
        return !!el && dotLike(el);
      } catch (error) {
        return false;
      }
    },
    noSelect(on) {
      try {
        const root = document.documentElement;
        if (on) {
          root.dataset.yktSelect = root.style.userSelect || '';
          root.style.userSelect = 'none';
        } else {
          root.style.userSelect = root.dataset.yktSelect || '';
        }
      } catch (error) { /* detached document */ }
    },
    signature() {
      const heading = findHeading();
      const hint = findHintRoot();
      return !!(heading || hint);
    },
    clickConfirm(index) {
      const el = document.querySelector(`[data-ykt-bypass="confirm-${index}"]`);
      if (!el) return false;
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
      }
      if (typeof el.click === 'function') el.click();
      return true;
    },
  };
})();
