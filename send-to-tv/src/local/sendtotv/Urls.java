package local.sendtotv;

import java.util.Locale;

/**
 * Turns whatever the phone sends into a safe http(s) link, or rejects it.
 * Pure Java (no Android) so SelfTest can run it on a PC.
 */
final class Urls {
    static final int MAX_LENGTH = 2048;

    private Urls() {}

    /**
     * Returns a loadable http(s) URL, or null when the input is not one.
     * Accepts a bare link ("viki.com/tv/x"), or text that contains a link
     * ("Watch this https://www.viki.com/videos/1 enjoy"), which is what
     * apps put on the clipboard when you tap Share then Copy.
     */
    static String normalize(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        if (s.isEmpty() || s.length() > MAX_LENGTH) return null;

        int at = schemeIndex(s);
        if (at >= 0) {
            s = s.substring(at);
            int end = firstWhitespace(s);
            if (end >= 0) s = s.substring(0, end);
        } else if (s.contains("://") || firstWhitespace(s) >= 0 || s.indexOf('.') < 0) {
            return null; // another scheme (file://, intent://), a sentence, or a single word
        } else {
            s = "https://" + s;
        }

        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c <= ' ' || c == 0x7f) return null;
        }

        String lower = s.toLowerCase(Locale.ROOT);
        int afterScheme = lower.startsWith("https://") ? 8 : lower.startsWith("http://") ? 7 : -1;
        if (afterScheme < 0) return null;
        int authorityEnd = s.length();
        for (int i = afterScheme; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '/' || c == '?' || c == '#') { authorityEnd = i; break; }
        }
        String authority = s.substring(afterScheme, authorityEnd);
        return validAuthority(authority) ? s : null;
    }

    /** Index of "http://" or "https://" (any case), or -1. */
    private static int schemeIndex(String s) {
        String lower = s.toLowerCase(Locale.ROOT);
        int a = lower.indexOf("https://");
        int b = lower.indexOf("http://");
        if (a < 0) return b;
        if (b < 0) return a;
        return Math.min(a, b);
    }

    private static int firstWhitespace(String s) {
        for (int i = 0; i < s.length(); i++) {
            if (Character.isWhitespace(s.charAt(i))) return i;
        }
        return -1;
    }

    /** host[:port] or [ipv6][:port]. User info ("user@host") is refused: it is a common phishing disguise. */
    private static boolean validAuthority(String a) {
        if (a.isEmpty() || a.indexOf('@') >= 0) return false;
        String host;
        String port = null;
        if (a.charAt(0) == '[') {
            int close = a.indexOf(']');
            if (close < 2) return false;
            host = a.substring(1, close);
            for (int i = 0; i < host.length(); i++) {
                char c = host.charAt(i);
                if (!(Character.digit(c, 16) >= 0 || c == ':' || c == '.')) return false;
            }
            String rest = a.substring(close + 1);
            if (!rest.isEmpty()) {
                if (rest.charAt(0) != ':') return false;
                port = rest.substring(1);
            }
        } else {
            int colon = a.lastIndexOf(':');
            if (colon >= 0) {
                host = a.substring(0, colon);
                port = a.substring(colon + 1);
            } else {
                host = a;
            }
            if (host.isEmpty() || host.startsWith(".") || host.startsWith("-")) return false;
            for (int i = 0; i < host.length(); i++) {
                char c = host.charAt(i);
                boolean ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
                        || c == '.' || c == '-' || c == '_';
                if (!ok) return false;
            }
        }
        if (port != null) {
            if (port.isEmpty() || port.length() > 5) return false;
            for (int i = 0; i < port.length(); i++) {
                if (port.charAt(i) < '0' || port.charAt(i) > '9') return false;
            }
            int p = Integer.parseInt(port);
            if (p < 1 || p > 65535) return false;
        }
        return true;
    }
}
