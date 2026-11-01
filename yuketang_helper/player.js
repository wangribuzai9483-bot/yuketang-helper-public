// Only ordinary player/DOM operations. The site continues to own progress reporting.
(() => {
  if (window.__yktHelper?.version === 2) return;
  let tracked = null;
  let source = "";
  let sample = { time: -1, at: performance.now() };
  let lastAttempt = -Infinity;
  let attempts = 0;
  let nextKey = "";
  let nextAt = 0;
  let speedChoice = "";
  let speedChanges = [];
  let lastSpeedAttempt = -Infinity;
  let speedStopped = false;
  let playError = "";
  let disposed = false;
  let hasPlayed = false;
  const visible = el => !!el && el.getClientRects().length > 0 &&
    getComputedStyle(el).display !== "none" && getComputedStyle(el).visibility !== "hidden";
  const text = el => (el?.textContent || "").replace(/\s+/g, " ").trim();
  const leaves = () => [...document.querySelectorAll(".leaf-item")];
  const videoLeaf = el => text(el.querySelector(".leaf-item-tag")) === "视频";
  const title = el => text(el?.querySelector(".leaf-item-title"));
  const active = () => document.querySelector(".leaf-item.is-active");
  const blocked = () => {
    const selectors = [
      '[role="dialog"]', '[aria-modal="true"]', '.el-message-box__wrapper',
      '.el-dialog__wrapper', '.v-modal', '.video-question', '.video-problem',
      '.question-dialog', '.problem-dialog', '.xt_video_player_question'
    ];
    for (const el of document.querySelectorAll(selectors.join(","))) {
      if (visible(el)) return text(el).slice(0, 120) || "页面弹窗，需要处理";
    }
    return "";
  };
  const chooseVideo = () => {
    const videos = [...document.querySelectorAll("video.xt_video_player, #video-box video, video")];
    return videos.find(visible) || null;
  };
  window.__yktHelper = {
    version: 2,
    read(options) {
      if (disposed) return { state: "disabled" };
      const obstruction = blocked();
      const video = chooseVideo();
      const base = {
        title: title(active()) || text(document.querySelector(".unit-title")) || "当前视频",
        course: text(document.querySelector(".learning-space-header__primary")),
        videoCount: leaves().filter(videoLeaf).length,
      };
      if (obstruction) {
        if (video && !video.paused) video.pause();
        // Do not accumulate retries while the user is answering a question.
        attempts = 0;
        return { ...base, state: "blocked", detail: obstruction };
      }
      if (!video) return { ...base, state: "waiting" };
      const now = performance.now();
      const currentSource = video.currentSrc || video.getAttribute("src") || "";
      if (tracked !== video || source !== currentSource) {
        tracked = video;
        source = currentSource;
        sample = { time: video.currentTime, at: now };
        attempts = 0;
        lastAttempt = -Infinity;
        nextKey = "";
        nextAt = 0;
        speedChoice = "";
        speedChanges = [];
        lastSpeedAttempt = -Infinity;
        speedStopped = false;
        playError = "";
        hasPlayed = false;
      }
      const detail = {
        ...base, time: video.currentTime, duration: Number.isFinite(video.duration) ? video.duration : 0,
        rate: video.playbackRate, muted: video.muted, readyState: video.readyState,
      };
      if (video.error) return { ...detail, state: "error", detail: `视频加载错误（${video.error.code}）` };
      if (video.ended) return { ...detail, state: "ended" };
      if (nextAt === Infinity) { nextKey = ""; nextAt = 0; }
      video.muted = options.muted;
      // Prefer the platform's existing speed selector so its own UI/state stays in sync.
      const speedKey = `${options.speed}`;
      if (speedChoice !== speedKey) {
        speedChoice = speedKey;
        speedChanges = [];
        speedStopped = false;
        lastSpeedAttempt = -Infinity;
      }
      let mismatch = Math.abs(video.playbackRate - options.speed) > 0.01;
      if (mismatch && !speedStopped && now - lastSpeedAttempt >= 1200) {
        lastSpeedAttempt = now;
        const container = video.closest(".xt_video_player_container") || document;
        const item = [...container.querySelectorAll("li[data-speed]")]
          .find(el => Number(el.dataset.speed) === options.speed);
        if (item) {
          const control = item.closest(".xt_video_player_speed, xt-speedbutton") || item.parentElement;
          const rect = control.getBoundingClientRect();
          const x = rect.left + 4, y = rect.top + 4;
          // The official PC player gates clicks on nonzero mouse movement within
          // its speed control. A bare item.click() leaves its stored speed at 1x.
          const dispatch = (type, dx = 0) => control.dispatchEvent(new MouseEvent(type, {
            bubbles: true, clientX: x + dx, clientY: y + dx, view: window,
          }));
          dispatch("mouseover");
          dispatch("mousemove", 6);
          item.click();
          dispatch("mouseout", 6);
        }
        speedChanges.push(now);
        if (Math.abs(video.playbackRate - options.speed) > 0.01) {
          video.defaultPlaybackRate = options.speed;
          video.playbackRate = options.speed;
        }
        // Stop fighting a player that really refuses the setting. Continue the
        // play/pause/stall checks below, and report the actual rate consistently.
        if (speedChanges.length >= 5) speedStopped = true;
      }
      detail.rate = video.playbackRate;
      detail.muted = video.muted;
      if (video.currentTime > sample.time + 0.05 || video.currentTime < sample.time - 0.5) {
        sample = { time: video.currentTime, at: now };
        attempts = 0;
        playError = "";
      }
      const idle = (now - sample.at) / 1000;
      if (video.paused) {
        if (!options.resumePaused && hasPlayed) return { ...detail, state: "paused", detail: "已关闭意外暂停恢复，可手动播放" };
        if (attempts >= 3) return { ...detail, state: "paused", detail: playError || "多次恢复后仍暂停，请恢复窗口检查" };
        if (options.resumePaused || attempts === 0) {
          if (now - lastAttempt >= 3000) {
            attempts++;
            lastAttempt = now;
            Promise.resolve(video.play()).catch(err => { playError = err.name; });
          }
        }
        if (playError === "NotAllowedError") {
          return { ...detail, state: "autoplay-blocked", detail: "浏览器要求手动点击一次播放" };
        }
        return { ...detail, state: "paused", detail: "正在尝试播放；也可在网页点击播放" };
      }
      hasPlayed = true;
      if (idle >= options.stallSeconds) {
        return { ...detail, state: "stalled", detail: "播放时间未推进，请检查网络或页面遮挡" };
      }
      if (speedStopped && Math.abs(video.playbackRate - options.speed) > 0.01) {
        return { ...detail, state: "speed-rejected", detail: `当前实际 ${video.playbackRate} 倍速；请在网页确认倍速菜单，可暂停后恢复助手重试` };
      }
      return { ...detail, state: video.readyState < 3 ? "buffering" : "playing" };
    },
    firstVideo() {
      if (blocked() || chooseVideo()) return false;
      const items = leaves();
      const selected = active();
      // Respect a user's manual selection of homework or an exam.
      if (selected && !videoLeaf(selected)) return false;
      const target = selected || items.find(videoLeaf);
      if (!target) return false;
      target.click();
      return true;
    },
    nextVideo(delaySeconds) {
      const video = chooseVideo();
      if (!video?.ended || blocked()) return { state: "not-ended" };
      if (nextAt === Infinity) return { state: "next-pending" };
      const items = leaves();
      const selected = active();
      if (!selected || !videoLeaf(selected)) return { state: "no-directory" };
      const index = items.indexOf(selected);
      const target = items.slice(index + 1).find(videoLeaf);
      if (!target) return { state: "complete" };
      const key = `${index}:${video.currentSrc || source}`;
      if (nextKey !== key) {
        nextKey = key;
        nextAt = performance.now();
        return { state: "next-wait" };
      }
      // A single click per ended video. Do not repeatedly click a failing route.
      if (performance.now() - nextAt < delaySeconds * 1000) return { state: "next-wait" };
      nextAt = Infinity;
      target.click();
      return { state: "next-clicked", title: title(target) };
    },
    pause() {
      disposed = true;
      for (const video of document.querySelectorAll("video")) video.pause();
    },
    resume() {
      disposed = false;
      hasPlayed = false;
      attempts = 0;
      playError = "";
      speedChanges = [];
      lastSpeedAttempt = -Infinity;
      speedStopped = false;
      sample.at = performance.now();
      lastAttempt = -Infinity;
    },
  };
})();
