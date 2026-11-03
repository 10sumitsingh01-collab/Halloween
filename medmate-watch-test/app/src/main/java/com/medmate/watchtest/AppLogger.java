package com.medmate.watchtest;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class AppLogger {
    private static final String TAG = "MedMateWatchTest";
    private static final SimpleDateFormat TIME_FMT = new SimpleDateFormat("HH:mm:ss", Locale.US);
    private static final List<String> logs = new ArrayList<>();
    private static LogListener listener;
    private static final Handler mainHandler = new Handler(Looper.getMainLooper());

    public interface LogListener {
        void onNewLog(String entry);
    }

    public static synchronized void setListener(LogListener l) {
        listener = l;
    }

    public static synchronized void log(String message) {
        String timestamp = TIME_FMT.format(new Date());
        String entry = "[" + timestamp + "] " + message;
        logs.add(entry);
        Log.i(TAG, entry);

        mainHandler.post(() -> {
            synchronized (AppLogger.class) {
                if (listener != null) {
                    listener.onNewLog(entry);
                }
            }
        });
    }

    public static synchronized List<String> getAllLogs() {
        return new ArrayList<>(logs);
    }

    public static synchronized void clearLogs() {
        logs.clear();
    }
}

