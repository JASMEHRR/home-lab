package local.sendtotv;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;
import java.util.Locale;

/**
 * Ad, tracker and pop-up ad domains (HaGeZi Light + HaGeZi Pop-Up Ads), kept as sorted 64-bit
 * fingerprints so ~145,000 domains fit in ~1.2 MB on a 1 GB TV and a lookup is a few binary searches.
 * ponytail: a fingerprint collision could block an unlisted domain; at 64 bits that is about 1 in 10^14.
 * A host is blocked when it, or any parent domain of it, is on the list.
 * Pure Java so SelfTest can run it; tools/AdListBuilder.java writes the file it reads.
 */
final class AdBlock {
    static final AdBlock EMPTY = new AdBlock(new long[0]);
    private static final int MAX_ENTRIES = 2_000_000;

    private final long[] sorted;

    AdBlock(long[] sortedHashes) {
        this.sorted = sortedHashes;
    }

    /** Reads the builder's format: an int count, then that many ascending longs. */
    static AdBlock read(InputStream in) throws IOException {
        DataInputStream d = new DataInputStream(new BufferedInputStream(in));
        int n = d.readInt();
        if (n < 0 || n > MAX_ENTRIES) throw new IOException("ad list has a bad size: " + n);
        long[] h = new long[n];
        for (int i = 0; i < n; i++) {
            h[i] = d.readLong();
            if (i > 0 && h[i - 1] >= h[i]) throw new IOException("ad list is not sorted at entry " + i);
        }
        return new AdBlock(h);
    }

    int size() {
        return sorted.length;
    }

    /** FNV-1a 64 over the (lower-case, ASCII) domain. The builder uses this same function. */
    static long hash(String domain) {
        long h = 0xcbf29ce484222325L;
        for (int i = 0; i < domain.length(); i++) {
            h ^= domain.charAt(i);
            h *= 0x100000001b3L;
        }
        return h;
    }

    /** True when host or one of its parent domains is listed. Bare top-level domains are never tested. */
    boolean blocks(String host) {
        if (host == null || host.isEmpty() || sorted.length == 0) return false;
        String h = host.toLowerCase(Locale.ROOT);
        if (h.endsWith(".")) h = h.substring(0, h.length() - 1);
        while (h.indexOf('.') > 0) {
            if (Arrays.binarySearch(sorted, hash(h)) >= 0) return true;
            h = h.substring(h.indexOf('.') + 1);
        }
        return false;
    }
}
