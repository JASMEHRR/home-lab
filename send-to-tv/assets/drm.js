/*
 * Send to TV: protected (Widevine) video in software mode.
 * On this Fire TV the web engine can't show hardware-protected (L1) video: it plays as a black
 * picture. This asks for Widevine's standard software level (L3) instead, which draws normally.
 * The site's licence server still decides whether, and at what quality, to allow playback.
 * Runs at the start of every page and embedded frame, before the site's own scripts.
 */
(function () {
  'use strict';
  var nav = window.navigator;
  if (!nav.requestMediaKeySystemAccess || nav.__stvSoftDrm) return;
  var original = nav.requestMediaKeySystemAccess.bind(nav);

  function copy(o) {
    var out = {};
    for (var k in o) out[k] = o[k];
    return out;
  }

  function soft(caps) {
    if (!caps) return caps;
    return Array.prototype.map.call(caps, function (c) {
      var o = copy(c);
      o.robustness = 'SW_SECURE_CRYPTO';
      return o;
    });
  }

  nav.requestMediaKeySystemAccess = function (keySystem, configs) {
    if (/widevine/i.test(String(keySystem)) && configs) {
      configs = Array.prototype.map.call(configs, function (cfg) {
        var o = copy(cfg);
        if (cfg.videoCapabilities) o.videoCapabilities = soft(cfg.videoCapabilities);
        if (cfg.audioCapabilities) o.audioCapabilities = soft(cfg.audioCapabilities);
        return o;
      });
    }
    return original(keySystem, configs);
  };
  nav.__stvSoftDrm = true;
})();
