/*
 * Send to TV: remote navigation for any web page.
 * Injected into every page by MainActivity. Exposes window.__stv, which the app
 * calls for each remote button. Arrow keys move a highlight to the nearest
 * clickable thing in that direction (no mouse cursor); OK returns where to tap;
 * videos that start playing go full screen together with their player (so
 * subtitles drawn by the site stay visible). Ad layers that cover the page are
 * hidden: on OK when one sits over the highlighted spot, on Back, and on clean().
 */
(function () {
  'use strict';
  if (window.__stv) return;

  var SELECTOR = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary', 'video', 'iframe',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]', '[role=option]',
    '[role=checkbox]', '[role=switch]', '[tabindex]:not([tabindex="-1"])', '[onclick]',
    '[contenteditable=""]', '[contenteditable=true]'
  ].join(',');
  var EDGE = 60;          // px kept clear when scrolling the highlight into view
  var FS_CLASS = '__stv_fs';

  var cur = null;
  var watching = false;   // a big video is playing: hide the ring until the next arrow press
  var ring = document.createElement('div');
  ring.setAttribute('aria-hidden', 'true');
  ring.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;box-sizing:border-box;' +
    'border:4px solid #4da3ff;border-radius:10px;display:none;' +
    'box-shadow:0 0 0 3px rgba(0,0,0,.7),0 0 22px 4px rgba(77,163,255,.9);' +
    'transition:left .12s,top .12s,width .12s,height .12s';
  var style = document.createElement('style');
  style.textContent = '.' + FS_CLASS + '{position:fixed!important;left:0!important;top:0!important;' +
    'width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;' +
    'margin:0!important;z-index:2147483646!important;background:#000!important;transform:none!important}' +
    'video.' + FS_CLASS + '{object-fit:contain!important}';

  function mount() {
    var host = document.body || document.documentElement;
    if (!host) return;
    if (!ring.parentNode) host.appendChild(ring);
    if (!style.parentNode) (document.head || host).appendChild(style);
  }

  function rectOf(el) { return el.getBoundingClientRect(); }

  function inView(r) {
    return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  }

  /** Not hidden by CSS. checkVisibility (Chromium 105+) is cheaper than getComputedStyle; both option spellings are passed. */
  function shown(el) {
    if (el.checkVisibility) {
      return el.checkVisibility({ opacityProperty: true, visibilityProperty: true, checkOpacity: true, checkVisibilityCSS: true });
    }
    var cs = getComputedStyle(el);
    return !(cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0);
  }

  /**
   * Clickable things near the screen (two screens up/down, and to the right for carousels), each
   * measured once: measuring is the slow part on a TV's processor, so off-screen elements are
   * dropped after reading as few edges as possible. Returns [{el, r}], r a plain box object.
   */
  function candidates() {
    var all = document.querySelectorAll(SELECTOR), out = [];
    var vw = innerWidth, vh = innerHeight;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el === ring || el.disabled) continue;
      var b = el.getBoundingClientRect();
      var top = b.top;
      if (top > vh * 3) continue;
      var bottom = b.bottom;
      if (bottom < -vh * 2) continue;
      var left = b.left;
      if (left > vw * 3) continue;
      var right = b.right;
      if (right < -vw) continue;
      if (right - left < 6 || bottom - top < 6) continue;
      if (!shown(el)) continue;
      out.push({ el: el, r: { left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top } });
    }
    return out;
  }

  /** Where a move starts: the highlighted element, or the screen edge we are moving away from. */
  function origin(dir) {
    if (cur && document.contains(cur)) {
      var r = rectOf(cur);
      if (inView(r)) return r;
    }
    var w = innerWidth, h = innerHeight;
    if (dir === 'down') return { left: 0, right: w, top: -1, bottom: -1, width: w, height: 0 };
    if (dir === 'up') return { left: 0, right: w, top: h + 1, bottom: h + 1, width: w, height: 0 };
    if (dir === 'right') return { left: -1, right: -1, top: 0, bottom: h, width: 0, height: h };
    return { left: w + 1, right: w + 1, top: 0, bottom: h, width: 0, height: h };
  }

  /** Lower is better; Infinity when b is not in that direction from a. */
  function score(dir, a, b) {
    var ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    var bx = b.left + b.width / 2, by = b.top + b.height / 2;
    var gap, cross, overlap;
    if (dir === 'down') {
      if (by - ay <= 1 || b.bottom <= a.bottom - 1) return Infinity;
      gap = Math.max(0, b.top - a.bottom); cross = Math.abs(bx - ax);
      overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    } else if (dir === 'up') {
      if (ay - by <= 1 || b.top >= a.top + 1) return Infinity;
      gap = Math.max(0, a.top - b.bottom); cross = Math.abs(bx - ax);
      overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    } else if (dir === 'right') {
      if (bx - ax <= 1 || b.right <= a.right - 1) return Infinity;
      gap = Math.max(0, b.left - a.right); cross = Math.abs(by - ay);
      overlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    } else {
      if (ax - bx <= 1 || b.left >= a.left + 1) return Infinity;
      gap = Math.max(0, a.left - b.right); cross = Math.abs(by - ay);
      overlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    }
    return gap + cross * (overlap > 0 ? 0.25 : 1.5);
  }

  /** The best candidate in that direction as {el, r}, or null. */
  function pick(dir) {
    var from = origin(dir), best = null, bestScore = Infinity, list = candidates();
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (cur && (c.el === cur || c.el.contains(cur) || cur.contains(c.el))) continue;
      var s = score(dir, from, c.r);
      if (s < bestScore) { bestScore = s; best = c; }
    }
    return best;
  }

  function draw() {
    if (watching || !cur || !document.contains(cur)) { ring.style.display = 'none'; return; }
    var r = rectOf(cur);
    if (!inView(r)) { ring.style.display = 'none'; return; }
    ring.style.display = 'block';
    ring.style.left = (r.left - 6) + 'px';
    ring.style.top = (r.top - 6) + 'px';
    ring.style.width = (r.width + 12) + 'px';
    ring.style.height = (r.height + 12) + 'px';
  }

  var drawQueued = false;
  function queueDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(function () { drawQueued = false; draw(); });
  }
  addEventListener('scroll', queueDraw, { capture: true, passive: true });
  addEventListener('resize', queueDraw, { passive: true });

  function setCur(el) {
    cur = el;
    // Text fields are only focused on OK: focusing them early opens site pop-ups (search suggestions).
    if (!isTyping(el)) {
      try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable; the ring still shows */ }
    }
    var r = rectOf(el);
    if (r.top < EDGE || r.bottom > innerHeight - EDGE || r.left < 0 || r.right > innerWidth) {
      // Instant, not smooth: smooth scrolling stutters on a Fire TV's graphics chip.
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'auto' });
    }
    mount();
    draw();
  }

  /** Nearest ancestor that scrolls along axis 'x' or 'y'. */
  function scrollerOf(el, axis) {
    for (var e = el && el.parentElement; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      var cs = getComputedStyle(e), ov = axis === 'x' ? cs.overflowX : cs.overflowY;
      var room = axis === 'x' ? e.scrollWidth > e.clientWidth + 4 : e.scrollHeight > e.clientHeight + 4;
      if ((ov === 'auto' || ov === 'scroll') && room) return e;
    }
    return null;
  }

  /** The page's main vertical scroller: the document, or the biggest scrolling box (app-style sites). */
  function mainScroller() {
    var doc = document.scrollingElement || document.documentElement;
    if (doc.scrollHeight > doc.clientHeight + 4) return doc;
    var best = null, area = 0, all = document.querySelectorAll('div,main,section');
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.scrollHeight <= e.clientHeight + 4) continue;
      var ov = getComputedStyle(e).overflowY;
      if (ov !== 'auto' && ov !== 'scroll') continue;
      var a = e.clientWidth * e.clientHeight;
      if (a > area) { area = a; best = e; }
    }
    return best || doc;
  }

  function scrollPage(sign) {
    var s = scrollerOf(cur, 'y') || mainScroller();
    s.scrollBy({ top: sign * innerHeight * 0.6, behavior: 'auto' });
    if (cur && !inView(rectOf(cur))) cur = null;
    draw();
    return 'scrolled';
  }

  function move(dir) {
    mount();
    watching = false;
    var next = pick(dir);
    if (next) {
      var r = next.r, o = origin(dir);
      var far = (dir === 'down' && r.top - o.bottom > innerHeight * 0.9) ||
                (dir === 'up' && o.top - r.bottom > innerHeight * 0.9);
      if (!far) { setCur(next.el); return 'moved'; }
    }
    if (dir === 'left' || dir === 'right') {
      var sx = scrollerOf(cur, 'x');
      if (!sx) return 'edge';
      sx.scrollBy({ left: (dir === 'right' ? 1 : -1) * sx.clientWidth * 0.8, behavior: 'auto' });
      draw();
      return 'scrolled';
    }
    return scrollPage(dir === 'down' ? 1 : -1);
  }

  function isTyping(el) {
    if (el.isContentEditable || el.tagName === 'TEXTAREA') return true;
    if (el.tagName !== 'INPUT') return false;
    return !/^(button|submit|reset|checkbox|radio|range|color|file|image)$/i.test(el.type || '');
  }

  // ---- ad layers ----

  /** The fixed-position box that el sits in, or null when el is part of the normal page. */
  function fixedLayer(el) {
    for (var e = el; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      if (getComputedStyle(e).position === 'fixed') return e;
    }
    return null;
  }

  /**
   * A fixed box that blocks the page: covers at least a quarter of the screen, is not our own
   * full-screen player, and holds less than half of the page's clickable things (so a site whose
   * whole app lives in one fixed box is never taken for an ad).
   */
  function isBlocker(layer) {
    if (!layer || layer.classList.contains(FS_CLASS) || layer === fullscreenElement()) return false;
    var r = rectOf(layer);
    var w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
    var h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    if (w <= 0 || h <= 0 || w * h < innerWidth * innerHeight * 0.25) return false;
    var vids = layer.querySelectorAll('video');
    for (var i = 0; i < vids.length; i++) if (!vids[i].paused && !vids[i].muted) return false; // muted = a video ad
    var inside = layer.querySelectorAll(SELECTOR).length;
    return inside * 2 < document.querySelectorAll(SELECTOR).length;
  }

  function hideLayer(layer) {
    layer.style.setProperty('display', 'none', 'important');
    console.log('[stv] hid a covering layer');
    // If the site later shows it again on purpose (its menu or search box), let it.
    if (window.MutationObserver) {
      var mo = new MutationObserver(function () { layer.style.removeProperty('display'); mo.disconnect(); });
      mo.observe(layer, { attributes: true, attributeFilter: ['class', 'open', 'hidden'] });
    }
    // Pop-ups often freeze scrolling underneath; let the page scroll again.
    [document.documentElement, document.body].forEach(function (e) {
      if (e && getComputedStyle(e).overflowY === 'hidden') e.style.setProperty('overflow', 'auto', 'important');
    });
  }

  /** Blocking layers on top at a grid of points across the screen. */
  function blockers() {
    var found = [];
    for (var gx = 1; gx <= 3; gx++) {
      for (var gy = 1; gy <= 3; gy++) {
        var layer = fixedLayer(document.elementFromPoint(innerWidth * gx / 4, innerHeight * gy / 4));
        if (layer && found.indexOf(layer) < 0 && isBlocker(layer)) found.push(layer);
      }
    }
    return found;
  }

  /**
   * Hides every layer covering the page (Back, the remote's Menu button, the phone's "Close pop-ups").
   * A few rounds, because pop-ups stack (a dark backdrop under the ad box). Returns how many.
   */
  function clean() {
    var count = 0;
    for (var round = 0; round < 3; round++) {
      var list = blockers();
      if (!list.length) break;
      for (var i = 0; i < list.length; i++) hideLayer(list[i]);
      count += list.length;
    }
    draw();
    return count;
  }

  /**
   * Hides ad layers lying over (x, y) that are not the highlighted element, so a tap reaches what is
   * highlighted instead of an invisible ad. Smaller covers (a site's own player buttons, headers) stay.
   */
  function clearPath(x, y) {
    for (var tries = 0; tries < 5; tries++) {
      var hit = document.elementFromPoint(x, y);
      if (!hit || hit === cur || cur.contains(hit) || hit.contains(cur)) return;
      var layer = fixedLayer(hit);
      if (!layer || layer.contains(cur) || !isBlocker(layer)) return;
      hideLayer(layer);
    }
  }

  /** Returns where to tap (CSS px) for the highlighted element, or null after just highlighting something. */
  function ok() {
    mount();
    if (document.querySelector('.' + FS_CLASS)) {
      // The player fills the window: OK presses its middle (play/pause, or an embedded player's play button).
      return { x: innerWidth / 2, y: innerHeight / 2, vw: innerWidth, typing: false };
    }
    if (!cur || !document.contains(cur) || !inView(rectOf(cur))) {
      var first = pick('down');
      if (first) setCur(first.el);
      return null;
    }
    var r = rectOf(cur);
    var x = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1);
    var y = Math.min(Math.max(r.top + r.height / 2, 1), innerHeight - 1);
    clearPath(x, y);
    return { x: x, y: y, vw: innerWidth, typing: isTyping(cur) };
  }

  /** The start-page tile that is highlighted, as {url, title}, for pinning with a long OK press. */
  function pinTarget() {
    var a = cur && cur.closest && cur.closest('a.tile');
    return a ? { url: a.href, title: a.getAttribute('data-title') || '' } : null;
  }

  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function back() {
    if (fullscreenElement()) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      return 'handled';
    }
    var pseudo = document.querySelector('.' + FS_CLASS);
    if (pseudo) { pseudo.classList.remove(FS_CLASS); return 'handled'; }
    if (clean() > 0) return 'handled';
    return 'none';
  }

  /** The video the media buttons act on: the full-screen one, a playing one, else the biggest. */
  function targetVideo() {
    var fs = fullscreenElement();
    if (fs) {
      if (fs.tagName === 'VIDEO') return fs;
      var inside = fs.querySelector('video');
      if (inside) return inside;
      if (fs.tagName === 'IFRAME') return null; // an embedded player: the app presses its middle instead
    }
    var vs = document.querySelectorAll('video'), best = null, area = 0;
    for (var i = 0; i < vs.length; i++) {
      var v = vs[i], r = rectOf(v), a = r.width * r.height;
      if (!v.paused && a > 0) return v;
      if (a > area) { area = a; best = v; }
    }
    return best;
  }

  function media(action) {
    var v = targetVideo();
    if (!v) return 'none';
    if (action === 'toggle') {
      if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function () {}); } else v.pause();
    } else if (action === 'ff') {
      var end = isFinite(v.duration) ? v.duration : v.currentTime + 10;
      v.currentTime = Math.min(end, v.currentTime + 10);
    } else if (action === 'rew') {
      v.currentTime = Math.max(0, v.currentTime - 10);
    }
    return 'ok';
  }

  var PLAYER_NAME = /player|video-js|vjs|jw-|plyr|shaka|bitmovin|dplayer|art-video|html5-video/i;

  /**
   * The player box around a video, so its controls and subtitles come along into full screen.
   * Prefers an ancestor named like a player; otherwise the highest ancestor that is almost exactly
   * the video's size (looser matches swallow site dialogs and page frames).
   */
  function playerOf(v) {
    var vr = rectOf(v), box = v, named = null;
    for (var e = v.parentElement, depth = 0; e && e !== document.body && depth < 8; e = e.parentElement, depth++) {
      var r = rectOf(e);
      var label = (e.getAttribute('class') || '') + ' ' + (e.id || '');
      if (!named && PLAYER_NAME.test(label) && r.width <= vr.width * 1.3 + 8 && r.height <= vr.height * 1.3 + 8) named = e;
      if (r.width <= vr.width * 1.05 + 8 && r.height <= vr.height * 1.12 + 8) box = e;
      else break;
    }
    return named || box;
  }

  // 'play' fires the moment playback is requested, still inside the few seconds in which the
  // browser accepts a full-screen request after a click. 'playing' (after buffering) is often too late.
  function videoStarted(ev) {
    var v = ev.target;
    if (!v || v.tagName !== 'VIDEO' || v.muted || v.paused) return;  // muted autoplay = previews/ads
    var r = rectOf(v), d = v.duration;
    if (r.width < innerWidth * 0.35) return;                         // small inline players
    if (isFinite(d) && d < 90) return;                               // short clips and ads (unknown length passes)
    watching = true;
    draw();
    if (fullscreenElement() || document.querySelector('.' + FS_CLASS)) return;
    requestFullscreenFor(playerOf(v));
  }
  document.addEventListener('play', videoStarted, true);
  document.addEventListener('volumechange', videoStarted, true);     // started muted, then the sound came on

  var pendingFullscreen = null;

  /** Full screen now if a button was pressed recently; otherwise via a fresh key press from the app. */
  function requestFullscreenFor(box) {
    var active = navigator.userActivation ? navigator.userActivation.isActive : true;
    if (active) { goFullscreen(box); return; }
    // The browser only allows full screen shortly after a button press. Ask the app for a genuine
    // key press (F11); the keydown listener below goes full screen inside it.
    pendingFullscreen = box;
    console.log('[stv] need-key');
    // If the key press never arrives (the keyboard focus is inside another site's frame), fill the window instead.
    setTimeout(function () {
      if (pendingFullscreen !== box) return;
      pendingFullscreen = null;
      if (document.contains(box)) box.classList.add(FS_CLASS);
    }, 1500);
  }

  /**
   * Play/Pause pressed twice: full screen off if anything is full screen, otherwise on for the
   * biggest video or embedded player on screen, else the highlighted element, else the whole page.
   */
  function toggleFullscreen() {
    if (fullscreenElement()) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return 'off'; }
    var pseudo = document.querySelector('.' + FS_CLASS);
    if (pseudo) { pseudo.classList.remove(FS_CLASS); return 'off'; }
    var best = null, bestArea = 0, list = document.querySelectorAll('video,iframe');
    for (var i = 0; i < list.length; i++) {
      var r = rectOf(list[i]), a = visibleShare(r) * r.width * r.height;
      if (a > bestArea && shown(list[i])) { bestArea = a; best = list[i]; }
    }
    var box = best ? (best.tagName === 'VIDEO' ? playerOf(best) : best)
      : (cur && document.contains(cur) ? cur : document.documentElement);
    if (best && autoDone) autoDone.add(best);
    watching = true;
    draw();
    requestFullscreenFor(box);
    return 'on';
  }

  // ---- full screen as soon as a video appears ----

  var autoDone = window.WeakSet ? new WeakSet() : null;  // players already handled: Back out of one and it stays out
  var PLAYER_SRC = /embed|player|video|stream|watch|vod|episode|play/i;

  /** How much of r is on screen, 0 to 1. */
  function visibleShare(r) {
    var w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
    var h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    return w <= 0 || h <= 0 ? 0 : (w * h) / (r.width * r.height);
  }

  /**
   * The main player on screen, as the box to make full screen: a big video that is waiting to
   * play or playing with sound, or a big embedded player frame. Silent autoplaying or looping
   * videos (home-page previews) and short clips (ads) are skipped.
   */
  function autoPlayer() {
    var list = document.querySelectorAll('video,iframe');
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (autoDone.has(el)) continue;
      var r = rectOf(el);
      if (r.width < innerWidth * 0.5 || r.height < innerHeight * 0.25 || visibleShare(r) < 0.6 || !shown(el)) continue;
      if (el.tagName === 'VIDEO') {
        if (el.muted && (el.autoplay || el.loop || !el.paused)) continue; // silent preview, even before it starts
        var d = el.duration;
        if (isFinite(d) && d > 0 && d < 90) continue;
        return { el: el, box: playerOf(el) };
      }
      var canFill = el.hasAttribute('allowfullscreen') || /fullscreen/i.test(el.getAttribute('allow') || '');
      if (canFill && PLAYER_SRC.test(el.src || '')) return { el: el, box: el };
    }
    return null;
  }

  // ponytail: a 1 s poll, not observers; one cheap query per second, and paused with the page when the app is hidden.
  if (autoDone) {
    setInterval(function () {
      if (fullscreenElement() || pendingFullscreen || document.querySelector('.' + FS_CLASS)) return;
      var p = autoPlayer();
      if (!p) return;
      autoDone.add(p.el);
      watching = true;
      draw();
      console.log('[stv] video on screen: full screen');
      requestFullscreenFor(p.box);
    }, 1000);
  }

  function goFullscreen(box) {
    var request = box.requestFullscreen || box.webkitRequestFullscreen;
    var fallback = function (why) {
      console.log('[stv] full screen refused (' + why + '), filling the window instead');
      box.classList.add(FS_CLASS);
    };
    if (!request) { fallback('no API'); return; }
    try {
      var p = request.call(box);
      if (p && p.then) p.then(function () { console.log('[stv] full screen on'); }, function (e) { fallback(e && e.message); });
    } catch (e) { fallback(e.message); }
  }

  addEventListener('keydown', function (e) {
    if (e.key !== 'F11' || !pendingFullscreen) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var box = pendingFullscreen;
    pendingFullscreen = null;
    if (document.contains(box)) goFullscreen(box);
  }, true);

  /** Puts text from the phone into the focused field (React/Vue-safe), optionally pressing Enter. */
  function text(value, submit) {
    var el = document.activeElement;
    if (!el || !isTyping(el)) {
      if (cur && isTyping(cur)) { el = cur; el.focus(); } else return 'no-field';
    }
    if (el.isContentEditable) {
      el.textContent = value;
    } else {
      var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    if (submit) {
      var k = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
      var allowed = el.dispatchEvent(new KeyboardEvent('keydown', k));
      el.dispatchEvent(new KeyboardEvent('keypress', k));
      el.dispatchEvent(new KeyboardEvent('keyup', k));
      if (allowed && el.form) {
        if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
      }
    }
    return 'ok';
  }

  window.__stv = {
    move: move,
    ok: ok,
    back: back,
    media: media,
    text: text,
    scroll: scrollPage,
    clean: clean,
    pinTarget: pinTarget,
    toggleFullscreen: toggleFullscreen
  };
})();
