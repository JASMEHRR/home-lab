package local.sendtotv;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Log;

import java.io.IOException;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * The phone server, kept alive as a foreground service in its own small process (":server").
 * The heavy TV screen lives in the main process, which Android can close when it is not in use;
 * phone buttons reach it through Remote broadcasts.
 */
public final class StvService extends Service implements Server.Handler {
    static final int PORT = 8765;
    static final int MAX_TEXT = 500;

    private static final String TAG = "SendToTV";
    private static final String CHANNEL = "send_to_tv";
    private static final int NOTIFICATION_ID = 1;
    private static final long ANSWER_TIMEOUT_MS = 2000;
    private static final Set<String> KEYS = new HashSet<>(Arrays.asList(
            "up", "down", "left", "right", "ok", "back", "play", "rew", "ff",
            "scrollup", "scrolldown", "home", "reload", "clean"));

    private Server server;
    private Store store;
    private HandlerThread answers;
    private Handler answerHandler;

    @Override
    public void onCreate() {
        super.onCreate();
        store = Stores.open(this);
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Send to TV", NotificationManager.IMPORTANCE_MIN));
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle("Send to TV is ready")
                .setContentText("On your phone open " + phoneAddress())
                .setSmallIcon(R.drawable.icon)
                .build();
        startForeground(NOTIFICATION_ID, n);

        // Broadcast answers arrive on their own thread: the server threads wait for them.
        answers = new HandlerThread("stv-answers");
        answers.start();
        answerHandler = new Handler(answers.getLooper());

        server = new Server(PORT, new Guard(store.pin(), System::currentTimeMillis), this,
                message -> Log.w(TAG, message));
        try {
            server.start();
        } catch (IOException e) {
            Log.e(TAG, "could not listen on port " + PORT, e);
            server = null;
            stopSelf();
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (server != null) server.stop();
        if (answers != null) answers.quitSafely();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    static String phoneAddress() {
        String ip = Text.lanAddress();
        return "http://" + (ip == null ? "(not on Wi-Fi)" : ip) + ":" + PORT;
    }

    /**
     * Sends a Remote broadcast to the TV screen and waits for its answer.
     * Returns false when the screen is not open (nobody answered) or did not answer in time.
     */
    private boolean tellScreen(Intent intent) {
        intent.setPackage(getPackageName());
        CountDownLatch done = new CountDownLatch(1);
        boolean[] answered = {false};
        sendOrderedBroadcast(intent, Remote.PERMISSION, new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent result) {
                answered[0] = getResultCode() == Activity.RESULT_OK;
                done.countDown();
            }
        }, answerHandler, Activity.RESULT_CANCELED, null, null);
        try {
            if (!done.await(ANSWER_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                Log.w(TAG, "the TV screen did not answer " + intent.getAction() + " in time");
                return false;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        return answered[0];
    }

    private void openScreen() {
        startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }

    // ---- Server.Handler (called on server worker threads) ----

    @Override
    public String open(String raw) {
        String url = Urls.normalize(raw);
        if (url == null) return "That doesn't look like a web link. It must start with http:// or https://";
        store.addRecent(url);
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url), this, MainActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(i);
        return null;
    }

    @Override
    public String key(String key) {
        if (!KEYS.contains(key)) return "Unknown button";
        if (tellScreen(new Intent(Remote.ACTION_KEY).putExtra(Remote.EXTRA_KEY, key))) return null;
        if ("home".equals(key)) {
            openScreen();
            return null;
        }
        return "Send to TV isn't on the screen. Send a link, or press ⌂ to open it.";
    }

    @Override
    public String text(String text, boolean submit) {
        if (text.length() > MAX_TEXT) return "That text is too long";
        Intent i = new Intent(Remote.ACTION_TEXT).putExtra(Remote.EXTRA_TEXT, text).putExtra(Remote.EXTRA_SUBMIT, submit);
        return tellScreen(i) ? null : "Send to TV isn't on the screen.";
    }

    @Override
    public String pin(String url, String title, boolean on) {
        String u = Urls.normalize(url);
        if (u == null) return "That doesn't look like a web link.";
        store.setPinned(u, title.length() > 300 ? title.substring(0, 300) : title, on);
        tellScreen(new Intent(Remote.ACTION_KEY).putExtra(Remote.EXTRA_KEY, "sites-changed"));
        return null;
    }

    @Override
    public String desktop(boolean on) {
        Store.Recent last = store.last();
        String host = last == null ? null : Uri.parse(last.url).getHost();
        if (host == null) return "Open a site on the TV first.";
        store.setDesktop(host, on);
        tellScreen(new Intent(Remote.ACTION_KEY).putExtra(Remote.EXTRA_KEY, "reload"));
        return null;
    }

    @Override
    public String stateJson() {
        boolean open = tellScreen(new Intent(Remote.ACTION_PING));
        Store.Recent last = store.last();
        String host = last == null ? null : Uri.parse(last.url).getHost();
        StringBuilder b = new StringBuilder("{\"ok\":true,\"tv\":");
        b.append(open ? "\"open\"" : "\"closed\"");
        b.append(",\"current\":{\"url\":").append(Http.jsonString(last == null ? "" : last.url))
                .append(",\"title\":").append(Http.jsonString(last == null ? "" : last.title))
                .append(",\"desktop\":").append(host != null && store.desktopHosts().contains(host.toLowerCase(Locale.ROOT)))
                .append('}');
        b.append(",\"recents\":");
        sites(b, store.recents());
        b.append(",\"pins\":");
        sites(b, store.pins());
        return b.append('}').toString();
    }

    private static void sites(StringBuilder b, List<Store.Recent> list) {
        b.append('[');
        for (int i = 0; i < list.size(); i++) {
            Store.Recent r = list.get(i);
            if (i > 0) b.append(',');
            b.append("{\"url\":").append(Http.jsonString(r.url))
                    .append(",\"title\":").append(Http.jsonString(r.title)).append('}');
        }
        b.append(']');
    }

    @Override
    public byte[] asset(String name) throws IOException {
        return Assets.bytes(this, name);
    }
}
