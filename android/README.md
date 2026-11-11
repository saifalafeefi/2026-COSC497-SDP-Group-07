# pulse watch for android

the band's dashboard on a phone, over bluetooth LE. no wifi, no master, no IP
address: pair it like earbuds and read what the band works out itself (its
heart-rate detector and its on-board autoencoder), or the master's verdict when
the master is connected.

- **live**: stress level against the wearer's line, the 64 Hz pulse (sensor
  counts), heart rate, SpO₂, motion, 10 minutes of heart rate, both on-band
  detectors, and the band's reason whenever there is no verdict yet (finger
  check, warming up, settling in, movement).
- **history**: every second the band reads a pulse, kept on the phone for 30
  days: per day the time monitored, calm %, flags, heart rate, a chart, and a
  card for each flag.
- **settings**: the band (flash its light, forget it), its WiFi network and IP
  (copy, for adding it to the master), sensitivity, alerts, data (PDF report,
  raw CSV, delete), about.
- **wifi setup**: offered once for a band with no saved network. reads the
  phone's network name (asks for location the first time; android only reveals
  it that way), the password goes to the band's encrypted WIFI characteristic,
  so the phone pairs first (android's own prompt, no PIN). the band joins and
  reports back its IP.
- **calibration** is done from the master's roster only; the app shows whether
  the wearer is calibrated, as the band remembers it.
- **demo**: "No band with you? Try the demo" plays a simulated band (calm,
  a flag, recovery) for showing the app without hardware.

the band side is `pulse_headless/board_ble.h`: service `7a1e0001-…` with STATUS
(1 Hz JSON), WAVE (64 Hz samples), INFO (finger check, reason, calibration),
CMD (`sens=`, `id=`), WIFI (encrypted write), NET (ssid, ip, join result), plus
the standard heart rate service so fitness apps can read bpm.

## build

needs a JDK 17–21 (AGP 8.7 does not run on 25) and the android SDK (platform 35,
build-tools 35.0.0); `local.properties` points at the SDK (`sdk.dir=…`).

```bash
cd android
./gradlew assembleRelease
```

the apk is `app/build/outputs/apk/release/app-release.apk`. release signing
reads `keystore/release.properties` (gitignored); without it the build signs
with the debug key, which installs the same way but can't update an apk
signed with the release key.

## install

copy the apk to the phone, open it, allow "install unknown apps" for whatever
opened it. android 8 or newer, bluetooth LE required.
