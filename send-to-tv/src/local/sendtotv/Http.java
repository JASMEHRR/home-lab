package local.sendtotv;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Just enough HTTP/1.1 for the phone page: one request per connection,
 * hard size limits, no chunked bodies. Pure Java so SelfTest can run it on a PC.
 */
final class Http {
    static final int MAX_HEADER_BYTES = 8 * 1024;
    static final int MAX_BODY_BYTES = 16 * 1024;

    private Http() {}

    /** A request the server refuses; status is the HTTP code to answer with. */
    static final class BadRequest extends IOException {
        private static final long serialVersionUID = 1L;
        final int status;

        BadRequest(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    static final class Request {
        final String method;
        final String path;
        final Map<String, String> query;
        final Map<String, String> headers;
        final byte[] body;

        Request(String method, String path, Map<String, String> query, Map<String, String> headers, byte[] body) {
            this.method = method;
            this.path = path;
            this.query = query;
            this.headers = headers;
            this.body = body;
        }

        /** The body as application/x-www-form-urlencoded fields. */
        Map<String, String> form() throws BadRequest {
            return parseForm(new String(body, StandardCharsets.UTF_8));
        }
    }

    /** Reads one request. Returns null when the client closed the connection before sending anything. */
    static Request read(InputStream in) throws IOException {
        ByteArrayOutputStream head = new ByteArrayOutputStream();
        int state = 0; // progress through \r\n\r\n
        while (state != 4) {
            int b = in.read();
            if (b < 0) {
                if (head.size() == 0) return null;
                throw new BadRequest(400, "request ended early");
            }
            head.write(b);
            if (head.size() > MAX_HEADER_BYTES) throw new BadRequest(431, "headers too large");
            if (b == '\r') state = (state == 2) ? 3 : 1;
            else if (b == '\n') state = (state == 1) ? 2 : (state == 3) ? 4 : 0;
            else state = 0;
        }

        String[] lines = new String(head.toByteArray(), StandardCharsets.ISO_8859_1).split("\r\n");
        String[] requestLine = lines[0].split(" ");
        if (requestLine.length != 3 || !requestLine[2].startsWith("HTTP/1.")) {
            throw new BadRequest(400, "bad request line");
        }

        Map<String, String> headers = new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            String line = lines[i];
            if (line.isEmpty()) continue;
            int colon = line.indexOf(':');
            if (colon <= 0) throw new BadRequest(400, "bad header");
            headers.put(line.substring(0, colon).trim().toLowerCase(Locale.ROOT), line.substring(colon + 1).trim());
        }
        if (headers.containsKey("transfer-encoding")) throw new BadRequest(501, "chunked bodies are not supported");

        int length = 0;
        String declared = headers.get("content-length");
        if (declared != null) {
            try {
                length = Integer.parseInt(declared);
            } catch (NumberFormatException e) {
                throw new BadRequest(400, "bad content-length");
            }
            if (length < 0) throw new BadRequest(400, "bad content-length");
            if (length > MAX_BODY_BYTES) throw new BadRequest(413, "body too large");
        }
        byte[] body = new byte[length];
        int got = 0;
        while (got < length) {
            int n = in.read(body, got, length - got);
            if (n < 0) throw new BadRequest(400, "body ended early");
            got += n;
        }

        String target = requestLine[1];
        int q = target.indexOf('?');
        String path = q < 0 ? target : target.substring(0, q);
        Map<String, String> query = q < 0 ? Collections.<String, String>emptyMap() : parseForm(target.substring(q + 1));
        return new Request(requestLine[0].toUpperCase(Locale.ROOT), path, query, headers, body);
    }

    static Map<String, String> parseForm(String s) throws BadRequest {
        Map<String, String> out = new HashMap<>();
        if (s.isEmpty()) return out;
        for (String pair : s.split("&")) {
            if (pair.isEmpty()) continue;
            int eq = pair.indexOf('=');
            String k = eq < 0 ? pair : pair.substring(0, eq);
            String v = eq < 0 ? "" : pair.substring(eq + 1);
            try {
                out.put(URLDecoder.decode(k, "UTF-8"), URLDecoder.decode(v, "UTF-8"));
            } catch (IllegalArgumentException e) {
                throw new BadRequest(400, "bad form encoding");
            } catch (UnsupportedEncodingException e) {
                throw new IllegalStateException("UTF-8 is always supported", e);
            }
        }
        return out;
    }

    static void respond(OutputStream out, int status, String type, byte[] body) throws IOException {
        String head = "HTTP/1.1 " + status + " " + reason(status) + "\r\n"
                + "Content-Type: " + type + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Cache-Control: no-store\r\n"
                + "X-Content-Type-Options: nosniff\r\n"
                + "Connection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.write(body);
        out.flush();
    }

    static void respondJson(OutputStream out, int status, String json) throws IOException {
        respond(out, status, "application/json; charset=utf-8", json.getBytes(StandardCharsets.UTF_8));
    }

    static String ok() {
        return "{\"ok\":true}";
    }

    static String error(String message) {
        return "{\"ok\":false,\"error\":" + jsonString(message) + "}";
    }

    /** A JSON string literal, safe to embed in HTML too (escapes < > &). */
    static String jsonString(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder(s.length() + 2).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                case '<': b.append("\\u003c"); break;
                case '>': b.append("\\u003e"); break;
                case '&': b.append("\\u0026"); break;
                default:
                    if (c < 0x20 || c == 0x2028 || c == 0x2029) b.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.append('"').toString();
    }

    private static String reason(int status) {
        switch (status) {
            case 200: return "OK";
            case 400: return "Bad Request";
            case 401: return "Unauthorized";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            case 405: return "Method Not Allowed";
            case 413: return "Payload Too Large";
            case 429: return "Too Many Requests";
            case 431: return "Request Header Fields Too Large";
            case 500: return "Internal Server Error";
            case 501: return "Not Implemented";
            default: return "Error";
        }
    }
}
