package com.example.nightwatch;

/** The app's settings. Plain Java on top of a Store, so it can be tested on the PC. */
public final class Settings {
    static final String NOTIFICATIONS = "notifications";

    private final Store store;

    public Settings(Store store) {
        this.store = store;
    }

    /** Show a notification when motion is seen. On by default. */
    public boolean notifications() {
        String v = store.get(NOTIFICATIONS);
        return v == null || Boolean.parseBoolean(v);
    }

    public void setNotifications(boolean on) {
        store.put(NOTIFICATIONS, Boolean.toString(on));
    }
}
