// board_ble.h -- the board talks to the Pulse Watch phone app over Bluetooth LE.
//
// No WiFi, no IP address, no master needed: the phone pairs with the band like a pair
// of earbuds (Android's companion-device sheet, filtered on PULSE_SVC) and reads
// the band's own verdict, which the board already works out itself. The
// ESP32-S3 has Bluetooth LE only; the Arduino core 3.3.x ships the NimBLE stack.
//
// GATT, service PULSE_SVC:
//   STATUS  read + notify, once a second: compact JSON (fits a 256-byte MTU)
//     {"sub","hr","sp","fi":finger state,"st":verdict state,
//      "src":master|board|none,"lv":0..1,"fl":0|1,"thr","w":seconds to wait,
//      "od":{"st","l","f"} board detector, "ae":{"st","l","f","ms"} board AI,
//      "mo":motion 0..1,"sens","rs":wifi rssi,"up":seconds}
//   WAVE    notify, ~8 a second: u32 index of the first sample (little endian),
//           then int16 samples of the conditioned 64 Hz pulse
//   CMD     write, text: "sens=0.62" sets the slider, "id=5" flashes the LED
//   INFO    read + notify, once a second beside STATUS (which is near its
//     size limit): {"fc":finger state,"fn":n,"fo":of,"fk":keep s,
//     "why":the reason with the verdict, "rs":wifi rssi,"up":seconds}
//   WIFI    write, ENCRYPTED: "J" 0x1F ssid 0x1F password -- join that network
//     now (and keep it). The first write makes the phone pair with the band
//     (LE Secure Connections, Just Works), so the password is never sent in
//     the clear. Goes through the same wifiRequest() as serial and /wifi.
//   NET     read + notify every 2 s: {"st":"on"|"joining"|"off","ss":ssid,
//     "ip":address,"rs":rssi,"n":networks saved,"jr":""|"pending"|"ok"|"fail",
//     "js":the ssid asked for} -- so the app can show the IP the master needs
// plus the standard Heart Rate service (0x180D / 0x2A37) so any fitness app or
// watch can read the heart rate too.
//
// Included from pulse_headless.ino after wsTick(): it uses the conditioned
// ring, verdictNow() and the detector / autoencoder state defined above it.
#pragma once

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <BLESecurity.h>

#define PULSE_SVC   "7a1e0001-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_STAT  "7a1e0002-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_WAVE  "7a1e0003-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_CMD   "7a1e0004-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_INFO  "7a1e0005-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_WIFI  "7a1e0006-6b2c-4f1a-9d3e-70756c736521"
#define PULSE_NET   "7a1e0007-6b2c-4f1a-9d3e-70756c736521"
#define BLE_NET_MS  2000
#define BLE_JOIN_MS 30000           // a join-now gives up (and reverts) after 25 s
#define BLE_WAVE_MS   125            // as the websocket: 8 packets a second
#define BLE_WAVE_MAX  24             // samples per packet: 4 + 48 bytes
#define BLE_STAT_MS   1000

static BLEServer *bleSrv = NULL;
static BLECharacteristic *bleStat = NULL, *bleWave = NULL, *bleHr = NULL, *bleInfo = NULL,
                         *bleNet = NULL;
// a WiFi request written by the phone, applied on the main loop
static char bleWifiSsid[33], bleWifiPass[65];
static volatile bool bleWifiReq = false;
static char bleJoinSsid[33] = "";    // the last network the phone asked for
static uint32_t bleJoinAt = 0;

static volatile bool bleReadvertise = false;
// bleOk, bleClients, bleSentStat/Wave and condBleTail live in the .ino: /health
// and condPush() use them before this file is included
static uint32_t bleWaveIdx = 0;      // index of the next sample this link sends

static volatile float bleSensReq = -1.0f;   // CMD writes land on the BLE task;
static volatile uint32_t bleIdReq = 0;      // the main loop applies them

class PulseSrvCb : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    bleClients = bleClients + 1;
    // keep advertising: a second phone (or the PC) can join while one is
    // connected. NimBLE here allows 3 (CONFIG_BT_NIMBLE_MAX_CONNECTIONS)
    if (bleClients < 3) bleReadvertise = true;
  }
  void onDisconnect(BLEServer *) override {
    bleClients = bleClients > 0 ? bleClients - 1 : 0;
    bleReadvertise = true;           // let the next phone find it
  }
};

class PulseCmdCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    String s = c->getValue();
    s.trim();
    if (s.startsWith("sens=")) {
      float v = s.substring(5).toFloat();
      if (v >= 0.0f && v <= 1.0f) bleSensReq = v;
    } else if (s.startsWith("id=")) {
      long n = s.substring(3).toInt();
      bleIdReq = (uint32_t)constrain(n, 1L, 30L) * 1000UL;
    }
  }
};

// pairing results, on serial: a phone that fails to pair can't send WiFi
class PulseSecCb : public BLESecurityCallbacks {
  void onAuthenticationComplete(ble_gap_conn_desc *desc, int status) override {
    if (status == 0)
      Serial.printf("# bluetooth: paired (encrypted=%d bonded=%d)\n", (int)desc->sec_state.encrypted,
                    (int)desc->sec_state.bonded);
    else
      Serial.printf("# bluetooth: pairing failed, rc %d\n", status);
  }
};

// "J" 0x1F ssid 0x1F password
class PulseWifiCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    String v = c->getValue();
    int a = v.indexOf((char)0x1F);
    int b = a < 0 ? -1 : v.indexOf((char)0x1F, a + 1);
    if (a != 1 || b < 0 || v[0] != 'J' || bleWifiReq) return;
    String ssid = v.substring(a + 1, b), pass = v.substring(b + 1);
    if (ssid.length() == 0 || ssid.length() > 32 || pass.length() > 64) return;
    strncpy(bleWifiSsid, ssid.c_str(), sizeof(bleWifiSsid) - 1);
    bleWifiSsid[sizeof(bleWifiSsid) - 1] = 0;
    strncpy(bleWifiPass, pass.c_str(), sizeof(bleWifiPass) - 1);
    bleWifiPass[sizeof(bleWifiPass) - 1] = 0;
    bleWifiReq = true;
  }
};

void bleBegin() {
  // "Pulse ca3ff0": the last six hex of the MAC, as the roster names the board
  String name = "Pulse " + devId.substring(devId.length() - 6);
  BLEDevice::init(name.c_str());
  BLEDevice::setMTU(256);
  // pairing is only asked for when the phone writes WiFi details: Just Works
  // (the band has no screen or keys), LE Secure Connections, bonded so it is
  // asked once. Everything else on the band stays readable without pairing.
  BLESecurity *sec = new BLESecurity();
  sec->setCapability(ESP_IO_CAP_NONE);
  sec->setAuthenticationMode(true, false, true);
  BLEDevice::setSecurityCallbacks(new PulseSecCb());
  bleSrv = BLEDevice::createServer();
  bleSrv->setCallbacks(new PulseSrvCb());

  BLEService *svc = bleSrv->createService(PULSE_SVC);
  bleStat = svc->createCharacteristic(PULSE_STAT,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  bleStat->addDescriptor(new BLE2902());
  bleWave = svc->createCharacteristic(PULSE_WAVE, BLECharacteristic::PROPERTY_NOTIFY);
  bleWave->addDescriptor(new BLE2902());
  bleInfo = svc->createCharacteristic(PULSE_INFO,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  bleInfo->addDescriptor(new BLE2902());
  BLECharacteristic *wifi = svc->createCharacteristic(PULSE_WIFI,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_ENC);
  wifi->setCallbacks(new PulseWifiCb());
  bleNet = svc->createCharacteristic(PULSE_NET,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  bleNet->addDescriptor(new BLE2902());
  BLECharacteristic *cmd = svc->createCharacteristic(PULSE_CMD,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  cmd->setCallbacks(new PulseCmdCb());
  svc->start();

  BLEService *hrs = bleSrv->createService(BLEUUID((uint16_t)0x180D));
  bleHr = hrs->createCharacteristic(BLEUUID((uint16_t)0x2A37), BLECharacteristic::PROPERTY_NOTIFY);
  bleHr->addDescriptor(new BLE2902());
  hrs->start();

  // the service in the advertisement (what the phone filters on), the name in
  // the scan response (what the pairing sheet shows): both would not fit in
  // one 31-byte packet beside a 128-bit UUID
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  BLEAdvertisementData ad, sr;
  ad.setFlags(0x06);                 // general discoverable, LE only
  ad.setCompleteServices(BLEUUID(PULSE_SVC));
  sr.setName(name);
  adv->setAdvertisementData(ad);
  adv->setScanResponseData(sr);
  adv->setScanResponse(true);
  // every 250-500 ms: found within a second, and WiFi keeps most of the radio
  adv->setMinInterval(0x190);
  adv->setMaxInterval(0x320);
  BLEDevice::startAdvertising();
  bleOk = true;
  Serial.print("# bluetooth advertising as \"");
  Serial.print(name);
  Serial.print("\" heap ");
  Serial.println(ESP.getFreeHeap());
}

static void ble__status() {
  Verdict v = verdictNow();
  bool finger = fingerDetected();
  char hr[8], sp[8], lv[10], od_l[10], ae_l[10], mo[10];
  if (finger && bpmValid) snprintf(hr, sizeof(hr), "%ld", (long)bpmLive); else strcpy(hr, "null");
  if (finger && validSpo2 && spo2 >= 70 && spo2 <= 100) snprintf(sp, sizeof(sp), "%ld", (long)spo2);
  else strcpy(sp, "null");
  if (v.fresh && v.hasLevel) snprintf(lv, sizeof(lv), "%.2f", v.level / 100.0f); else strcpy(lv, "null");
  float bl = bdLevel, al = aeLevel, m = accMotion();
  if (bdReady && bl >= 0.0f) snprintf(od_l, sizeof(od_l), "%.2f", bl); else strcpy(od_l, "null");
  if (al >= 0.0f) snprintf(ae_l, sizeof(ae_l), "%.2f", al); else strcpy(ae_l, "null");
  if (m >= 0.0f) snprintf(mo, sizeof(mo), "%.2f", m); else strcpy(mo, "null");
  // the finger state alone ("on"), not /health's "on:5/5:0"; the wearer's name
  // clipped, so the whole thing stays inside one 253-byte notification
  char fi[12], sub[13];
  strncpy(fi, fingerStateNow(), sizeof(fi) - 1);
  fi[sizeof(fi) - 1] = 0;
  char *colon = strchr(fi, ':');
  if (colon) *colon = 0;
  strncpy(sub, hostSubject, sizeof(sub) - 1);
  sub[sizeof(sub) - 1] = 0;
  for (char *p = sub; *p; p++) if (*p == '"' || *p == '\\') *p = '_';
  char j[256];
  int n = snprintf(j, sizeof(j),
      "{\"sub\":\"%s\",\"hr\":%s,\"sp\":%s,\"fi\":\"%s\","
      "\"st\":\"%s\",\"src\":\"%s\",\"lv\":%s,\"fl\":%d,\"thr\":%.2f,\"w\":%ld,"
      "\"od\":{\"st\":\"%s\",\"l\":%s,\"f\":%d},\"ae\":{\"st\":\"%s\",\"l\":%s,\"f\":%d,\"ms\":%lu},"
      "\"mo\":%s,\"sens\":%.2f,\"rs\":%d,\"up\":%lu}",
      sub, hr, sp, fi,
      v.fresh ? v.state : "stale", v.src, lv, (v.fresh && v.flag == 1) ? 1 : 0, v.thr,
      (long)v.wait, BD_ST_NAME[bdStateId], od_l, (int)bdFlag,
      aeStateName(), ae_l, (int)aeFlag, (unsigned long)aeMs,
      mo, hostSens, WiFi.status() == WL_CONNECTED ? (int)WiFi.RSSI() : 0,
      (unsigned long)(millis() / 1000));
  if (n <= 0 || n > 240) return;     // never send half a JSON (MTU 256 - 3, with margin)
  bleStat->setValue((uint8_t *)j, n);
  bleStat->notify();
  bleSentStat++;

  // INFO: the finger check's progress and the reason, as the dashboards show them
  char fst[12] = "none";
  int fn = 0, fo = 5, fk = 0;
  sscanf(fingerStateNow(), "%11[^:]:%d/%d:%d", fst, &fn, &fo, &fk);
  char why[72];
  whyNow(why, sizeof(why), v.src);
  char k[200];
  int m2 = snprintf(k, sizeof(k), "{\"fc\":\"%s\",\"fn\":%d,\"fo\":%d,\"fk\":%d,\"why\":\"%s\","
                    "\"cal\":%d,\"rs\":%d,\"up\":%lu}", fst, fn, fo, fk, why, (int)hostCal,
                    WiFi.status() == WL_CONNECTED ? (int)WiFi.RSSI() : 0, (unsigned long)(millis() / 1000));
  if (m2 > 0 && m2 < (int)sizeof(k)) {
    bleInfo->setValue((uint8_t *)k, m2);
    bleInfo->notify();
  }
  if (finger && bpmValid && bpmLive > 0 && bpmLive < 256) {
    uint8_t hm[2] = {0x00, (uint8_t)bpmLive};  // flags: 8-bit bpm, no contact bits
    bleHr->setValue(hm, 2);
    bleHr->notify();
  }
}

// NET: where the band is on WiFi, so the phone can show the IP the master needs
static void ble__net() {
  bool on = WiFi.status() == WL_CONNECTED;
  String ss = on ? WiFi.SSID() : String("");
  const char *jr = "";
  if (bleJoinAt) {
    if (on && ss == bleJoinSsid) jr = "ok";
    else if (millis() - bleJoinAt > BLE_JOIN_MS) jr = "fail";
    else jr = "pending";
  }
  char j[200];
  int n = snprintf(j, sizeof(j),
      "{\"st\":\"%s\",\"ss\":\"%s\",\"ip\":\"%s\",\"rs\":%d,\"n\":%d,\"jr\":\"%s\",\"js\":\"%s\"}",
      on ? "on" : (knownCount > 0 ? "joining" : "off"), jsonEsc(ss).c_str(),
      on ? WiFi.localIP().toString().c_str() : "", on ? (int)WiFi.RSSI() : 0, knownCount,
      jr, jsonEsc(String(bleJoinSsid)).c_str());
  if (n <= 0 || n >= (int)sizeof(j)) return;
  bleNet->setValue((uint8_t *)j, n);
  bleNet->notify();
}

// From the main loop, after wsTick(): applies phone commands, sends the status
// once a second and the waveform in packets of up to BLE_WAVE_MAX samples.
void blePoll() {
  if (!bleOk) return;
  static uint32_t lastHeap = 0;
  if (millis() - lastHeap > 10000) {  // Bluetooth costs internal RAM: keep an eye on it
    lastHeap = millis();
    Serial.printf("# heap free=%u min=%u largest=%u ble=%d wifi=%d\n", (unsigned)ESP.getFreeHeap(),
                  (unsigned)ESP.getMinFreeHeap(), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                  (int)bleClients, (int)WiFi.status());
  }
  float sv = bleSensReq;
  if (sv >= 0.0f) {
    bleSensReq = -1.0f;
    hostSens = sv;
    sensChangedMs = millis() | 1;    // saved to NVS like the dashboard's slider
  }
  uint32_t id = bleIdReq;
  if (id) {
    bleIdReq = 0;
    identifyReq = id;
  }
  if (bleReadvertise) {
    bleReadvertise = false;
    BLEDevice::startAdvertising();
  }
  if (bleWifiReq && wifiRequest(2, bleWifiSsid, bleWifiPass)) {   // join now, keep it
    strncpy(bleJoinSsid, bleWifiSsid, sizeof(bleJoinSsid) - 1);
    bleJoinSsid[sizeof(bleJoinSsid) - 1] = 0;
    bleJoinAt = millis() | 1;
    memset(bleWifiPass, 0, sizeof(bleWifiPass));   // the request holds its own copy
    bleWifiReq = false;
    Serial.print("# bluetooth: phone asked to join ");
    Serial.println(bleJoinSsid);
  }
  if (bleClients == 0) {
    condBleTail = condHead;          // nobody listening, do not accumulate
    bleWaveIdx = condTotal;
    return;
  }
  uint32_t now = millis();
  static uint32_t lastStat = 0, lastWave = 0;
  if (now - lastStat >= BLE_STAT_MS) {
    lastStat = now;
    ble__status();
  }
  static uint32_t lastNet = 0;
  if (now - lastNet >= BLE_NET_MS) {
    lastNet = now;
    ble__net();
  }
  if (now - lastWave >= BLE_WAVE_MS) {
    lastWave = now;
    uint8_t pkt[4 + 2 * BLE_WAVE_MAX];
    // the index of condBleTail: condTotal counts every push, and the ring holds
    // (condHead - condBleTail) of them not yet sent
    uint16_t head = condHead;
    uint16_t pend = (uint16_t)((head + COND_CAP - condBleTail) % COND_CAP);
    uint32_t first = condTotal - pend;
    int n = 0;
    while (condBleTail != head && n < BLE_WAVE_MAX) {
      float v = condBuf[condBleTail];
      condBleTail = (condBleTail + 1) % COND_CAP;
      if (v > 32767.0f) v = 32767.0f;
      if (v < -32767.0f) v = -32767.0f;
      int16_t s = (int16_t)lroundf(v);
      pkt[4 + 2 * n] = (uint8_t)(s & 0xFF);
      pkt[5 + 2 * n] = (uint8_t)((s >> 8) & 0xFF);
      n++;
    }
    if (n > 0) {
      memcpy(pkt, &first, 4);        // the S3 is little endian
      bleWave->setValue(pkt, 4 + 2 * n);
      bleWave->notify();
      bleSentWave++;
      bleWaveIdx = first + n;
    }
  }
}
