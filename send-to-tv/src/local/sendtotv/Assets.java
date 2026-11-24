package local.sendtotv;

import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Reads files bundled in the APK's assets folder. */
final class Assets {
    private Assets() {}

    static byte[] bytes(Context context, String name) throws IOException {
        try (InputStream in = context.getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }

    static String text(Context context, String name) throws IOException {
        return new String(bytes(context, name), StandardCharsets.UTF_8);
    }
}
