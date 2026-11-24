Here is everything that has been done on Jasmehr's Amazon Fire TV Stick so far, and the key facts about it. This is background only, for your information.

THE DEVICE
- Fire TV Stick 3rd gen, model AFTSS, Fire OS 7.6.8.5, Android WebView 138. Used mainly by his mom.
- IP 192.168.100.156, ADB over Wi-Fi on port 5555, controlled from his Windows 11 laptop. adb is at C:\Users\jasme\platform-tools\adb.exe . ADB debugging is still on.
- Home network: Huawei HG8121H fibre router at 192.168.100.1, Wi-Fi "Mehr" (2.4 GHz only). Plan is 60 Mbps; one test on the laptop measured about 10 Mbps.
- HDMI-CEC is on, so waking the stick also turns the real TV on.
- Project folder on the laptop: C:\Users\jasme\OneDrive\Desktop\EyeToy and Buzz\ . The full change log with undo commands is "Fire TV changes.txt" in that folder.

2026-11-08: SPEED AND DEBLOAT
- Cleared app caches (storage went from 74% to 63% full).
- Featured content video and audio autoplay turned off.
- Privacy: device usage data, app usage data and interest-based ads all turned off. Amazon's protected background services could not be disabled.
- Uninstalled 19 apps: Netflix, Hotstar, JioCinema, SonyLIV, ZEE5, Voot, Discovery+, ALTBalaji, Eros Now, Lionsgate Play, MX Player, Aaj Tak, NDTV, PTC, Ludo King, Red Ball 4, Snake, Word Search, Picture Puzzle. Storage after: 34% full, 3.4 GB free.
- Installed SmartTube stable (org.smarttube.stable, from github.com/yuliskov/SmartTube) for YouTube without ads.
- Hid cloud tiles: News18 India, VETO, TED, Gaana, MX Player TV.

2026-11-09: HOME SCREEN
- Home launcher is "dumbtv home" (tv.dumbtv.home) from github.com/humblemuzzu/dumbtv, holding the HOME button.
- Its settings file is /sdcard/Android/data/tv.dumbtv.home/files/home.conf, with photos in the wallpapers/ folder beside it. A filename containing morning, afternoon or night puts the photo in that time-of-day slot.
- Backdrop: 9 mountain photos from his Ascend web app, rotating by time of day, 40% dim.
- Current home.conf:
  background=image, rotate=daytime, dim=40, tiles=medium, labels=focus, chips=icons, inputs=off, library=off
  apps.hide=com.amazon.bueller.music,com.amazon.minitv.android.app,com.amazon.firetv.troubleshooting,me.efesser.flauncher,com.amazon.spiderpork
  apps.order=local.sendtotv,org.smarttube.stable,com.amazon.firebat
- Backups on the laptop: backups\home.conf.before-ascend (old settings), backups\home.conf.ascend (current), backups\ascend-wallpapers\ (the 9 photos).
- Animations (window, transition, animator scale) set to 0. They were 1.0 originally, then 0.5.
- Disabled (not uninstalled): com.amazon.bueller.music (Amazon Music), com.amazon.minitv.android.app (miniTV), com.amazon.bueller.photos (Photos, which also ran the screensaver). If a Fire OS or Prime Video update fails, re-enabling these is the first fix.
- Uninstalled the official YouTube app (com.amazon.firetv.youtube). SmartTube replaces it.

2026-11-20: STREMIO
- Installed the official Stremio for Android TV 1.10.4 (com.stremio.one, armeabi-v7a) from dl.strem.io, signed CN=Ivo Georgiev, O=Stremio. APK kept in apks\stremio\. He has signed in.
- The Windows desktop Stremio 5.0.24 is also on the laptop (installer in apks\stremio-windows\).

SEND TO TV (his own app, version 1.4, package local.sendtotv)
- Source: C:\Users\jasme\OneDrive\Desktop\EyeToy and Buzz\send-to-tv\
- Built with build.ps1 (no Gradle: JDK 17, aapt2, javac, d8, apksigner), which runs a 120-check self test first. Output: dist\SendToTV.apk. Signed with keystore\sendtotv.p12; updates must use the same key.
- It is a full-screen web browser app for the TV plus a background service with a web server on port 8765. It starts on boot.
- Mom opens http://192.168.100.156:8765 on her phone. The TV start page shows a QR code that includes the PIN, and the phone remembers the PIN. From the phone she can send links, use a remote, pin sites, close pop-ups and switch a site to its desktop version.
- Main files: src\local\sendtotv\MainActivity.java (browser, remote keys, ad blocking, pop-up windows, full screen, DRM), StvService.java and Server.java (phone API: /api/open, /api/pin, /api/desktop, key presses), Store.java (recent, pins, desktop-site list), AdBlock.java, Text.java (QR code), assets\nav.js (remote navigation, pop-up cleaner, auto full screen), assets\drm.js, assets\start.html (TV start page), assets\phone.html (phone page), adlists\ (HaGeZi ad lists).
- Features:
  - Blocks ads and pop-ups (about 145,000 domains).
  - Pinned sites: hold OK on the remote to pin or unpin.
  - The Menu button closes pop-ups.
  - Videos go full screen by themselves when a real player appears. Muted previews and clips under 90 seconds are skipped.
  - Pressing Play/Pause twice toggles full screen.
  - The desktop-site switch is remembered per site.
- Black video fix: protected (Widevine L1) video showed black in the TV's browser. assets\drm.js makes sites use Widevine's software level (SW_SECURE_CRYPTO) and is loaded into every page and embedded frame at page start. This was confirmed working on a Vimeo protected video.
- Embedded players need allow="encrypted-media", or Vimeo shows "supported browser".

FACTS LEARNED WHILE TESTING
- The TV's browser always sends the header X-Requested-With: local.sendtotv, and Sec-CH-UA says "Android WebView". Two attempts to remove them failed; only the User-Agent string can be changed.
- Screenshots taken over adb black out protected video, so a black screenshot does not show what the TV really displays.
- When the TV screen is off, the app's web view pauses.
- Test pages can't be served from the laptop to the TV. Testing was done by opening https://example.com through the phone page and injecting test content.

ALREADY DECIDED
- Piracy sites, apps and add-ons (HiAnime, Zoro TV, Torrentio) were declined and are not on the TV through this work.

STILL OPEN
- Netflix and Crunchyroll in Send to TV have not been retested since the black video fix.
- The v1.4 changes have not had a code review.
