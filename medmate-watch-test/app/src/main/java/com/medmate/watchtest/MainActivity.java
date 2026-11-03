package com.medmate.watchtest;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.telecom.PhoneAccount;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.view.View;
import android.widget.Button;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends AppCompatActivity {
    private static final String CHANNEL_ID = "medmate_test_channel";
    private static final int PERMISSION_REQ_CODE = 101;

    private TextView tvPermissions;
    private Button btnGrantPermissions;
    private TextView tvLog;
    private ScrollView scrollLog;
    private RadioGroup rgAudioRoute;
    private RadioButton rbBluetooth;

    private PhoneAccountHandle phoneAccountHandle;
    private TelecomManager telecomManager;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        initViews();
        createNotificationChannel();
        initTelecomAccount();
        setupLogListener();
        checkPermissions();

        AppLogger.log("App started");
    }

    private void initViews() {
        tvPermissions = findViewById(R.id.tv_permissions);
        btnGrantPermissions = findViewById(R.id.btn_grant_permissions);
        tvLog = findViewById(R.id.tv_log);
        scrollLog = findViewById(R.id.scroll_log);
        rgAudioRoute = findViewById(R.id.rg_audio_route);
        rbBluetooth = findViewById(R.id.rb_bluetooth);

        Button btnProbe1 = findViewById(R.id.btn_probe1);
        Button btnProbe2 = findViewById(R.id.btn_probe2);
        Button btnProbe3Start = findViewById(R.id.btn_probe3_start);
        Button btnProbe3Stop = findViewById(R.id.btn_probe3_stop);
        Button btnClearLog = findViewById(R.id.btn_clear_log);
        Button btnCopyLog = findViewById(R.id.btn_copy_log);

        btnGrantPermissions.setOnClickListener(v -> requestRequiredPermissions());
        btnProbe1.setOnClickListener(v -> fireTestNotification());
        btnProbe2.setOnClickListener(v -> simulateIncomingCall());
        btnProbe3Start.setOnClickListener(v -> startAlarmProbe());
        btnProbe3Stop.setOnClickListener(v -> stopAlarmProbe());

        btnClearLog.setOnClickListener(v -> {
            AppLogger.clearLogs();
            tvLog.setText("");
        });

        btnCopyLog.setOnClickListener(v -> {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null) {
                ClipData clip = ClipData.newPlainText("MedMate Watch Log", tvLog.getText());
                cm.setPrimaryClip(clip);
                Toast.makeText(this, "Log copied to clipboard", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void setupLogListener() {
        // Load initial logs
        StringBuilder sb = new StringBuilder();
        for (String entry : AppLogger.getAllLogs()) {
            sb.append(entry).append("\n");
        }
        tvLog.setText(sb.toString());

        AppLogger.setListener(entry -> {
            tvLog.append(entry + "\n");
            scrollLog.post(() -> scrollLog.fullScroll(View.FOCUS_DOWN));
        });
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "MedMate Test",
                    NotificationManager.IMPORTANCE_HIGH
            );
            channel.setDescription("Medication reminder test notifications");
            channel.enableVibration(true);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    private void initTelecomAccount() {
        try {
            telecomManager = (TelecomManager) getSystemService(Context.TELECOM_SERVICE);
            phoneAccountHandle = new PhoneAccountHandle(
                    new ComponentName(this, TestCallService.class),
                    "MedMateTestAccount"
            );

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && telecomManager != null) {
                PhoneAccount.Builder builder = PhoneAccount.builder(phoneAccountHandle, "MedMate Test Call")
                        .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED);
                telecomManager.registerPhoneAccount(builder.build());
            }
        } catch (Exception e) {
            // Telecom setup fallback
        }
    }

    private void checkPermissions() {
        List<String> missing = getMissingPermissions();
        if (missing.isEmpty()) {
            tvPermissions.setText("All required permissions granted.");
            tvPermissions.setTextColor(ContextCompat.getColor(this, R.color.status_green));
            btnGrantPermissions.setVisibility(View.GONE);
        } else {
            tvPermissions.setText("Missing permissions: " + missing.size() + " required");
            tvPermissions.setTextColor(ContextCompat.getColor(this, R.color.status_red));
            btnGrantPermissions.setVisibility(View.VISIBLE);
        }
    }

    private List<String> getMissingPermissions() {
        List<String> missing = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                missing.add(Manifest.permission.POST_NOTIFICATIONS);
            }
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.MANAGE_OWN_CALLS)
                != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.MANAGE_OWN_CALLS);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT)
                    != PackageManager.PERMISSION_GRANTED) {
                missing.add(Manifest.permission.BLUETOOTH_CONNECT);
            }
        }
        return missing;
    }

    private void requestRequiredPermissions() {
        List<String> missing = getMissingPermissions();
        if (!missing.isEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toArray(new String[0]), PERMISSION_REQ_CODE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQ_CODE) {
            checkPermissions();
            initTelecomAccount();
        }
    }

    // --- PROBE 1: Notification Actions ---
    private void fireTestNotification() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "POST_NOTIFICATIONS permission not granted", Toast.LENGTH_SHORT).show();
            requestRequiredPermissions();
            return;
        }

        Intent takenIntent = new Intent(this, NotificationReceiver.class);
        takenIntent.setAction(NotificationReceiver.ACTION_TAKEN);
        PendingIntent takenPi = PendingIntent.getBroadcast(this, 1, takenIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent snoozeIntent = new Intent(this, NotificationReceiver.class);
        snoozeIntent.setAction(NotificationReceiver.ACTION_SNOOZE);
        PendingIntent snoozePi = PendingIntent.getBroadcast(this, 2, snoozeIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent dismissIntent = new Intent(this, NotificationReceiver.class);
        dismissIntent.setAction(NotificationReceiver.ACTION_DISMISSED);
        PendingIntent dismissPi = PendingIntent.getBroadcast(this, 3, dismissIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent contentIntent = new Intent(this, MainActivity.class);
        contentIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent contentPi = PendingIntent.getActivity(this, 0, contentIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("MedMate TEST: Metformin 500 mg")
                .setContentText("Scheduled: 08:00 (Breakfast). Tap an action below.")
                .setSmallIcon(R.drawable.ic_notification)
                .setContentIntent(contentPi)
                .setDeleteIntent(dismissPi)
                .addAction(new NotificationCompat.Action.Builder(0, "Taken (TEST)", takenPi).build())
                .addAction(new NotificationCompat.Action.Builder(0, "Snooze 10 min (TEST)", snoozePi).build())
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setAutoCancel(true);

        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(NotificationReceiver.NOTIFICATION_ID, builder.build());
            AppLogger.log("Notification fired (id=" + NotificationReceiver.NOTIFICATION_ID + ")");
        }
    }

    // --- PROBE 2: Telecom Calling (HFP) ---
    private void simulateIncomingCall() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.MANAGE_OWN_CALLS)
                != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "MANAGE_OWN_CALLS permission not granted", Toast.LENGTH_SHORT).show();
            requestRequiredPermissions();
            return;
        }

        try {
            if (telecomManager == null) {
                telecomManager = (TelecomManager) getSystemService(Context.TELECOM_SERVICE);
            }
            if (phoneAccountHandle == null) {
                initTelecomAccount();
            }

            Bundle extras = new Bundle();
            extras.putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, phoneAccountHandle);
            extras.putParcelable(TelecomManager.EXTRA_INCOMING_CALL_ADDRESS,
                    Uri.fromParts("tel", "555-MEDMATE", null));

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                telecomManager.addNewIncomingCall(phoneAccountHandle, extras);
            } else {
                AppLogger.log("CALL error: Self-managed Telecom requires Android 8.0+ (API 26)");
            }
        } catch (SecurityException se) {
            AppLogger.log("CALL security exception: " + se.getMessage());
        } catch (Exception e) {
            AppLogger.log("CALL error: " + e.getMessage());
        }
    }

    // --- PROBE 3: Alarm & Audio Routing ---
    private void startAlarmProbe() {
        boolean routeBluetooth = rbBluetooth.isChecked();
        Intent serviceIntent = new Intent(this, TestAlarmService.class);
        serviceIntent.setAction(TestAlarmService.ACTION_START);
        serviceIntent.putExtra(TestAlarmService.EXTRA_ROUTE_BLUETOOTH, routeBluetooth);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent);
        } else {
            startService(serviceIntent);
        }
    }

    private void stopAlarmProbe() {
        Intent serviceIntent = new Intent(this, TestAlarmService.class);
        serviceIntent.setAction(TestAlarmService.ACTION_STOP);
        startService(serviceIntent);
    }
}

