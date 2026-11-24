package local.sendtotv;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Starts the listener when the TV boots and right after the app is updated (an update stops the
 * running service), so the phone can send a link without anyone opening the app first.
 */
public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            context.startForegroundService(new Intent(context, StvService.class));
        }
    }
}
