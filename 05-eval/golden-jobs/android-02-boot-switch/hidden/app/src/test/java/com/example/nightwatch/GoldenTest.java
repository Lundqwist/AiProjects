package com.example.nightwatch;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.Test;

public class GoldenTest {
    private static final String BOOT = "android.intent.action.BOOT_COMPLETED";

    private static SettingsTest.MapStore store() {
        return new SettingsTest.MapStore();
    }

    private static String read(String rel) throws Exception {
        Path p = Paths.get(rel);
        if (!Files.exists(p)) p = Paths.get("app").resolve(rel);   // tests run in app\ or in the project folder
        return new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
    }

    @Test
    public void goldenOffByDefault() {
        assertFalse(new Settings(store()).startAtBoot());
    }

    @Test
    public void goldenSavedAsTrueFalse() {
        SettingsTest.MapStore m = store();
        Settings s = new Settings(m);
        s.setStartAtBoot(true);
        assertTrue(s.startAtBoot());
        assertEquals("true", m.map.get("start_at_boot"));
        s.setStartAtBoot(false);
        assertFalse(new Settings(m).startAtBoot());
        assertEquals("false", m.map.get("start_at_boot"));
    }

    @Test
    public void goldenOldAutostartIsRead() {
        SettingsTest.MapStore m = store();
        m.map.put("autostart", "yes");
        assertTrue(new Settings(m).startAtBoot());
        m.map.put("autostart", "no");
        assertFalse(new Settings(m).startAtBoot());
        m.map.put("autostart", "maybe");
        assertFalse(new Settings(m).startAtBoot());
    }

    @Test
    public void goldenNewKeyWinsAndOldKeyIsRemoved() {
        SettingsTest.MapStore m = store();
        m.map.put("autostart", "yes");
        m.map.put("start_at_boot", "false");
        assertFalse(new Settings(m).startAtBoot());
        Settings s = new Settings(m);
        s.setStartAtBoot(true);
        assertNull(m.map.get("autostart"));
        assertEquals("true", m.map.get("start_at_boot"));
    }

    @Test
    public void goldenNotificationsUntouched() {
        SettingsTest.MapStore m = store();
        Settings s = new Settings(m);
        s.setStartAtBoot(true);
        assertTrue(s.notifications());
    }

    @Test
    public void goldenReceiverDecides() {
        SettingsTest.MapStore m = store();
        Settings s = new Settings(m);
        assertFalse(BootReceiver.shouldStart(BOOT, s));
        s.setStartAtBoot(true);
        assertTrue(BootReceiver.shouldStart(BOOT, s));
        assertFalse(BootReceiver.shouldStart("android.intent.action.SCREEN_ON", s));
        assertFalse(BootReceiver.shouldStart(null, s));
    }

    @Test
    public void goldenSwitchInTheLayout() throws Exception {
        String layout = read("src/main/res/layout/activity_main.xml");
        Matcher sw = Pattern.compile("<Switch\\b[^>]*android:id=\"@\\+id/switch_start_at_boot\"[^>]*>", Pattern.DOTALL)
                .matcher(layout);
        assertTrue("a Switch with id switch_start_at_boot", sw.find());
        assertTrue("its text is @string/start_at_boot", sw.group().contains("android:text=\"@string/start_at_boot\""));
        String strings = read("src/main/res/values/strings.xml");
        assertTrue(strings.contains("<string name=\"start_at_boot\">Start when the phone starts</string>"));
    }

    @Test
    public void goldenMainActivityUsesTheSwitch() throws Exception {
        String main = read("src/main/java/com/example/nightwatch/MainActivity.java");
        assertTrue(main.contains("R.id.switch_start_at_boot"));
        assertTrue("reads the setting", Pattern.compile("\\bstartAtBoot\\s*\\(").matcher(main).find());
        assertTrue("saves the setting", main.contains("setStartAtBoot"));
    }

    @Test
    public void goldenManifest() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        assertTrue("RECEIVE_BOOT_COMPLETED permission",
                manifest.contains("<uses-permission android:name=\"android.permission.RECEIVE_BOOT_COMPLETED\""));
        Matcher r = Pattern.compile("<receiver\\b[^>]*android:name=\"(\\.|com\\.example\\.nightwatch\\.)BootReceiver\"[^>]*>(.*?)</receiver>",
                Pattern.DOTALL).matcher(manifest);
        assertTrue("a receiver for BootReceiver", r.find());
        assertTrue("exported", r.group().contains("android:exported=\"true\""));
        assertTrue("for BOOT_COMPLETED", r.group(2).contains("android.intent.action.BOOT_COMPLETED"));
    }
}
