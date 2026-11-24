package local.sendtotv;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Map;

/**
 * Checks the 4-digit PIN. After 5 wrong tries from one address within 5 minutes,
 * that address is locked out for a minute, so the 9,000 possible PINs cannot be
 * guessed quickly. Pure Java so SelfTest can run it with a fake clock.
 */
final class Guard {
    interface Clock {
        long now();
    }

    enum Result { OK, WRONG, LOCKED }

    static final int MAX_FAILS = 5;
    static final long WINDOW_MS = 5 * 60_000L;
    static final long LOCK_MS = 60_000L;

    private static final class Attempts {
        int fails;
        long windowStart;
        long lockedUntil;
    }

    private final byte[] pin;
    private final Clock clock;
    private final Map<String, Attempts> byAddress = new HashMap<>();

    Guard(String pin, Clock clock) {
        if (pin == null || pin.isEmpty()) throw new IllegalArgumentException("pin must not be empty");
        this.pin = pin.getBytes(StandardCharsets.UTF_8);
        this.clock = clock;
    }

    synchronized Result check(String address, String given) {
        long now = clock.now();
        Attempts a = byAddress.get(address);
        if (a != null && now < a.lockedUntil) return Result.LOCKED;

        boolean match = given != null && MessageDigest.isEqual(given.getBytes(StandardCharsets.UTF_8), pin);
        if (match) {
            byAddress.remove(address);
            return Result.OK;
        }
        if (a == null || now - a.windowStart > WINDOW_MS) {
            a = new Attempts();
            a.windowStart = now;
            byAddress.put(address, a);
        }
        a.fails++;
        if (a.fails >= MAX_FAILS) {
            a.lockedUntil = now + LOCK_MS;
            a.fails = 0;
            a.windowStart = now;
        }
        return Result.WRONG;
    }
}
