package com.example.nightwatch;

/** Where settings are kept: SharedPreferences in the app, a map in tests. */
public interface Store {
    /** The value, or null when the key isn't there. */
    String get(String key);

    void put(String key, String value);

    void remove(String key);
}
