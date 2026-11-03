package com.medmate.watchtest;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.widget.Toast;

public class NotificationReceiver extends BroadcastReceiver {
    public static final String ACTION_TAKEN = "com.medmate.watchtest.ACTION_TAKEN";
    public static final String ACTION_SNOOZE = "com.medmate.watchtest.ACTION_SNOOZE";
    public static final String ACTION_DISMISSED = "com.medmate.watchtest.ACTION_DISMISSED";
    public static final int NOTIFICATION_ID = 1001;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) {
            return;
        }

        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.cancel(NOTIFICATION_ID);
        }

        String action = intent.getAction();
        String toastText;
        if (ACTION_TAKEN.equals(action)) {
            AppLogger.log("Action received: TAKEN");
            toastText = "Action recorded: TAKEN";
        } else if (ACTION_SNOOZE.equals(action)) {
            AppLogger.log("Action received: SNOOZE");
            toastText = "Action recorded: SNOOZE";
        } else if (ACTION_DISMISSED.equals(action)) {
            AppLogger.log("Notification dismissed");
            toastText = "Action recorded: DISMISSED";
        } else {
            return;
        }

        Toast.makeText(context, toastText, Toast.LENGTH_SHORT).show();
    }
}

