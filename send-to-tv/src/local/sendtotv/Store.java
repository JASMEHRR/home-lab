package local.sendtotv;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The PIN, the recently sent sites, pinned sites, sites shown in desktop mode and the last page
 * shown, kept in one small text file that
 * both of the app's processes use: the TV screen, and the background listener the phone talks
 * to. Every call re-reads the file under an exclusive lock, so neither process works from a
 * stale copy or overwrites the other's change. Pure Java so SelfTest can run it on a PC.
 *
 * Storage failures surface as UncheckedIOException; callers decide whether they matter.
 */
final class Store {
    static final int MAX_RECENTS = 12;
    static final int MAX_PINS = 24;
    static final int MAX_DESKTOP = 50;

    private static final int MAX_FILE_BYTES = 64 * 1024;
    private static final SecureRandom RANDOM = new SecureRandom();
    /** Java file locks belong to the whole process, so threads of one process also queue here. */
    private static final Object THREADS = new Object();

    /** A site: its url and, once a page has loaded, its title (empty until then). */
    static final class Recent {
        final String url;
        final String title;

        Recent(String url, String title) {
            this.url = url;
            this.title = title;
        }
    }

    private static final class Data {
        String pin;
        Recent last;
        final List<Recent> recents = new ArrayList<>();
        final List<Recent> pins = new ArrayList<>();
        final Set<String> desktop = new LinkedHashSet<>();
    }

    private interface Change {
        /** Edits the data; returns true when it changed and must be written back. */
        boolean apply(Data d);
    }

    private final File file;

    Store(File file) {
        this.file = file;
    }

    /** The 4-digit PIN, created on first use. */
    String pin() {
        return transact(d -> {
            if (d.pin != null) return false;
            d.pin = String.valueOf(1000 + RANDOM.nextInt(9000));
            return true;
        }).pin;
    }

    /** Keeps an existing PIN; otherwise adopts this one (used to carry over an older version's PIN). */
    void adoptPin(String pin) {
        if (!validPin(pin)) return;
        transact(d -> {
            if (d.pin != null) return false;
            d.pin = pin;
            return true;
        });
    }

    List<Recent> recents() {
        return Collections.unmodifiableList(transact(null).recents);
    }

    List<Recent> pins() {
        return Collections.unmodifiableList(transact(null).pins);
    }

    /** Pins url (newest first) or unpins it. Pinning an already pinned url just refreshes its title. */
    void setPinned(String url, String title, boolean pinned) {
        String u = clean(url);
        String t = clean(title).trim();
        if (u.isEmpty()) return;
        transact(d -> {
            boolean removed = d.pins.removeIf(p -> p.url.equals(u));
            if (!pinned) return removed;
            if (d.pins.size() >= MAX_PINS) d.pins.remove(d.pins.size() - 1);
            d.pins.add(0, new Recent(u, t));
            return true;
        });
    }

    /** Pins url if it is not pinned, else unpins it. Returns true when it is pinned afterwards. */
    boolean togglePin(String url, String title) {
        String u = clean(url);
        if (u.isEmpty()) return false;
        boolean pinned = false;
        for (Recent p : pins()) pinned |= p.url.equals(u);
        // ponytail: read-then-write is two locks; a phone and the remote toggling the same pin in the same instant is harmless.
        setPinned(u, title, !pinned);
        return !pinned;
    }

    /** Hosts the TV shows as a desktop computer would (for sites that refuse TVs or phones). */
    Set<String> desktopHosts() {
        return Collections.unmodifiableSet(transact(null).desktop);
    }

    void setDesktop(String host, boolean on) {
        String h = clean(host).trim().toLowerCase(Locale.ROOT);
        if (h.isEmpty()) return;
        transact(d -> {
            if (!on) return d.desktop.remove(h);
            if (!d.desktop.add(h)) return false;
            while (d.desktop.size() > MAX_DESKTOP) d.desktop.remove(d.desktop.iterator().next());
            return true;
        });
    }

    /** The page the TV screen showed most recently, or null. Powers the start page's Continue tile. */
    Recent last() {
        return transact(null).last;
    }

    /** Moves url to the front of the recent list, keeping a title it already had. */
    void addRecent(String url) {
        String u = clean(url);
        if (u.isEmpty()) return;
        transact(d -> {
            String title = "";
            for (int i = 0; i < d.recents.size(); i++) {
                if (d.recents.get(i).url.equals(u)) {
                    title = d.recents.remove(i).title;
                    break;
                }
            }
            d.recents.add(0, new Recent(u, title));
            while (d.recents.size() > MAX_RECENTS) d.recents.remove(d.recents.size() - 1);
            return true;
        });
    }

    /** Records a page that finished loading on the TV: it becomes "last", and its recent entry gets the title. */
    void pageShown(String url, String title) {
        String u = clean(url);
        String t = clean(title).trim();
        if (u.isEmpty()) return;
        transact(d -> {
            boolean changed = false;
            if (d.last == null || !d.last.url.equals(u) || !d.last.title.equals(t)) {
                d.last = new Recent(u, t);
                changed = true;
            }
            if (!t.isEmpty()) {
                for (int i = 0; i < d.recents.size(); i++) {
                    Recent r = d.recents.get(i);
                    if (r.url.equals(u) && !r.title.equals(t)) {
                        d.recents.set(i, new Recent(u, t));
                        changed = true;
                    }
                }
            }
            return changed;
        });
    }

    // ---- file handling ----

    private Data transact(Change change) {
        synchronized (THREADS) {
            File dir = file.getAbsoluteFile().getParentFile();
            if (dir != null && !dir.isDirectory() && !dir.mkdirs()) {
                throw new UncheckedIOException(new IOException("cannot create " + dir));
            }
            try (RandomAccessFile raf = new RandomAccessFile(file, "rw");
                 FileChannel channel = raf.getChannel();
                 FileLock lock = channel.lock()) {
                if (!lock.isValid()) throw new IOException("lost the lock on " + file);
                Data d = parse(read(channel));
                if (change != null && change.apply(d)) write(channel, format(d));
                return d;
            } catch (IOException e) {
                throw new UncheckedIOException("could not use " + file, e);
            }
        }
    }

    private static String read(FileChannel channel) throws IOException {
        long size = channel.size();
        if (size == 0) return "";
        // A file this big was not written by us; start over rather than load it.
        if (size > MAX_FILE_BYTES) return "";
        ByteBuffer buf = ByteBuffer.allocate((int) size);
        channel.position(0);
        while (buf.hasRemaining() && channel.read(buf) >= 0) {
            // keep reading until full or end of file
        }
        return new String(buf.array(), 0, buf.position(), StandardCharsets.UTF_8);
    }

    private static void write(FileChannel channel, String text) throws IOException {
        ByteBuffer buf = ByteBuffer.wrap(text.getBytes(StandardCharsets.UTF_8));
        channel.truncate(0);
        channel.position(0);
        while (buf.hasRemaining()) channel.write(buf);
        channel.force(false);
    }

    /** One record per line, fields separated by tabs. Unknown or damaged lines are skipped. */
    private static Data parse(String text) {
        Data d = new Data();
        for (String line : text.split("\n")) {
            String[] f = line.split("\t", -1);
            if (f.length < 2) continue;
            switch (f[0]) {
                case "pin":
                    if (validPin(f[1])) d.pin = f[1];
                    break;
                case "last":
                    if (!f[1].isEmpty()) d.last = new Recent(f[1], f.length > 2 ? f[2] : "");
                    break;
                case "recent":
                    if (!f[1].isEmpty() && d.recents.size() < MAX_RECENTS) {
                        d.recents.add(new Recent(f[1], f.length > 2 ? f[2] : ""));
                    }
                    break;
                case "pinned":
                    if (!f[1].isEmpty() && d.pins.size() < MAX_PINS) {
                        d.pins.add(new Recent(f[1], f.length > 2 ? f[2] : ""));
                    }
                    break;
                case "desktop":
                    if (!f[1].isEmpty() && d.desktop.size() < MAX_DESKTOP) d.desktop.add(f[1]);
                    break;
                default:
                    break;
            }
        }
        return d;
    }

    private static String format(Data d) {
        StringBuilder b = new StringBuilder();
        if (d.pin != null) b.append("pin\t").append(d.pin).append('\n');
        if (d.last != null) b.append("last\t").append(clean(d.last.url)).append('\t').append(clean(d.last.title)).append('\n');
        for (Recent r : d.recents) {
            b.append("recent\t").append(clean(r.url)).append('\t').append(clean(r.title)).append('\n');
        }
        for (Recent p : d.pins) {
            b.append("pinned\t").append(clean(p.url)).append('\t').append(clean(p.title)).append('\n');
        }
        for (String h : d.desktop) b.append("desktop\t").append(clean(h)).append('\n');
        return b.toString();
    }

    private static boolean validPin(String pin) {
        return pin != null && pin.matches("\\d{4,8}");
    }

    private static String clean(String s) {
        return s == null ? "" : s.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }
}
