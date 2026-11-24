package local.sendtotv;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.File;

/** Opens the app's Store, carrying over the PIN that version 1.0 kept in SharedPreferences. */
final class Stores {
    private static final String STATE_FILE = "state.txt";
    private static final String LEGACY_PREFS = "send_to_tv";
    private static final String LEGACY_PIN = "pin";

    private Stores() {}

    static Store open(Context context) {
        Context app = context.getApplicationContext();
        Store store = new Store(new File(app.getFilesDir(), STATE_FILE));
        SharedPreferences legacy = app.getSharedPreferences(LEGACY_PREFS, Context.MODE_PRIVATE);
        String pin = legacy.getString(LEGACY_PIN, null);
        if (pin != null) {
            store.adoptPin(pin);
            // Version 1.0's recent list is not carried over: it only ever held setup test pages.
            legacy.edit().clear().commit();
        }
        return store;
    }
}
