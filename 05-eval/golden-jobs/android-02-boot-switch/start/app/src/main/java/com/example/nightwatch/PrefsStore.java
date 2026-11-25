package com.example.nightwatch;

import android.content.Context;
import android.content.SharedPreferences;

/** The app's Store: SharedPreferences. */
public final class PrefsStore implements Store {
    private final SharedPreferences prefs;

    public PrefsStore(Context context) {
        prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE);
    }

    @Override
    public String get(String key) {
        return prefs.getString(key, null);
    }

    @Override
    public void put(String key, String value) {
        prefs.edit().putString(key, value).apply();
    }

    @Override
    public void remove(String key) {
        prefs.edit().remove(key).apply();
    }
}
