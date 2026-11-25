package com.example.wakebutton;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.Toast;

/** One button that wakes the PC. The address is fixed for now; the network code is in WakeOnLan. */
public class MainActivity extends Activity {
    private static final String PC_MAC = "D8:5E:D3:5B:01:71";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Button button = new Button(this);
        button.setText(R.string.wake);
        button.setOnClickListener(v -> new Thread(() -> {
            try {
                WakeOnLan.send(WakeOnLan.parseMac(PC_MAC), "255.255.255.255");
                runOnUiThread(() -> Toast.makeText(this, "Sent", Toast.LENGTH_SHORT).show());
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }).start());
        setContentView(button);
    }
}
