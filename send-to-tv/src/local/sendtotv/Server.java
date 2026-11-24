package local.sendtotv;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

/**
 * The little web server the phone talks to. Serves the phone page and a
 * PIN-protected JSON API. Answers only devices on the home network.
 * Pure Java (no Android) so SelfTest can run it on a PC with a fake Handler.
 */
final class Server {
    /** What the TV app does with each request. A null return means success; otherwise a message for the phone. */
    interface Handler {
        String open(String url);

        String key(String key);

        String text(String text, boolean submit);

        /** Pins (on) or unpins a site on the TV's start page. */
        String pin(String url, String title, boolean on);

        /** Shows the site that is on the TV as a desktop computer would (on), or normally. */
        String desktop(boolean on);

        String stateJson();

        byte[] asset(String name) throws IOException;
    }

    interface Logger {
        void warn(String message);
    }

    private static final int SOCKET_TIMEOUT_MS = 10_000;
    private static final int WORKERS = 4;

    private final int port;
    private final Guard guard;
    private final Handler handler;
    private final Logger log;
    private volatile ServerSocket socket;
    private ExecutorService pool;

    Server(int port, Guard guard, Handler handler, Logger log) {
        this.port = port;
        this.guard = guard;
        this.handler = handler;
        this.log = log;
    }

    /** Binds the port and starts answering. Throws if the port is taken. */
    synchronized void start() throws IOException {
        if (socket != null) return;
        ServerSocket s = new ServerSocket();
        s.setReuseAddress(true);
        try {
            s.bind(new InetSocketAddress(port));
        } catch (IOException e) {
            s.close();
            throw e;
        }
        socket = s;
        pool = Executors.newFixedThreadPool(WORKERS);
        Thread t = new Thread(() -> acceptLoop(s), "stv-accept");
        t.setDaemon(true);
        t.start();
    }

    /** The bound port (useful when started with port 0 in tests). */
    int localPort() {
        ServerSocket s = socket;
        return s == null ? -1 : s.getLocalPort();
    }

    synchronized void stop() {
        ServerSocket s = socket;
        socket = null;
        if (s != null) {
            try {
                s.close();
            } catch (IOException e) {
                log.warn("closing server socket: " + e.getMessage());
            }
        }
        if (pool != null) pool.shutdownNow();
    }

    private void acceptLoop(ServerSocket s) {
        while (!s.isClosed()) {
            final Socket client;
            try {
                client = s.accept();
            } catch (IOException e) {
                if (!s.isClosed()) log.warn("accept failed: " + e.getMessage());
                continue;
            }
            try {
                pool.execute(() -> serve(client));
            } catch (RejectedExecutionException e) {
                closeQuietly(client);
            }
        }
    }

    private void serve(Socket client) {
        try (Socket c = client) {
            c.setSoTimeout(SOCKET_TIMEOUT_MS);
            InputStream in = new BufferedInputStream(c.getInputStream());
            OutputStream out = new BufferedOutputStream(c.getOutputStream());
            InetAddress peer = c.getInetAddress();
            if (!isHomeNetwork(peer)) {
                Http.respondJson(out, 403, Http.error("only devices on your home network can use this"));
                return;
            }
            Http.Request r;
            try {
                r = Http.read(in);
            } catch (Http.BadRequest e) {
                Http.respondJson(out, e.status, Http.error(e.getMessage()));
                return;
            }
            if (r == null) return;
            try {
                route(r, peer.getHostAddress(), out);
            } catch (RuntimeException e) {
                // A handler failed (for example, saving to storage). Nothing was sent yet: report it.
                log.warn("request failed: " + e);
                Http.respondJson(out, 500, Http.error("The TV hit a problem with that. Please try again."));
            }
        } catch (IOException e) {
            log.warn("connection error: " + e.getMessage());
        }
    }

    private void route(Http.Request r, String peer, OutputStream out) throws IOException {
        if ("GET".equals(r.method)) {
            switch (r.path) {
                case "/":
                case "/index.html":
                    sendAsset(out, "phone.html", "text/html; charset=utf-8");
                    return;
                case "/icon.png":
                    sendAsset(out, "icon.png", "image/png");
                    return;
                case "/manifest.json":
                    sendAsset(out, "manifest.json", "application/manifest+json");
                    return;
                default:
                    Http.respondJson(out, 404, Http.error("not found"));
                    return;
            }
        }
        if (!"POST".equals(r.method)) {
            Http.respondJson(out, 405, Http.error("method not allowed"));
            return;
        }
        if (!r.path.startsWith("/api/")) {
            Http.respondJson(out, 404, Http.error("not found"));
            return;
        }

        Map<String, String> form;
        try {
            form = r.form();
        } catch (Http.BadRequest e) {
            Http.respondJson(out, e.status, Http.error(e.getMessage()));
            return;
        }
        switch (guard.check(peer, form.get("pin"))) {
            case LOCKED:
                Http.respondJson(out, 429, Http.error("Too many wrong PINs. Wait a minute and try again."));
                return;
            case WRONG:
                Http.respondJson(out, 401, Http.error("Wrong PIN. It is shown on the TV's Send to TV screen."));
                return;
            case OK:
            default:
                break;
        }

        String problem;
        switch (r.path) {
            case "/api/state":
                Http.respondJson(out, 200, handler.stateJson());
                return;
            case "/api/open":
                problem = handler.open(value(form, "url"));
                break;
            case "/api/key":
                problem = handler.key(value(form, "k"));
                break;
            case "/api/text":
                problem = handler.text(value(form, "t"), "1".equals(form.get("submit")));
                break;
            case "/api/pin":
                problem = handler.pin(value(form, "url"), value(form, "title"), "1".equals(form.get("on")));
                break;
            case "/api/desktop":
                problem = handler.desktop("1".equals(form.get("on")));
                break;
            default:
                Http.respondJson(out, 404, Http.error("not found"));
                return;
        }
        if (problem == null) Http.respondJson(out, 200, Http.ok());
        else Http.respondJson(out, 400, Http.error(problem));
    }

    private void sendAsset(OutputStream out, String name, String type) throws IOException {
        byte[] body;
        try {
            body = handler.asset(name);
        } catch (IOException e) {
            log.warn("asset " + name + " missing: " + e.getMessage());
            Http.respondJson(out, 404, Http.error("not found"));
            return;
        }
        Http.respond(out, 200, type, body);
    }

    private static String value(Map<String, String> form, String key) {
        String v = form.get(key);
        return v == null ? "" : v;
    }

    /** Loopback, private IPv4 ranges, link-local, and IPv6 unique-local (fc00::/7). */
    static boolean isHomeNetwork(InetAddress a) {
        if (a.isLoopbackAddress() || a.isSiteLocalAddress() || a.isLinkLocalAddress()) return true;
        if (a instanceof Inet6Address) {
            byte[] b = a.getAddress();
            return (b[0] & 0xfe) == 0xfc;
        }
        return false;
    }

    private static void closeQuietly(Socket s) {
        try {
            s.close();
        } catch (IOException ignored) {
            // the connection is being dropped on purpose; nothing else to do
        }
    }
}
