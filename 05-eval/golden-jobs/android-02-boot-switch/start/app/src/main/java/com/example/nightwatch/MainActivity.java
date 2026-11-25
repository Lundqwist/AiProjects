package com.example.nightwatch;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Button;
import android.widget.Switch;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        Settings settings = new Settings(new PrefsStore(this));

        Switch notifications = findViewById(R.id.switch_notifications);
        notifications.setChecked(settings.notifications());
        notifications.setOnCheckedChangeListener((v, on) -> settings.setNotifications(on));

        Button start = findViewById(R.id.button_start);
        start.setOnClickListener(v -> startForegroundService(new Intent(this, WatchService.class)));
    }
}
