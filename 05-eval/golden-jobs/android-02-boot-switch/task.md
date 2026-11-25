The client's Android app Night Watch (Java, Gradle) watches a camera feed with a foreground service
(`WatchService`). Settings are in `Settings.java` on top of a `Store` (SharedPreferences in the app, a map in the
tests); `gradlew testDebugUnitTest` runs the unit tests. The client wants the app to start watching by itself after
the phone restarts, when the user wants that:

1. A new setting, off by default: `public boolean startAtBoot()` and `public void setStartAtBoot(boolean on)` in
   `Settings`, saved as `"true"`/`"false"` under the key `start_at_boot`.
2. An older version saved this as `autostart` with the values `yes`/`no`. While `start_at_boot` isn't saved yet,
   `startAtBoot()` uses `autostart` (`yes` means on, anything else off). `setStartAtBoot` saves `start_at_boot` and
   removes `autostart`.
3. A switch on the main screen: in `res/layout/activity_main.xml`, a `Switch` with the id `switch_start_at_boot`,
   its text from a new string resource `start_at_boot` = `Start when the phone starts`. `MainActivity` shows the
   saved value and saves every change, like the notifications switch.
4. A `BootReceiver` class (a `BroadcastReceiver`) that starts `WatchService` with `startForegroundService` after the
   phone has started, but only when the setting is on. Put the decision in
   `public static boolean shouldStart(String action, Settings settings)`: true only for the action
   `android.intent.action.BOOT_COMPLETED` with the setting on. Register the receiver in `AndroidManifest.xml` for
   that action (with `android:exported="true"`), and add the permission it needs.

Keep the existing tests working.
