package com.medmate.watchtest;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import androidx.core.app.NotificationCompat;

import java.util.List;
import java.util.Locale;

public class TestAlarmService extends Service {
    public static final String ACTION_START = "com.medmate.watchtest.START_ALARM";
    public static final String ACTION_STOP = "com.medmate.watchtest.STOP_ALARM";
    public static final String EXTRA_ROUTE_BLUETOOTH = "EXTRA_ROUTE_BLUETOOTH";
    private static final int FG_NOTIFICATION_ID = 2001;
    private static final String CHANNEL_ID = "medmate_alarm_channel";

    private MediaPlayer mediaPlayer;
    private TextToSpeech tts;
    private AudioManager audioManager;
    private boolean ttsInitialized = false;
    private boolean isRunning = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Runnable ttsRunnable;

    @Override
    public void onCreate() {
        super.onCreate();
        audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        createNotificationChannel();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "MedMate Alarm Probe",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Running active alarm probe");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            stopSelf();
            return START_NOT_STICKY;
        }

        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopAlarm();
            return START_NOT_STICKY;
        }

        if (ACTION_START.equals(action)) {
            boolean routeBluetooth = intent.getBooleanExtra(EXTRA_ROUTE_BLUETOOTH, false);
            startAlarm(routeBluetooth);
        }

        return START_NOT_STICKY;
    }

    private void startAlarm(boolean routeBluetooth) {
        if (isRunning) {
            return;
        }
        isRunning = true;

        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("MedMate TEST: Alarm Probe Active")
                .setContentText("Playing alarm & repeating TTS prompt")
                .setSmallIcon(R.drawable.ic_notification)
                .setOngoing(true)
                .build();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(FG_NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(FG_NOTIFICATION_ID, notification);
        }

        // Configure audio routing
        if (routeBluetooth) {
            AppLogger.log("ALARM started (route: bluetooth sco)");
            applyBluetoothRouting();
        } else {
            AppLogger.log("ALARM started (route: phone speaker)");
            applySpeakerRouting();
        }

        // Initialize and start looping alarm sound
        startLoopingAudio();

        // Initialize Text-To-Speech
        initTtsAndSchedule();
    }

    private void applyBluetoothRouting() {
        if (audioManager == null) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                List<AudioDeviceInfo> devices = audioManager.getAvailableCommunicationDevices();
                AudioDeviceInfo targetDevice = null;
                for (AudioDeviceInfo dev : devices) {
                    int type = dev.getType();
                    if (type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                        type == AudioDeviceInfo.TYPE_BLE_HEADSET) {
                        targetDevice = dev;
                        break;
                    }
                }
                if (targetDevice != null) {
                    audioManager.setCommunicationDevice(targetDevice);
                } else {
                    audioManager.startBluetoothSco();
                    audioManager.setBluetoothScoOn(true);
                }
            } else {
                audioManager.startBluetoothSco();
                audioManager.setBluetoothScoOn(true);
            }
        } catch (Exception e) {
            // Safe fallback
        }
    }

    private void applySpeakerRouting() {
        if (audioManager == null) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                audioManager.clearCommunicationDevice();
            }
            if (audioManager.isBluetoothScoOn()) {
                audioManager.setBluetoothScoOn(false);
                audioManager.stopBluetoothSco();
            }
        } catch (Exception e) {
            // Safe fallback
        }
    }

    private void startLoopingAudio() {
        try {
            Uri alert = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            if (alert == null) {
                alert = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
            }
            mediaPlayer = new MediaPlayer();
            mediaPlayer.setDataSource(this, alert);
            mediaPlayer.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build());
            mediaPlayer.setLooping(true);
            mediaPlayer.prepare();
            mediaPlayer.start();
        } catch (Exception e) {
            // Alarm fallback if ringtone fails
        }
    }

    private void initTtsAndSchedule() {
        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.US);
                ttsInitialized = true;
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override
                    public void onStart(String utteranceId) {}

                    @Override
                    public void onDone(String utteranceId) {
                        if (mediaPlayer != null && isRunning) {
                            try {
                                mediaPlayer.setVolume(1.0f, 1.0f);
                            } catch (Exception ignored) {}
                        }
                    }

                    @Override
                    public void onError(String utteranceId) {
                        if (mediaPlayer != null && isRunning) {
                            try {
                                mediaPlayer.setVolume(1.0f, 1.0f);
                            } catch (Exception ignored) {}
                        }
                    }
                });

                // Speak once immediately, then repeat every 20 seconds
                speakUtterance();
                scheduleNextUtterance();
            }
        });
    }

    private void scheduleNextUtterance() {
        ttsRunnable = () -> {
            if (!isRunning) return;
            speakUtterance();
            scheduleNextUtterance();
        };
        handler.postDelayed(ttsRunnable, 20000);
    }

    private void speakUtterance() {
        if (!ttsInitialized || tts == null) return;
        if (mediaPlayer != null) {
            try {
                mediaPlayer.setVolume(0.15f, 0.15f); // Duck alarm
            } catch (Exception ignored) {}
        }
        AppLogger.log("TTS spoken: Metformin 500 mg");
        tts.speak("Time to take Metformin, 500 milligram, after breakfast",
                TextToSpeech.QUEUE_FLUSH, null, "metformin_tts");
    }

    private void stopAlarm() {
        if (!isRunning) {
            return;
        }
        isRunning = false;
        if (handler != null && ttsRunnable != null) {
            handler.removeCallbacks(ttsRunnable);
        }
        if (mediaPlayer != null) {
            try {
                if (mediaPlayer.isPlaying()) {
                    mediaPlayer.stop();
                }
                mediaPlayer.release();
            } catch (Exception ignored) {}
            mediaPlayer = null;
        }
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Exception ignored) {}
            tts = null;
        }
        applySpeakerRouting();
        AppLogger.log("ALARM stopped by user");
        stopForeground(true);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        if (isRunning) {
            stopAlarm();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}

