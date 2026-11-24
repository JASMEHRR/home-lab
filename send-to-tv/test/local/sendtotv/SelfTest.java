package local.sendtotv;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Checks the parts of Send to TV that do not need Android: link checking, the HTTP reader,
 * the PIN guard, the shared state file and the server end to end. Run by build.ps1; a failure
 * stops the build.
 */
public final class SelfTest {
    private static int passed;
    private static final List<String> failures = Collections.synchronizedList(new ArrayList<>());

    public static void main(String[] args) throws Exception {
        urls();
        http();
        guard();
        store();
        server();
        adBlock();
        text();
        qr();
        System.out.println("SelfTest: " + passed + " passed, " + failures.size() + " failed");
        for (String f : failures) System.out.println("  FAIL " + f);
        if (!failures.isEmpty()) System.exit(1);
    }

    private static void check(String name, boolean ok) {
        if (ok) passed++;
        else failures.add(name);
    }

    private static void eq(String name, Object want, Object got) {
        check(name + " (want " + want + ", got " + got + ")", want == null ? got == null : want.equals(got));
    }

    private static void urls() {
        eq("plain https", "https://www.viki.com/tv/123", Urls.normalize("https://www.viki.com/tv/123"));
        eq("bare domain gets https", "https://viki.com/tv/1", Urls.normalize("  viki.com/tv/1 "));
        eq("link inside shared text", "https://www.viki.com/videos/1-x",
                Urls.normalize("Watch this on Viki! https://www.viki.com/videos/1-x enjoy"));
        eq("uppercase scheme", "HTTPS://Example.com/a", Urls.normalize("HTTPS://Example.com/a"));
        eq("lan host with port", "http://nas:8096/web", Urls.normalize("http://nas:8096/web"));
        eq("ipv6", "http://[fe80::1]:8080/", Urls.normalize("http://[fe80::1]:8080/"));
        eq("odd but valid path chars", "https://example.com/a|b{c}", Urls.normalize("https://example.com/a|b{c}"));
        eq("null", null, Urls.normalize(null));
        eq("empty", null, Urls.normalize("   "));
        eq("javascript scheme", null, Urls.normalize("javascript:alert(1)"));
        eq("file scheme", null, Urls.normalize("file:///sdcard/x"));
        eq("intent scheme", null, Urls.normalize("intent://x#Intent;end"));
        eq("scheme only", null, Urls.normalize("http://"));
        eq("user info refused", null, Urls.normalize("https://user@evil.com/"));
        eq("single word", null, Urls.normalize("hello"));
        eq("sentence without link", null, Urls.normalize("watch this later"));
        eq("bad port", null, Urls.normalize("http://example.com:99999/"));
        StringBuilder huge = new StringBuilder("https://example.com/");
        while (huge.length() <= Urls.MAX_LENGTH) huge.append('a');
        eq("too long", null, Urls.normalize(huge.toString()));
    }

    private static Http.Request parse(String raw) throws IOException {
        return Http.read(new ByteArrayInputStream(raw.getBytes(StandardCharsets.ISO_8859_1)));
    }

    private static int status(String raw) {
        try {
            parse(raw);
            return 200;
        } catch (Http.BadRequest e) {
            return e.status;
        } catch (IOException e) {
            return -1;
        }
    }

    private static void http() throws IOException {
        Http.Request r = parse("POST /api/open?x=1 HTTP/1.1\r\nHost: tv\r\nContent-Length: 30\r\n\r\npin=1234&url=https%3A%2F%2Fa.b");
        eq("method", "POST", r.method);
        eq("path", "/api/open", r.path);
        eq("query", "1", r.query.get("x"));
        eq("form pin", "1234", r.form().get("pin"));
        eq("form url decoded", "https://a.b", r.form().get("url"));
        check("closed before sending is null", Http.read(new ByteArrayInputStream(new byte[0])) == null);
        eq("truncated headers", 400, status("GET / HTTP/1.1\r\nHost: x"));
        eq("bad request line", 400, status("HELLO\r\n\r\n"));
        eq("chunked refused", 501, status("POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"));
        eq("bad length", 400, status("POST / HTTP/1.1\r\nContent-Length: abc\r\n\r\n"));
        eq("negative length", 400, status("POST / HTTP/1.1\r\nContent-Length: -5\r\n\r\n"));
        eq("body too large", 413, status("POST / HTTP/1.1\r\nContent-Length: 999999\r\n\r\n"));
        eq("body shorter than length", 400, status("POST / HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc"));
        StringBuilder big = new StringBuilder("GET / HTTP/1.1\r\n");
        while (big.length() < Http.MAX_HEADER_BYTES + 10) big.append("X-Pad: aaaaaaaaaaaaaaaaaaaaaaaa\r\n");
        eq("headers too large", 431, status(big.append("\r\n").toString()));
        boolean badEncoding = false;
        try {
            Http.parseForm("a=%ZZ");
        } catch (Http.BadRequest e) {
            badEncoding = e.status == 400;
        }
        check("bad percent-encoding rejected", badEncoding);
        eq("json escaping", "\"a\\\"b\\\\c\\n\\u003c/script\\u003e\"", Http.jsonString("a\"b\\c\n</script>"));
    }

    private static void guard() {
        long[] now = {0};
        Guard g = new Guard("4321", () -> now[0]);
        eq("right pin", Guard.Result.OK, g.check("a", "4321"));
        eq("missing pin", Guard.Result.WRONG, g.check("a", null));
        for (int i = 0; i < Guard.MAX_FAILS - 2; i++) g.check("a", "0000");
        eq("fifth wrong try still WRONG", Guard.Result.WRONG, g.check("a", "0000"));
        eq("locked after five", Guard.Result.LOCKED, g.check("a", "4321"));
        eq("other address unaffected", Guard.Result.OK, g.check("b", "4321"));
        now[0] += Guard.LOCK_MS + 1;
        eq("unlocked after a minute", Guard.Result.OK, g.check("a", "4321"));
        now[0] += 1;
        g.check("c", "1");
        now[0] += Guard.WINDOW_MS + 1;
        for (int i = 0; i < Guard.MAX_FAILS - 1; i++) g.check("c", "1");
        eq("old failures expire", Guard.Result.OK, g.check("c", "4321"));
    }

    private static void store() throws IOException, InterruptedException {
        File dir = Files.createTempDirectory("stv-store").toFile();
        File f = new File(dir, "state.txt");
        Store a = new Store(f);
        Store b = new Store(f); // a second process's view of the same file
        String pin = a.pin();
        check("pin is 4 digits: " + pin, pin.matches("\\d{4}"));
        eq("pin is the same for both processes", pin, b.pin());
        b.adoptPin("1111");
        eq("adoptPin keeps an existing pin", pin, a.pin());

        Store fresh = new Store(new File(dir, "fresh.txt"));
        fresh.adoptPin("abc");
        fresh.adoptPin("2468");
        eq("adoptPin ignores junk and sets a missing pin", "2468", fresh.pin());

        check("nothing shown yet", a.last() == null && a.recents().isEmpty());
        a.addRecent("https://a.com/1");
        a.addRecent("https://b.com/2");
        a.addRecent("https://a.com/1");
        eq("re-sent site moves to the front", "https://a.com/1", b.recents().get(0).url);
        eq("no duplicates", 2, b.recents().size());
        b.pageShown("https://a.com/1", "Title\tWith\nBreaks");
        eq("title saved without tabs or line breaks", "Title With Breaks", a.recents().get(0).title);
        eq("last page recorded", "https://a.com/1", a.last().url);
        a.addRecent("https://a.com/1");
        eq("re-sending keeps the title", "Title With Breaks", b.recents().get(0).title);
        a.pageShown("https://other.com/x", "");
        eq("last follows the screen even off the list", "https://other.com/x", b.last().url);
        a.addRecent("");
        eq("empty url ignored", 2, a.recents().size());
        for (int i = 0; i < 20; i++) a.addRecent("https://x.com/" + i);
        eq("list capped", Store.MAX_RECENTS, b.recents().size());
        eq("newest first", "https://x.com/19", b.recents().get(0).url);

        List<Thread> writers = new ArrayList<>();
        for (int t = 0; t < 4; t++) {
            final Store s = (t % 2 == 0) ? a : b;
            final int id = t;
            Thread th = new Thread(() -> {
                for (int i = 0; i < 25; i++) s.addRecent("https://t" + id + ".com/" + i);
            });
            writers.add(th);
            th.start();
        }
        for (Thread th : writers) th.join();
        List<Store.Recent> after = a.recents();
        eq("concurrent writers leave a full list", Store.MAX_RECENTS, after.size());
        boolean wellFormed = true;
        for (Store.Recent r : after) wellFormed &= r.url.matches("https://t[0-3]\\.com/\\d+");
        check("concurrent writers leave only real entries", wellFormed);
        eq("pin survives concurrent writes", pin, b.pin());

        check("no pins yet", a.pins().isEmpty());
        check("toggle pins", a.togglePin("https://k.com/show", "A show"));
        eq("pin seen by the other process", "A show", b.pins().get(0).title);
        b.setPinned("https://k.com/other", "Other", true);
        eq("newest pin first", "https://k.com/other", a.pins().get(0).url);
        b.setPinned("https://k.com/show", "Renamed", true);
        eq("re-pinning does not duplicate", 2, a.pins().size());
        check("toggle unpins", !a.togglePin("https://k.com/show", ""));
        eq("unpinned", 1, b.pins().size());
        for (int i = 0; i < 30; i++) a.setPinned("https://p.com/" + i, "", true);
        eq("pins capped", Store.MAX_PINS, b.pins().size());
        eq("recents untouched by pins", Store.MAX_RECENTS, b.recents().size());

        a.setDesktop("Example.COM", true);
        check("desktop host stored lower-case", b.desktopHosts().contains("example.com"));
        b.setDesktop("example.com", false);
        check("desktop host removed", a.desktopHosts().isEmpty());
        for (int i = 0; i < 60; i++) a.setDesktop("h" + i + ".com", true);
        eq("desktop hosts capped", Store.MAX_DESKTOP, b.desktopHosts().size());
        check("oldest desktop host dropped first", !b.desktopHosts().contains("h0.com") && b.desktopHosts().contains("h59.com"));
        eq("pin survives the new records", pin, b.pin());

        Files.write(f.toPath(), "garbage\n\u0000\u0001\nrecent\npin\tabc\n".getBytes(StandardCharsets.UTF_8));
        Store damaged = new Store(f);
        check("damaged file reads as empty", damaged.recents().isEmpty() && damaged.last() == null);
        check("damaged file gets a fresh pin", damaged.pin().matches("\\d{4}"));

        for (File x : dir.listFiles()) Files.delete(x.toPath());
        Files.delete(dir.toPath());
    }

    private static final class FakeHandler implements Server.Handler {
        volatile String opened;
        volatile String pressed;

        @Override public String open(String url) {
            if (url.contains("boom")) throw new IllegalStateException("storage failed");
            String u = Urls.normalize(url);
            if (u == null) return "not a link";
            opened = u;
            return null;
        }

        @Override public String key(String key) {
            pressed = key;
            return null;
        }

        @Override public String text(String text, boolean submit) {
            return text.isEmpty() ? "empty" : null;
        }

        volatile String pinned;
        volatile Boolean desktopOn;

        @Override public String pin(String url, String title, boolean on) {
            if (Urls.normalize(url) == null) return "not a link";
            pinned = (on ? "+" : "-") + url + "|" + title;
            return null;
        }

        @Override public String desktop(boolean on) {
            desktopOn = on;
            return null;
        }

        @Override public String stateJson() {
            return "{\"ok\":true,\"tv\":\"open\"}";
        }

        @Override public byte[] asset(String name) throws IOException {
            if ("phone.html".equals(name)) return "<html>phone</html>".getBytes(StandardCharsets.UTF_8);
            throw new IOException("no " + name);
        }
    }

    private static int call(int port, String method, String path, String body, StringBuilder reply) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + port + path).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(3000);
        c.setReadTimeout(3000);
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            try (OutputStream o = c.getOutputStream()) {
                o.write(body.getBytes(StandardCharsets.UTF_8));
            }
        }
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        if (in != null && reply != null) {
            try (InputStream s = in) {
                byte[] b = new byte[4096];
                int n;
                while ((n = s.read(b)) > 0) reply.append(new String(b, 0, n, StandardCharsets.UTF_8));
            }
        }
        c.disconnect();
        return code;
    }

    private static void server() throws IOException {
        FakeHandler h = new FakeHandler();
        List<String> warnings = new ArrayList<>();
        Server s = new Server(0, new Guard("2468", System::currentTimeMillis), h, warnings::add);
        s.start();
        int port = s.localPort();
        try {
            StringBuilder page = new StringBuilder();
            eq("GET / serves phone page", 200, call(port, "GET", "/", null, page));
            check("phone page body", page.toString().contains("phone"));
            eq("missing asset is 404", 404, call(port, "GET", "/icon.png", null, null));
            eq("unknown GET is 404", 404, call(port, "GET", "/nope", null, null));
            eq("PUT not allowed", 405, call(port, "PUT", "/api/open", "pin=2468", null));
            eq("wrong pin", 401, call(port, "POST", "/api/open", "pin=1111&url=https%3A%2F%2Fa.com", null));
            eq("right pin opens", 200, call(port, "POST", "/api/open", "pin=2468&url=https%3A%2F%2Fa.com%2Fx", null));
            eq("handler got the link", "https://a.com/x", h.opened);
            StringBuilder err = new StringBuilder();
            eq("bad link is 400", 400, call(port, "POST", "/api/open", "pin=2468&url=javascript%3Ax", err));
            check("bad link message", err.toString().contains("not a link"));
            eq("key", 200, call(port, "POST", "/api/key", "pin=2468&k=down", null));
            eq("handler got the key", "down", h.pressed);
            eq("empty text is 400", 400, call(port, "POST", "/api/text", "pin=2468&t=", null));
            eq("pin", 200, call(port, "POST", "/api/pin", "pin=2468&url=https%3A%2F%2Fa.com%2Fp&title=A+show&on=1", null));
            eq("handler got the pin", "+https://a.com/p|A show", h.pinned);
            eq("unpin", 200, call(port, "POST", "/api/pin", "pin=2468&url=https%3A%2F%2Fa.com%2Fp&on=0", null));
            eq("handler got the unpin", "-https://a.com/p|", h.pinned);
            eq("pin needs a link", 400, call(port, "POST", "/api/pin", "pin=2468&url=nope&on=1", null));
            eq("desktop", 200, call(port, "POST", "/api/desktop", "pin=2468&on=1", null));
            eq("handler got desktop", Boolean.TRUE, h.desktopOn);
            eq("pin needs the PIN", 401, call(port, "POST", "/api/pin", "pin=1111&url=https%3A%2F%2Fa.com&on=1", null));
            StringBuilder state = new StringBuilder();
            eq("state", 200, call(port, "POST", "/api/state", "pin=2468", state));
            check("state body", state.toString().contains("\"tv\":\"open\""));
            eq("unknown api", 404, call(port, "POST", "/api/nope", "pin=2468", null));
            StringBuilder failed = new StringBuilder();
            eq("handler failure answers 500", 500, call(port, "POST", "/api/open", "pin=2468&url=https%3A%2F%2Fboom.com", failed));
            check("handler failure message", failed.toString().contains("try again"));
            for (int i = 0; i < Guard.MAX_FAILS; i++) call(port, "POST", "/api/state", "pin=0000", null);
            eq("lockout reaches the phone as 429", 429, call(port, "POST", "/api/state", "pin=2468", null));
        } finally {
            s.stop();
        }
        // Expected warnings: the deliberate missing-icon request and the deliberate handler failure.
        warnings.removeIf(w -> w.startsWith("asset icon.png missing") || w.contains("storage failed"));
        check("server logged no unexpected errors: " + warnings, warnings.isEmpty());
    }

    private static void adBlock() throws IOException {
        long[] h = { AdBlock.hash("doubleclick.net"), AdBlock.hash("popads.net"), AdBlock.hash("ads.example.org") };
        java.util.Arrays.sort(h);
        java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
        try (java.io.DataOutputStream out = new java.io.DataOutputStream(bytes)) {
            out.writeInt(h.length);
            for (long x : h) out.writeLong(x);
        }
        AdBlock ab = AdBlock.read(new ByteArrayInputStream(bytes.toByteArray()));
        eq("ad list size", 3, ab.size());
        check("listed domain blocked", ab.blocks("doubleclick.net"));
        check("subdomain blocked", ab.blocks("static.ad.doubleclick.net"));
        check("case and trailing dot ignored", ab.blocks("POPADS.NET."));
        check("listed subdomain blocked", ab.blocks("x.ads.example.org"));
        check("its parent is not", !ab.blocks("example.org"));
        check("look-alike not blocked", !ab.blocks("notdoubleclick.net"));
        check("ordinary site not blocked", !ab.blocks("www.wikipedia.org"));
        check("null and empty not blocked", !ab.blocks(null) && !ab.blocks(""));
        check("empty list blocks nothing", !AdBlock.EMPTY.blocks("doubleclick.net"));
        boolean unsortedRejected = false;
        try {
            AdBlock.read(new ByteArrayInputStream(new byte[] {0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0, 1}));
        } catch (IOException e) {
            unsortedRejected = true;
        }
        check("unsorted list rejected", unsortedRejected);
        boolean truncatedRejected = false;
        try {
            AdBlock.read(new ByteArrayInputStream(new byte[] {0, 0, 0, 5, 1, 2}));
        } catch (IOException e) {
            truncatedRejected = true;
        }
        check("truncated list rejected", truncatedRejected);
    }

    private static void qr() {
        String svg = Text.qrSvg("http://192.168.100.156:8765/#pin=8216");
        check("qr is an svg", svg.startsWith("<svg") && svg.endsWith("</svg>"));
        // 36 bytes at medium error correction is QR version 3: 29 modules plus a 4-module border each side.
        check("qr size is version 3 with border: " + svg.substring(0, 80), svg.contains("viewBox=\"0 0 37 37\""));
        check("qr has dark modules", svg.contains("h1v1h-1z"));
        check("qr has no markup from the text", !svg.contains("8216"));
    }

    private static void text() {
        eq("html escaping", "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;",
                Text.html("<a href=\"x\">Tom & Jerry's</a>"));
        eq("html null", "", Text.html(null));
    }
}
