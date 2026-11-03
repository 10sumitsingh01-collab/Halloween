# MedMate Watch Compatibility Test App

Standalone native Android test application designed to empirically test smartwatch action compatibility on the **Fire-Boltt BSW052** paired via the **Da Fit** companion app.

> **TEST ONLY:** This application operates in full isolation. It contains no backend credentials, makes no network calls, and cannot modify production patient dose records or schedules.

---

## Probes Implemented

### 1. Probe 1: Native Notification Actions
- Posts a high-priority Android system notification (`NotificationCompat.Action`) on channel `medmate_test_channel`.
- Includes two explicit action buttons:
  - **Taken (TEST)** &rarr; logs `[HH:MM:SS] Action received: TAKEN`
  - **Snooze 10 min (TEST)** &rarr; logs `[HH:MM:SS] Action received: SNOOZE`
- Dismissing the notification logs `[HH:MM:SS] Notification dismissed`.
- Verifies whether Da Fit relays action buttons to the watch firmware and allows triggering `PendingIntent` callbacks from the wrist.

### 2. Probe 2: Self-Managed Telecom Calling (HFP)
- Registers a self-managed `PhoneAccount` via Android's `TelecomManager` (`android.permission.MANAGE_OWN_CALLS`).
- Raises an incoming call with caller display name:
  `"MedMate TEST: Metformin 500 mg"` (from `tel:555-MEDMATE`).
- Verifies whether the watch's Bluetooth calling screen triggers with interactive Answer / Reject buttons:
  - **Answer** &rarr; logs `[HH:MM:SS] CALL answered`
  - **Reject** &rarr; logs `[HH:MM:SS] CALL rejected`
  - **Timeout** &rarr; after 60 seconds of ringing, disconnects and logs `[HH:MM:SS] CALL missed (timeout)`

### 3. Probe 3: Looping Alarm & Text-to-Speech (TTS)
- Foreground service playing a looping system alarm tone combined with Android `TextToSpeech`.
- Repeated utterance every 20 seconds:
  *"Time to take Metformin, 500 milligram, after breakfast"*
- Audio routing toggle:
  - **Phone Speaker**: routes standard alarm audio through phone loudspeakers.
  - **Bluetooth Hands-Free**: routes audio through Bluetooth SCO / communication device (the watch's wrist speaker).
- Only device types are logged (never hardware MAC addresses or private device identifiers).

---

## Build Instructions

### Prerequisites
- JDK 17 (e.g., Homebrew OpenJDK 17)
- Android SDK Platform 34 and Build-Tools 34.0.0
- Android Debug Bridge (`adb`)

### Building the APK
```bash
cd medmate-watch-test

# Ensure Java 17 and Android SDK paths are set
export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

# Build debug APK
./gradlew assembleDebug
```
The compiled APK is placed at:
`app/build/outputs/apk/debug/app-debug.apk`

---

## Device Installation via ADB

1. **Enable USB Debugging on Phone**:
   - Open **Settings** > **About phone** > tap **Build number** 7 times.
   - Go to **Settings** > **System** (or Additional settings) > **Developer options**.
   - Turn ON **USB debugging**.
   - Connect phone to Mac via USB. When prompted on the phone, check *"Always allow from this computer"* and tap **Allow**.

2. **Install and Run**:
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.medmate.watchtest/.MainActivity
```

---

## Da Fit Pre-Test Checklist

Before running tests, ensure the companion app and Bluetooth links are properly configured:
1. Open the **Da Fit** app on the smartphone and select the **Device** tab.
2. Go to **Notifications** > Turn ON **"Other apps"** (or locate **MedMate Watch Test** in the app list and toggle it ON).
3. Confirm the phone is connected to the watch over Bluetooth inside Da Fit.
4. For Probes 2 & 3: Ensure the watch is also connected as a Bluetooth phone / audio device in Android's system Bluetooth settings (e.g. `BSW052_Phone`).
5. Disable battery optimization for Da Fit in Android system settings.
