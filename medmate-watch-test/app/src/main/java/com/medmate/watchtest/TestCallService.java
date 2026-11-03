package com.medmate.watchtest;

import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.telecom.Connection;
import android.telecom.ConnectionRequest;
import android.telecom.ConnectionService;
import android.telecom.DisconnectCause;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;

public class TestCallService extends ConnectionService {

    public static class TestConnection extends Connection {
        private final Handler timeoutHandler = new Handler(Looper.getMainLooper());
        private final Runnable timeoutRunnable;

        public TestConnection() {
            setConnectionProperties(PROPERTY_SELF_MANAGED);
            setConnectionCapabilities(CAPABILITY_SUPPORT_HOLD | CAPABILITY_MUTE);
            setCallerDisplayName("MedMate TEST: Metformin 500 mg", TelecomManager.PRESENTATION_ALLOWED);
            setAddress(Uri.fromParts("tel", "555-MEDMATE", null), TelecomManager.PRESENTATION_ALLOWED);
            setInitializing();
            setRinging();

            timeoutRunnable = () -> {
                if (getState() == STATE_RINGING) {
                    AppLogger.log("CALL missed (timeout)");
                    setDisconnected(new DisconnectCause(DisconnectCause.MISSED, "Call timed out after 60s"));
                    destroy();
                }
            };
            timeoutHandler.postDelayed(timeoutRunnable, 60000);
        }

        private void cancelTimeout() {
            timeoutHandler.removeCallbacks(timeoutRunnable);
        }

        @Override
        public void onAnswer() {
            cancelTimeout();
            AppLogger.log("CALL answered");
            setActive();
            timeoutHandler.postDelayed(() -> {
                setDisconnected(new DisconnectCause(DisconnectCause.LOCAL, "Call completed"));
                destroy();
            }, 1500);
        }

        @Override
        public void onReject() {
            cancelTimeout();
            AppLogger.log("CALL rejected");
            setDisconnected(new DisconnectCause(DisconnectCause.REJECTED, "Call rejected"));
            destroy();
        }

        @Override
        public void onDisconnect() {
            cancelTimeout();
            AppLogger.log("CALL disconnected");
            setDisconnected(new DisconnectCause(DisconnectCause.LOCAL, "Call disconnected"));
            destroy();
        }

        @Override
        public void onAbort() {
            cancelTimeout();
            AppLogger.log("CALL aborted");
            setDisconnected(new DisconnectCause(DisconnectCause.CANCELED, "Call aborted"));
            destroy();
        }
    }

    @Override
    public Connection onCreateIncomingConnection(PhoneAccountHandle connectionManagerPhoneAccount, ConnectionRequest request) {
        AppLogger.log("CALL incoming raised");
        return new TestConnection();
    }

    @Override
    public void onCreateIncomingConnectionFailed(PhoneAccountHandle connectionManagerPhoneAccount, ConnectionRequest request) {
        AppLogger.log("CALL incoming failed to create");
    }
}

