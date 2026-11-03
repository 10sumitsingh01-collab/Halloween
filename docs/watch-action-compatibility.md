# MedMate Watch Action Compatibility Audit & Test Specification

**Document Version:** 1.0  
**Target Hardware:** Fire-Boltt BSW052 Smartwatch  
**Scope:** Section 2 (Repository & Capability Audit), Phase A Probes Specification, and Phase B Manual Test Protocol.

---

## 1. Executive Summary

MedMate currently delivers medication reminders to patients via Telegram messages with inline buttons (`Taken` and `Snooze 10 min`). On patient smartphones paired to a **Fire-Boltt BSW052** smartwatch, notification text is mirrored to the watch screen. However, to confirm or snooze a dose, the patient is currently forced to pick up their smartphone.

This document audits the existing MedMate backend, analyzes the hardware and software constraints of the Fire-Boltt BSW052, defines three isolated compatibility probes (Notification Actions, Telecom In-Call Actions, and Alarm/TTS Audio Routing), and provides a structured manual test protocol to determine whether any watch-initiated action or hands-free interaction is feasible.

---

## 2. Repository Architecture & Capability Audit

### 2.1 Existing Reminder Delivery Flow
1. **Trigger:** The background scheduler (`app/scheduler.py` invoking `app/services/escalation.py`) evaluates due doses across active schedules.
2. **Notification Dispatch:** `app/notifiers/telegram.py` formats the reminder using `app/notifiers/templates.py:format_patient_reminder` (displaying medication name, strength, instructions, and due time in the patient's local timezone).
3. **Payload Construction:** Outbound messages sent via Telegram Bot API `sendMessage` include an `inline_keyboard` markup with two callback buttons:
   - `Taken` &rarr; `callback_data="taken:<dose_id>"`
   - `Snooze 10 min` &rarr; `callback_data="snooze:<dose_id>"`

### 2.2 Existing Taken/Snooze Action Flow
MedMate supports two distinct paths for action confirmation, both converging on `app/services/dose_service.py` and `app/services/dose_engine.py`:

#### Path A: Telegram Poller (`app/services/telegram_poller.py`)
- Background polling loop fetches updates via Telegram `getUpdates`.
- Validates callback query structure strictly against regex `^(taken|snooze):(\d+)$`.
- Verifies patient ownership: checks that `dose.schedule.medication.patient.telegram_chat_id` strictly matches the callback sender's Telegram chat ID.
- Executes business logic:
  - `taken`: invokes `dose_service.confirm_dose_taken(db, dose_id, source=DoseSource.PHONE, is_caregiver=False, clock=clock)`.
  - `snooze`: invokes `dose_service.snooze_dose(db, dose_id, minutes=10, clock=clock)`.

#### Path B: REST API (`app/routers/doses.py`)
- `POST /api/v1/doses/{id}/taken`: accepts optional `source` (defaults to `DoseSource.PHONE`), authenticated via `require_assistant_or_patient_key` (permits caregiver key, patient key, or patient-bound assistant key).
- `POST /api/v1/doses/{id}/snooze`: accepts `minutes` (1–60, default 10), authenticated via `require_assistant_or_patient_key`.
- **Validation Rules Enforced in `dose_engine.py`:**
  1. Already terminal check: rejected with 409 if status is already `taken`, `late`, or `skipped`.
  2. Early window guard: rejected with 409 if current time is earlier than `due_at - EARLY_WINDOW_MINUTES` (default 60 min).
  3. Double-dose guard: rejected with 409 if a dose for the same schedule was confirmed within `DOUBLE_DOSE_GUARD_MINUTES` (default 30 min).
  4. Late window check: past `LATE_WINDOW_MINUTES` (default 120 min), patient confirmation is rejected with 409 ("dose window closed").
  5. Stock decrement: atomic decrement of `medication.stock_remaining` upon valid confirmation.

### 2.3 Device & Client Integration Audit
An inspection of the repository confirmed:
- **No Android native application** currently exists in the codebase.
- **No Progressive Web App (PWA), Web Push, or Firebase Cloud Messaging (FCM)** exists.
- Reminders are solely coupled to the Telegram Bot API and SMS/WhatsApp mock abstractions.

### 2.4 Telegram Inline Buttons vs. Android Notification Actions
A critical architectural distinction must be noted:
- **Telegram Inline Buttons:** These are proprietary UI elements rendered exclusively within the Telegram client app window. They communicate directly with Telegram's cloud servers via Telegram MTProto/HTTPS. They are **not** Android OS notification action buttons.
- **Android Notification Actions:** These are native OS objects (`NotificationCompat.Action`) attached to the Android system notification drawer. They trigger a local Android `BroadcastReceiver`, `Service`, or `PendingIntent`.
- **Why the Watch Currently Cannot Act on Telegram Reminders:**
  When Telegram displays a notification on the phone, the Da Fit app intercepts the Android status bar notification text (title and message body) via Android's `NotificationListenerService` and relays the text bytes over Bluetooth Low Energy (BLE) to the watch. Because Telegram does not expose its proprietary in-chat inline keyboard buttons as Android notification actions, and because the watch's proprietary firmware cannot render interactive Telegram inline keyboards, the watch only displays static text.

---

## 3. Hardware & Companion App Reality (Fire-Boltt BSW052 via Da Fit)

### 3.1 Confirmed Evidence
- The watch vibrates/buzzes upon receiving incoming notifications mirrored from the phone.
- The watch screen displays the text of the notification.
- To take action, the user must physically access their smartphone.

### 3.2 Technical Architecture of the BSW052
The Fire-Boltt BSW052 is a budget smartwatch running a lightweight proprietary Real-Time Operating System (RTOS) (typically based on Realtek/Goodix/Actions microcontrollers), paired exclusively via the Da Fit companion app.
- **Not WearOS:** Unlike Google WearOS or Apple watchOS, the BSW052 does not run a third-party application runtime, does not support custom watch APKs, and does not support standard Android Wear notification action extensions.
- **Companion App Gateway:** All data transferred between phone and watch passes through the Da Fit app via vendor-specific BLE GATT profiles.
- **Bluetooth Calling Capability:** The BSW052 supports Bluetooth calling (Dual Bluetooth / HFP - Hands-Free Profile). It contains an integrated wrist speaker and microphone, and registers with Android as a Bluetooth hands-free audio device.

### 3.3 What Remains Unknown
1. Does the Da Fit app parse native Android `NotificationCompat.Action` buttons and transmit action triggers back to the phone when pressed on the watch? (Most RTOS companion apps only mirror text and discard action buttons).
2. Does the watch firmware expose interactive action buttons for third-party notifications, or only a single "Dismiss/Clear" button?
3. Can an incoming self-managed Android Telecom phone call trigger the watch's native incoming call screen with interactive "Answer / Reject" buttons?
4. If an alarm with Text-to-Speech audio is routed to the Bluetooth SCO/hands-free channel, does the watch wrist speaker output the audio clearly?

---

## 4. Three-Probe Compatibility Strategy

To answer these unknowns empirically without resorting to reverse-engineering private protocols, three isolated probes are specified:

```
+-----------------------------------------------------------------------------------+
|                        MedMate Watch Test Probes (Phase A)                        |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  [Probe 1: Notification Actions]                                                 |
|    - System notification with explicit Taken (TEST) and Snooze 10 min (TEST)     |
|    - Tests: Does Da Fit mirror actions? Can watch invoke PendingIntent?          |
|                                                                                   |
|  [Probe 2: Telecom Calling (HFP)]                                                 |
|    - Self-managed PhoneAccount & ConnectionService                                |
|    - Caller Name: "MedMate TEST: Metformin 500 mg"                               |
|    - Tests: Does watch ring with caller ID? Can watch Answer (Taken) or Reject?   |
|                                                                                   |
|  [Probe 3: Alarm & Audio Routing (SCO/A2DP)]                                      |
|    - High-priority alarm sound + Text-to-Speech spoken medication instructions   |
|    - Toggle: Phone Speaker vs. Bluetooth Hands-Free Device (Watch Speaker)        |
|    - Tests: Does audio play aloud through the watch wrist speaker?                |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Probe 1: Notification Action Probe
- **Mechanism:** Posts a high-priority `NotificationCompat.Builder` alert to a dedicated channel (`medmate_test_channel`).
- **Actions Attached:**
  - `Taken (TEST)` &rarr; `BroadcastReceiver` logging `"ACTION taken"`
  - `Snooze 10 min (TEST)` &rarr; `BroadcastReceiver` logging `"ACTION snooze"`
- **Notification Content:**
  - Title: `MedMate Watch Compatibility Test`
  - Body: `Test notification only. No real medication dose will be changed.`

### Probe 2: Telecom Incoming Call Probe
- **Mechanism:** Utilizes Android's self-managed `TelecomManager` API with a custom `ConnectionService` and `PhoneAccount`.
- **Target Display Name:** `"MedMate TEST: Metformin 500 mg"`
- **Lifecycle & Logging:**
  - When user answers (on watch or phone): logs `"CALL answered"`
  - When user rejects/hangs up (on watch or phone): logs `"CALL rejected"`
  - When unanswered for 60 seconds: logs `"CALL missed"` and terminates call.
- **Permissions Required:** `android.permission.MANAGE_OWN_CALLS` (normal permission, self-managed, no carrier access required).

### Probe 3: Alarm Sound & TTS Audio Routing Probe
- **Mechanism:** Plays a looping alarm tone combined with Android `TextToSpeech` synthesized speech:
  `"Time to take Metformin, 500 milligram, after breakfast"` repeating every 20 seconds until stopped.
- **Audio Routing:**
  - Route Selector: Toggle between standard device speaker and Bluetooth Hands-Free (SCO / communication device).
  - Audio Route Logging: In-app log records active route type using `AudioDeviceInfo.getType()` (e.g., `TYPE_BUILTIN_SPEAKER`, `TYPE_BLUETOOTH_SCO`, `TYPE_BLUETOOTH_A2DP`), **never** logging hardware MAC addresses or private device names.

---

## 5. Phase B Manual Test Protocol

### 5.1 Da Fit Pre-Test Checklist & Setup
1. **Open Da Fit app on phone**: Navigate to the **Device** tab.
2. **Notification Access**: Go to **Notifications** > Turn ON **"Other apps"** (or find **"MedMate Watch Test"** in the app list and turn it ON).
3. **BLE Connection**: Ensure phone is connected to the Fire-Boltt BSW052 via Bluetooth Low Energy (BLE) inside Da Fit.
4. **Bluetooth Call / Audio Connection (Probes 2 & 3)**:
   - Ensure the watch is also connected as a Bluetooth phone / audio device in Android's system Bluetooth settings.
   - *Note:* The Fire-Boltt BSW052 uses dual Bluetooth: one BLE link for Da Fit fitness/notification syncing, and one classic Bluetooth HFP/A2DP link (e.g. "BSW052_Phone") for phone calls and speaker audio.
5. **Battery Optimization**: Ensure Da Fit has background autostart and battery optimization disabled in Android settings so notifications are not dropped.

### 5.2 Build & Install Instructions
To build and install the test APK from source:
```bash
# Set Java 17 and toolchain paths
export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

# Build debug APK
cd medmate-watch-test
./gradlew assembleDebug

# Install and launch on phone via ADB
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.medmate.watchtest/.MainActivity
```

### 5.3 Test Execution Steps
- **Test Run 1: Notification Actions**
  1. Tap `Post Test Notification`.
  2. Observe watch display and vibration.
  3. Inspect watch screen for `Taken (TEST)` or `Snooze 10 min (TEST)` buttons.
  4. Attempt to tap an action button on the watch. Check Android app on-screen log.
  5. Repeat with the phone screen locked.

- **Test Run 2: Telecom Incoming Call**
  1. Tap `Raise Test Call`.
  2. Observe watch response: does the watch ring/vibrate with caller ID `"MedMate TEST: Metformin 500 mg"`?
  3. Attempt to answer the call on the watch: check if `"CALL answered"` is logged on phone.
  4. Attempt to reject the call on the watch: check if `"CALL rejected"` is logged on phone.
  5. Allow to ring for 60 seconds without answering: verify `"CALL missed"` is logged.

- **Test Run 3: Alarm & Audio Routing**
  1. Toggle audio route to `Bluetooth Hands-Free`.
  2. Tap `Start Alarm Probe`.
  3. Listen for alarm and synthesized TTS voice on the watch wrist speaker.
  4. Tap `Stop Alarm Probe`.

---

## 6. Manual Test Results Table

*(The tester should complete this table following physical verification with the Fire-Boltt BSW052. Leave companion app version blank until testing)*

| Probe | Watch Buzzed or Rang? | Text / Caller Name Shown on Watch? | Action or Answer/Reject Possible on Watch? | Action Actually Recorded by Android App? | Audio Audible from Watch Speaker? | Android Version | Companion App Name & Version | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Probe 1: Notification Actions** | | | | | N/A | | Da Fit (v: ) | |
| **Probe 2: Telecom Incoming Call** | | | | | | | Da Fit (v: ) | |
| **Probe 3: Alarm & TTS Audio** | | | N/A | N/A | | | Da Fit (v: ) | |

---

## 7. Phase C Bridge Architecture Specification (Documented for Future Implementation)

> [!IMPORTANT]
> **Phase C Deferred:** The backend bridge in Phase C will be designed and implemented **only after** the manual test results in Section 6 are filled in and physical watch capabilities are confirmed. No Phase C code or backend modifications are performed in this phase.

When Phase C is authorized, the Android-to-backend bridge must adhere to the following architecture:
1. **Limited Device Token:** The Android test app must authenticate using a dedicated, scoped assistant token (`ASSISTANT_API_KEY`) bound strictly to a dedicated test patient ID (`ASSISTANT_PATIENT_ID`).
2. **Restricted Endpoint Access:** The token will only have access to:
   - `GET /api/v1/patients/{id}/doses/today`
   - `POST /api/v1/doses/{id}/taken`
   - `POST /api/v1/doses/{id}/snooze`
   All other administrative, caregiver, medication, and schedule endpoints will return HTTP 403.
3. **Network Transport Security:**
   - Production builds: Strict HTTPS enforcement with certificate validation.
   - Debug / Local Test builds: Cleartext HTTP (`usesCleartextTraffic="true"`) permitted **only** to the designated local Mac development IP address via an explicit Android Network Security Configuration (`res/xml/network_security_config.xml`).
4. **Idempotency & Backend Authority:**
   - Actions triggered from the watch/app submit to the backend. The backend dose engine remains the sole source of truth for stock decrement, double-dose guards, and timing rules.

---

## 8. Risks & Safety Limitations

1. **Risk of Silent Notification Action Dropping:** Most budget RTOS companion apps silently strip notification actions. Probe 1 will decisively verify if this limitation applies.
2. **Bluetooth Connection State Flakiness:** If the watch's secondary Bluetooth audio channel enters sleep mode, incoming call HFP signals may experience delay.
3. **Zero Production Mutation:** All probes operate in isolated test mode. No production patient schedules or dose records can be modified by the compatibility test app.

