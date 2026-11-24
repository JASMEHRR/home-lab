package local.sendtotv;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.lang.reflect.InvocationHandler;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The TV screen: a full-screen web view driven entirely by the remote (or the phone page).
 * Arrow keys go to nav.js, which moves a highlight between links, buttons and videos.
 * OK becomes a real tap at the highlighted spot, so sites see a genuine click.
 *
 * Memory: the web engine is the heaviest thing on a 1 GB Fire TV. When the screen is left,
 * page work pauses at once and the screen closes after AWAY_CLOSE_MS (sooner if Android is short
 * of memory). The start page's Continue tile brings the last page back.
 *
 * Ads: requests to known ad and pop-up domains are refused (AdBlock), pop-up windows never open,
 * and nav.js hides layers that cover the page. Holding OK pins a page or tile to the start page.
 */
public final class MainActivity extends Activity {
    private static final String TAG = "SendToTV";
    private static final String START_BASE = "https://start.sendtotv.invalid/";
    private static final String MISSING = "\"__missing__\"";
    private static final int MATCH = ViewGroup.LayoutParams.MATCH_PARENT;
    private static final long AWAY_CLOSE_MS = 2 * 60_000L;
    private static final long HOLD_MS = 700;
    private static final long DOUBLE_PRESS_MS = 450;
    private static final long TAB_LINK_WAIT_MS = 300;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService disk = Executors.newSingleThreadExecutor();
    private final Runnable closeWhileAway = () -> {
        Log.i(TAG, "screen left for a while: closing it to free memory");
        finish();
    };

    private Store store;
    private FrameLayout root;
    private WebView web;
    private View customView;
    private WebChromeClient.CustomViewCallback customCallback;
    private String navJs;
    private String drmJs;
    private long lastPlayPress;
    private String startTemplate;
    private boolean onStartPage;
    private boolean visible;
    /** The ad list, loaded once per process (it outlives screen reopenings); null until loaded. */
    private static volatile AdBlock adList;
    private static final CountDownLatch AD_LIST_READY = new CountDownLatch(1);
    private static final AtomicBoolean AD_LIST_STARTED = new AtomicBoolean();
    private Set<String> desktopHosts = Collections.emptySet();
    private String mobileUa;
    private String desktopUa;
    /** The last page navigation the web view started, to tell new-tab links from links that already opened. */
    private String lastNavigation = "";
    private int navigations;
    private int navigationsAtPopup;
    private boolean holdFired;
    private final Runnable holdOk = () -> {
        holdFired = true;
        togglePinHere();
    };
    /** Receives the link under the last tap, for links that ask for a new tab (see Chrome.onCreateWindow). */
    private final Handler tabLinks = new Handler(Looper.getMainLooper(), msg -> {
        openTabLink(msg.getData().getString("url"));
        return true;
    });

    /** Phone buttons, relayed by StvService. Registered only while this screen is in front. */
    private final BroadcastReceiver remote = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String action = intent.getAction();
            if (Remote.ACTION_KEY.equals(action)) {
                String key = intent.getStringExtra(Remote.EXTRA_KEY);
                if (key != null) handle(key);
            } else if (Remote.ACTION_TEXT.equals(action)) {
                String text = intent.getStringExtra(Remote.EXTRA_TEXT);
                if (text != null) typeText(text, intent.getBooleanExtra(Remote.EXTRA_SUBMIT, false));
            }
            // ACTION_PING needs no work: answering at all proves the screen is open.
            if (isOrderedBroadcast()) setResultCode(Activity.RESULT_OK);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = Stores.open(this);
        try {
            navJs = Assets.text(this, "nav.js");
            drmJs = Assets.text(this, "drm.js");
            startTemplate = Assets.text(this, "start.html");
        } catch (IOException e) {
            throw new IllegalStateException("the APK is missing its assets", e);
        }

        // DevTools over ADB, only while ADB debugging is on (which already grants full control of the TV).
        WebView.setWebContentsDebuggingEnabled(
                Settings.Global.getInt(getContentResolver(), Settings.Global.ADB_ENABLED, 0) == 1);
        root = new FrameLayout(this);
        root.setBackgroundColor(0xff000000);
        setContentView(root);
        createWebView();

        if (AD_LIST_STARTED.compareAndSet(false, true)) {
            disk.execute(() -> {
                try (InputStream in = getAssets().open("adhosts.bin")) {
                    adList = AdBlock.read(in);
                    Log.i(TAG, "ad block list: " + adList.size() + " domains");
                } catch (IOException e) {
                    adList = AdBlock.EMPTY;
                    Log.e(TAG, "could not load the ad block list; ads will show", e);
                } finally {
                    AD_LIST_READY.countDown();
                }
            });
        }
        startForegroundService(new Intent(this, StvService.class));
        if (!openFromIntent(getIntent())) showStart();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        openFromIntent(intent);
    }

    @Override
    protected void onStart() {
        super.onStart();
        visible = true;
        main.removeCallbacks(closeWhileAway);
        if (web != null) web.resumeTimers();
        loadDesktopHosts();
        // Registered for the whole time the screen is visible, including when an overlay pauses it.
        IntentFilter filter = new IntentFilter();
        filter.addAction(Remote.ACTION_KEY);
        filter.addAction(Remote.ACTION_TEXT);
        filter.addAction(Remote.ACTION_PING);
        registerReceiver(remote, filter, Remote.PERMISSION, null);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override
    protected void onStop() {
        unregisterReceiver(remote);
        visible = false;
        if (!isChangingConfigurations()) {
            if (web != null) web.pauseTimers(); // stops page scripts and timers while away
            main.postDelayed(closeWhileAway, AWAY_CLOSE_MS);
        }
        super.onStop();
    }

    @Override
    public void onTrimMemory(int level) {
        super.onTrimMemory(level);
        if (!visible && level >= TRIM_MEMORY_BACKGROUND) {
            Log.i(TAG, "Android is short of memory: closing the screen now (level " + level + ")");
            finish();
        }
    }

    @Override
    protected void onDestroy() {
        main.removeCallbacks(closeWhileAway);
        destroyWebView();
        disk.shutdown(); // a pending save still finishes
        super.onDestroy();
    }

    private void createWebView() {
        web = new WebView(this);
        root.addView(web, 0, new FrameLayout.LayoutParams(MATCH, MATCH));
        configure(web);
        web.requestFocus();
    }

    private void destroyWebView() {
        if (web == null) return;
        removeCustomView();
        root.removeView(web);
        web.destroy();
        web = null;
    }

    private void configure(WebView w) {
        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        // New windows go through Chrome.onCreateWindow: links meant for a new tab open here, pop-ups never open.
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        // Some sites refuse in-app browsers and TVs: present as Chrome on a phone (no Fire TV model
        // name), or as Chrome on a PC for sites the phone switched to desktop mode.
        Matcher chrome = Pattern.compile("Chrome/(\\d+)").matcher(s.getUserAgentString());
        String major = chrome.find() ? chrome.group(1) : "138";
        mobileUa = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + major
                + ".0.0.0 Mobile Safari/537.36";
        desktopUa = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + major
                + ".0.0.0 Safari/537.36";
        s.setUserAgentString(mobileUa);
        addStartScript(w, drmJs);
        CookieManager.getInstance().setAcceptThirdPartyCookies(w, true);
        // Full priority while on screen; when hidden, Android may reclaim the page's memory
        // (onRenderProcessGone below then recovers).
        w.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, true);
        w.setBackgroundColor(0xff000000);
        w.setWebViewClient(new Client());
        w.setWebChromeClient(new Chrome());
    }

    private boolean openFromIntent(Intent intent) {
        if (intent == null || !Intent.ACTION_VIEW.equals(intent.getAction()) || web == null) return false;
        String url = Urls.normalize(intent.getDataString());
        if (url == null) return false;
        exitFullscreen();
        onStartPage = false;
        useAgentFor(url);
        web.loadUrl(url);
        return true;
    }

    private void showStart() {
        if (web == null) return;
        exitFullscreen();
        List<Store.Recent> recents;
        List<Store.Recent> pins;
        Store.Recent last;
        String pin;
        try {
            recents = store.recents();
            pins = store.pins();
            last = store.last();
            pin = store.pin();
        } catch (UncheckedIOException e) {
            Log.e(TAG, "could not read saved sites", e);
            recents = Collections.emptyList();
            pins = Collections.emptyList();
            last = null;
            pin = "????";
        }
        StringBuilder pinTiles = new StringBuilder();
        for (Store.Recent r : pins) pinTiles.append(tile(r, "tile pinned", null));
        StringBuilder tiles = new StringBuilder();
        if (last != null) tiles.append(tile(last, "tile continue", "Continue"));
        for (Store.Recent r : recents) tiles.append(tile(r, "tile", null));
        // The QR code carries the PIN too, so a phone that scans it never has to type it.
        String ip = Text.lanAddress();
        String addr = ip == null ? null : "http://" + ip + ":" + StvService.PORT;
        String qr = addr == null ? "<p class=\"offline\">Connect the TV to Wi-Fi to show the code.</p>"
                : Text.qrSvg(pin.matches("\\d{4,8}") ? addr + "/#pin=" + pin : addr);
        String html = startTemplate
                .replace("{{ADDR}}", Text.html(addr == null ? "(not on Wi-Fi)" : addr))
                .replace("{{PIN}}", Text.html(pin))
                .replace("{{QR}}", qr)
                .replace("{{PINS}}", pinTiles.length() == 0
                        ? "<p class=\"empty\">Hold OK on any page or tile to pin it here.</p>" : pinTiles.toString())
                .replace("{{TILES}}", tiles.length() == 0
                        ? "<p class=\"empty\">Sites you send will appear here.</p>" : tiles.toString());
        onStartPage = true;
        web.loadDataWithBaseURL(START_BASE, html, "text/html", "utf-8", null);
    }

    /** One start-page tile. A label (like "Continue") replaces the title, which then moves to the second line. */
    private static String tile(Store.Recent r, String cssClass, String label) {
        Uri u = Uri.parse(r.url);
        String host = u.getHost() == null ? r.url : u.getHost();
        String title = r.title.isEmpty() ? host : r.title;
        String heading = label == null ? title : label;
        String detail = label == null ? host : title;
        return "<a class=\"" + cssClass + "\" href=\"" + Text.html(r.url) + "\" data-title=\"" + Text.html(r.title)
                + "\"><b>" + Text.html(heading)
                + "</b><small>" + Text.html(detail) + "</small></a>";
    }

    // ---- input ----

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        String action = actionFor(e.getKeyCode());
        if (action == null) return super.dispatchKeyEvent(e);
        if ("ok".equals(action) && customView == null) {
            // A short press selects (on release); holding it pins.
            if (e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0) {
                holdFired = false;
                main.postDelayed(holdOk, HOLD_MS);
            } else if (e.getAction() == KeyEvent.ACTION_DOWN && e.isLongPress() && !holdFired) {
                main.removeCallbacks(holdOk); // Android already says it is a long press
                holdOk.run();
            } else if (e.getAction() == KeyEvent.ACTION_UP) {
                main.removeCallbacks(holdOk);
                if (!holdFired) handle("ok");
            }
            return true;
        }
        if ("play".equals(action) && e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0) {
            // Play/Pause twice quickly: full screen on or off. The second press also undoes the first's pause.
            long now = SystemClock.uptimeMillis();
            boolean twice = now - lastPlayPress < DOUBLE_PRESS_MS;
            lastPlayPress = twice ? 0 : now;
            handle("play");
            if (twice) handle("fullscreen");
            return true;
        }
        if (e.getAction() == KeyEvent.ACTION_DOWN && (e.getRepeatCount() == 0 || isArrow(action))) handle(action);
        return true;
    }

    static String actionFor(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP: return "up";
            case KeyEvent.KEYCODE_DPAD_DOWN: return "down";
            case KeyEvent.KEYCODE_DPAD_LEFT: return "left";
            case KeyEvent.KEYCODE_DPAD_RIGHT: return "right";
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_NUMPAD_ENTER: return "ok";
            case KeyEvent.KEYCODE_BACK: return "back";
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE: return "play";
            case KeyEvent.KEYCODE_MEDIA_REWIND: return "rew";
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD: return "ff";
            case KeyEvent.KEYCODE_MENU: return "menu";
            default: return null;
        }
    }

    private static boolean isArrow(String a) {
        return "up".equals(a) || "down".equals(a) || "left".equals(a) || "right".equals(a);
    }

    /** One entry point for the remote and the phone. Must run on the UI thread. */
    void handle(String action) {
        if (web == null) return;
        if (customView != null) {
            switch (action) {
                case "back": exitFullscreen(); return;
                case "fullscreen": exitFullscreen(); return;
                case "left": case "rew": js("media('rew')", null); return;
                case "right": case "ff": js("media('ff')", null); return;
                case "ok": case "play":
                    // A video the page can reach toggles directly; an embedded player gets a press in its middle.
                    js("media('toggle')", r -> {
                        View v = customView;
                        if ("\"none\"".equals(r) && v != null) tap(v, v.getWidth() / 2f, v.getHeight() / 2f);
                    });
                    return;
                default: return;
            }
        }
        switch (action) {
            case "up": case "down": case "left": case "right":
                js("move('" + action + "')", null);
                return;
            case "ok":
                js("ok()", this::onOkResult);
                return;
            case "back":
                js("back()", r -> { if (!"\"handled\"".equals(r)) goBack(); });
                return;
            case "play": js("media('toggle')", null); return;
            case "rew": js("media('rew')", null); return;
            case "ff": js("media('ff')", null); return;
            case "scrollup": js("scroll(-1)", null); return;
            case "scrolldown": js("scroll(1)", null); return;
            case "home": showStart(); return;
            case "fullscreen": js("toggleFullscreen()", null); return;
            case "reload": reload(); return;
            case "clean": js("clean()", null); return;
            // Menu closes pop-ups; with none to close it goes to the start page.
            case "menu": js("clean()", r -> { if (r == null || !r.matches("[1-9]\\d*")) showStart(); }); return;
            case "sites-changed": if (onStartPage) showStart(); return;
            default: Log.w(TAG, "unknown action " + action);
        }
    }

    /** Types text from the phone into the field that has focus on the page. Must run on the UI thread. */
    void typeText(String text, boolean submit) {
        js("text(" + JSONObject.quote(text) + "," + submit + ")", null);
    }

    private static AdBlock ads() {
        AdBlock list = adList;
        return list == null ? AdBlock.EMPTY : list;
    }

    /** Reloads the page, switching between the phone and desktop look if the phone changed it. */
    private void reload() {
        loadDesktopHosts();
        String url = web.getUrl();
        if (!onStartPage && url != null && url.startsWith("http") && useAgentFor(url)) web.loadUrl(url);
        else web.reload();
    }

    private void loadDesktopHosts() {
        try {
            desktopHosts = store.desktopHosts();
        } catch (UncheckedIOException e) {
            Log.w(TAG, "could not read desktop sites", e);
        }
    }

    /** Sets the user agent this url's site gets. Returns true when it changed (the page must load again). */
    private boolean useAgentFor(String url) {
        if (web == null) return false;
        String host = Uri.parse(url).getHost();
        String want = host != null && desktopHosts.contains(host.toLowerCase(Locale.ROOT)) ? desktopUa : mobileUa;
        WebSettings s = web.getSettings();
        if (want.equals(s.getUserAgentString())) return false;
        s.setUserAgentString(want);
        return true;
    }

    /** Hold OK: pins the highlighted start-page tile, or the page on screen; a second hold unpins. */
    private void togglePinHere() {
        WebView w = web;
        if (w == null || customView != null) return;
        if (onStartPage) {
            js("pinTarget()", r -> {
                if (r == null || !r.startsWith("{")) return;
                try {
                    JSONObject o = new JSONObject(r);
                    togglePin(o.getString("url"), o.optString("title"));
                } catch (JSONException e) {
                    Log.w(TAG, "bad pinTarget() result " + r, e);
                }
            });
            return;
        }
        String url = w.getUrl();
        if (url == null || !url.startsWith("http")) return;
        String title = w.getTitle();
        togglePin(url, title == null ? "" : title);
    }

    private void togglePin(String url, String title) {
        disk.execute(() -> {
            boolean pinned;
            try {
                pinned = store.togglePin(url, title);
            } catch (UncheckedIOException e) {
                Log.w(TAG, "could not save the pin", e);
                return;
            }
            main.post(() -> {
                Toast.makeText(this, pinned ? "Pinned to the start page" : "Unpinned", Toast.LENGTH_SHORT).show();
                if (onStartPage) showStart();
            });
        });
    }

    /** A link asked for a new tab. The TV has no tabs, so it opens here unless the page already went there. */
    private void openTabLink(String url) {
        if (url == null || !(url.startsWith("http://") || url.startsWith("https://"))) return;
        if (ads().blocks(Uri.parse(url).getHost())) {
            Log.i(TAG, "blocked an ad link " + url);
            return;
        }
        main.postDelayed(() -> {
            // The page went somewhere itself (a normal link whose click also fired a pop-up): leave it.
            if (web == null || navigations != navigationsAtPopup || url.equals(lastNavigation)) return;
            onStartPage = false;
            useAgentFor(url);
            web.loadUrl(url);
        }, TAB_LINK_WAIT_MS);
    }

    /**
     * Runs script at the start of every page and embedded frame, before the site's own scripts
     * (drm.js must be in place before a player asks for protected video). The system WebView offers
     * this through the hook the androidx.webkit library uses; this calls it the same way without
     * bundling the library. Without it, protected video stays black but everything else works.
     */
    private static void addStartScript(WebView w, String script) {
        try {
            ClassLoader cl = WebView.getWebViewClassLoader();
            String boundary = "org.chromium.support_lib_boundary.";
            InvocationHandler factory = (InvocationHandler) Class.forName("org.chromium.support_lib_glue.SupportLibReflectionUtil", false, cl)
                    .getDeclaredMethod("createWebViewProviderFactory").invoke(null);
            InvocationHandler provider = (InvocationHandler) factory.invoke(null,
                    Class.forName(boundary + "WebViewProviderFactoryBoundaryInterface", false, cl).getMethod("createWebView", WebView.class),
                    new Object[] {w});
            provider.invoke(null, Class.forName(boundary + "WebViewProviderBoundaryInterface", false, cl)
                    .getMethod("addDocumentStartJavaScript", String.class, String[].class), new Object[] {script, new String[] {"*"}});
        } catch (Throwable t) {
            Log.w(TAG, "could not add the start-of-page script; protected video may show black", t);
        }
    }

    private void goBack() {
        if (web == null) return;
        if (onStartPage) moveTaskToBack(true);
        else if (web.canGoBack()) web.goBack();
        else showStart();
    }

    /** Calls window.__stv.<call>; injects nav.js once and retries if the page has not got it yet. */
    private void js(String call, ValueCallback<String> done) {
        WebView w = web;
        if (w == null) return;
        String expr = "(function(){try{return window.__stv?window.__stv." + call
                + ":'__missing__';}catch(e){return '__error__:'+e;}})()";
        w.evaluateJavascript(expr, result -> {
            if (MISSING.equals(result)) {
                w.evaluateJavascript(navJs, ignored -> w.evaluateJavascript(expr, done));
            } else if (done != null) {
                done.onReceiveValue(result);
            }
        });
    }

    private void onOkResult(String result) {
        if (web == null || result == null || "null".equals(result) || !result.startsWith("{")) return;
        try {
            JSONObject o = new JSONObject(result);
            if (!o.has("x")) return;
            double viewportWidth = o.getDouble("vw");
            if (viewportWidth <= 0) return;
            float scale = (float) (web.getWidth() / viewportWidth);
            tap((float) o.getDouble("x") * scale, (float) o.getDouble("y") * scale);
            if (o.optBoolean("typing")) showKeyboard();
        } catch (JSONException e) {
            Log.w(TAG, "bad ok() result " + result, e);
        }
    }

    /** A real touch at (x, y) in view pixels, so the page treats it as a genuine user click. */
    private void tap(float x, float y) {
        tap(web, x, y);
    }

    private static void tap(View target, float x, float y) {
        long t = SystemClock.uptimeMillis();
        MotionEvent down = MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, x, y, 0);
        down.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        target.dispatchTouchEvent(down);
        down.recycle();
        MotionEvent up = MotionEvent.obtain(t, t + 60, MotionEvent.ACTION_UP, x, y, 0);
        up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        target.dispatchTouchEvent(up);
        up.recycle();
    }

    private void showKeyboard() {
        WebView w = web;
        if (w == null) return;
        w.requestFocus();
        InputMethodManager imm = getSystemService(InputMethodManager.class);
        w.postDelayed(() -> imm.showSoftInput(w, InputMethodManager.SHOW_IMPLICIT), 200);
    }

    /** Saves the page off the UI thread: the file write ends with a disk sync, slow on a Fire TV. */
    private void remember(String url, String title) {
        disk.execute(() -> {
            try {
                store.pageShown(url, title);
            } catch (UncheckedIOException e) {
                Log.w(TAG, "could not save the last page", e); // only the Continue tile is affected
            }
        });
    }

    // ---- full-screen video ----

    private void exitFullscreen() {
        if (customView == null) return;
        WebChromeClient.CustomViewCallback cb = customCallback;
        removeCustomView();
        if (cb != null) cb.onCustomViewHidden();
    }

    private void removeCustomView() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        customCallback = null;
        if (web != null) {
            web.setVisibility(View.VISIBLE);
            web.requestFocus();
        }
    }

    private final class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri u = request.getUrl();
            String scheme = u.getScheme();
            if ("about".equals(scheme)) return false;
            if (!"http".equals(scheme) && !"https".equals(scheme)) {
                // intent:, market:, whatsapp: and similar would try to leave the app; ad pages love them.
                Log.i(TAG, "blocked link " + u);
                return true;
            }
            if (!request.isForMainFrame()) return false;
            if (ads().blocks(u.getHost())) {
                Log.i(TAG, "blocked a jump to an ad site " + u.getHost());
                return true;
            }
            String url = u.toString();
            lastNavigation = url;
            navigations++;
            if (useAgentFor(url)) {
                // ponytail: a form post that crosses into a desktop-mode site arrives as a plain visit.
                view.loadUrl(url);
                return true;
            }
            return false;
        }

        /** Runs on a WebView worker thread: refuses anything from an ad or pop-up domain. */
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            if (request.isForMainFrame()) return null;
            AdBlock list = adList;
            if (list == null) {
                // The first page can start before the list is read; hold its requests briefly rather than let ads through.
                try {
                    AD_LIST_READY.await(3, TimeUnit.SECONDS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                list = ads();
            }
            if (!list.blocks(request.getUrl().getHost())) return null;
            return new WebResourceResponse("text/plain", "utf-8", 403, "Blocked",
                    Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            onStartPage = url != null && url.startsWith(START_BASE);
        }

        @Override
        public void onPageCommitVisible(WebView view, String url) {
            view.evaluateJavascript(navJs, null);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            view.evaluateJavascript(navJs, null);
            if (!onStartPage && url != null && url.startsWith("http")) {
                String title = view.getTitle();
                remember(url, title == null ? "" : title);
            }
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (!request.isForMainFrame()) return;
            String page = "<body style=\"background:#111;color:#eee;font:22px sans-serif;padding:60px\">"
                    + "<h2>Couldn't open this page</h2><p>" + Text.html(String.valueOf(error.getDescription()))
                    + "</p><p>" + Text.html(request.getUrl().toString()) + "</p>"
                    + "<p>Press Back to return.</p></body>";
            view.loadDataWithBaseURL(null, page, "text/html", "utf-8", null);
        }

        /**
         * The page's engine process ended: it crashed, or Android reclaimed its memory while the
         * screen was hidden. Without this the whole app would be killed with it.
         */
        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            Log.w(TAG, "web engine stopped (" + (detail.didCrash() ? "crashed" : "memory reclaimed") + ")");
            if (view != web) return true; // an older engine we already replaced
            destroyWebView();
            if (!visible) {
                finish();
                return true;
            }
            createWebView();
            showStart(); // its Continue tile reopens the page that was showing
            return true;
        }
    }

    private final class Chrome extends WebChromeClient {
        @Override
        public void onShowCustomView(View view, CustomViewCallback callback) {
            if (customView != null || web == null) {
                callback.onCustomViewHidden();
                return;
            }
            customView = view;
            customCallback = callback;
            root.addView(view, new FrameLayout.LayoutParams(MATCH, MATCH));
            web.setVisibility(View.INVISIBLE);
        }

        @Override
        public void onHideCustomView() {
            removeCustomView();
        }

        /**
         * Pop-ups and links that ask for a new tab. Only a link the viewer pressed opens (here: the TV
         * has no tabs); scripted windows, the usual pop-up ad trick, never open.
         */
        @Override
        public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
            WebView.HitTestResult hit = view.getHitTestResult();
            int type = hit == null ? WebView.HitTestResult.UNKNOWN_TYPE : hit.getType();
            if (isUserGesture && (type == WebView.HitTestResult.SRC_ANCHOR_TYPE
                    || type == WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE)) {
                navigationsAtPopup = navigations;
                view.requestFocusNodeHref(tabLinks.obtainMessage());
            } else {
                Log.i(TAG, "blocked a pop-up window");
            }
            return false;
        }

        @Override
        public void onPermissionRequest(PermissionRequest request) {
            // Protected (DRM) video needs this, or it plays as a black screen. Camera and microphone
            // are never needed for watching.
            for (String r : request.getResources()) {
                if (PermissionRequest.RESOURCE_PROTECTED_MEDIA_ID.equals(r)) {
                    request.grant(new String[] {PermissionRequest.RESOURCE_PROTECTED_MEDIA_ID});
                    return;
                }
            }
            request.deny();
        }

        @Override
        public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
            callback.invoke(origin, false, false);
        }

        @Override
        public boolean onConsoleMessage(ConsoleMessage message) {
            // Only nav.js's own messages ("[stv] ..."); page logs would flood the log.
            String text = message.message();
            if (text == null || !text.startsWith("[stv]")) return true;
            if ("[stv] need-key".equals(text)) {
                // A genuine key event renews the page's "user just pressed something" permission,
                // which full screen requires. nav.js catches this F11 and enters full screen.
                WebView w = web;
                if (w == null) return true;
                w.post(() -> {
                    long t = SystemClock.uptimeMillis();
                    w.dispatchKeyEvent(new KeyEvent(t, t, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_F11, 0));
                    w.dispatchKeyEvent(new KeyEvent(t, t + 30, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_F11, 0));
                });
                return true;
            }
            Log.i(TAG, text);
            return true;
        }

        @Override
        public Bitmap getDefaultVideoPoster() {
            // Avoids WebView's grey placeholder icon before a video starts.
            return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888);
        }
    }
}
