package local.sendtotv;

/**
 * Messages from the background listener (process ":server") to the TV screen (main process).
 * Sent as ordered broadcasts guarded by a signature permission, so only this app can send or
 * receive them; the screen answers RESULT_OK, which tells the listener it is open.
 */
final class Remote {
    static final String PERMISSION = "local.sendtotv.permission.REMOTE";
    static final String ACTION_KEY = "local.sendtotv.action.KEY";
    static final String ACTION_TEXT = "local.sendtotv.action.TEXT";
    static final String ACTION_PING = "local.sendtotv.action.PING";
    static final String EXTRA_KEY = "key";
    static final String EXTRA_TEXT = "text";
    static final String EXTRA_SUBMIT = "submit";

    private Remote() {}
}
