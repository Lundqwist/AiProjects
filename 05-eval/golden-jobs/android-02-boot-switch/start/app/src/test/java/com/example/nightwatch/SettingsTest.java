package com.example.nightwatch;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.HashMap;
import java.util.Map;
import org.junit.Test;

public class SettingsTest {
    static final class MapStore implements Store {
        final Map<String, String> map = new HashMap<>();

        @Override
        public String get(String key) {
            return map.get(key);
        }

        @Override
        public void put(String key, String value) {
            map.put(key, value);
        }

        @Override
        public void remove(String key) {
            map.remove(key);
        }
    }

    @Test
    public void notificationsAreOnByDefault() {
        assertTrue(new Settings(new MapStore()).notifications());
    }

    @Test
    public void notificationsCanBeTurnedOff() {
        Settings s = new Settings(new MapStore());
        s.setNotifications(false);
        assertFalse(s.notifications());
    }
}
