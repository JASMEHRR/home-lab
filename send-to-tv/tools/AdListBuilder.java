package local.sendtotv;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * Turns HaGeZi "wildcard onlydomains" lists into the compact file AdBlock reads:
 * an int count, then the sorted, de-duplicated FNV-1a fingerprints of every domain.
 * Usage (from build.ps1): AdListBuilder <list.txt>... <adhosts.bin>
 */
public final class AdListBuilder {
    private static final int MIN_DOMAINS = 10_000; // a smaller list means the download went wrong

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("usage: AdListBuilder <list.txt>... <adhosts.bin>");
            System.exit(2);
        }
        List<String> lines = new ArrayList<>();
        for (int i = 0; i < args.length - 1; i++) lines.addAll(Files.readAllLines(Paths.get(args[i]), StandardCharsets.UTF_8));
        long[] hashes = new long[lines.size()];
        int n = 0;
        for (String raw : lines) {
            String d = raw.trim().toLowerCase(Locale.ROOT);
            if (d.isEmpty() || d.startsWith("#")) continue;
            if (d.startsWith("*.")) d = d.substring(2);
            if (!d.matches("[a-z0-9_-]+(\\.[a-z0-9_-]+)+")) continue;
            hashes[n++] = AdBlock.hash(d);
        }
        long[] sorted = Arrays.copyOf(hashes, n);
        Arrays.sort(sorted);
        int unique = 0;
        for (int i = 0; i < sorted.length; i++) {
            if (i == 0 || sorted[i] != sorted[i - 1]) sorted[unique++] = sorted[i];
        }
        if (unique < MIN_DOMAINS) throw new IOException("only " + unique + " domains; are the lists complete?");
        try (OutputStream file = Files.newOutputStream(Paths.get(args[args.length - 1]));
             DataOutputStream out = new DataOutputStream(new BufferedOutputStream(file))) {
            out.writeInt(unique);
            for (int i = 0; i < unique; i++) out.writeLong(sorted[i]);
        }
        System.out.println("ad list: " + unique + " domains -> " + args[args.length - 1]);
    }
}
