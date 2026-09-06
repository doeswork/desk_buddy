# Robot MQTT: what I found, and the one thing left to do

## Short version

`rc=5` means the broker refused the credentials. It is **not** a network,
firewall, TLS, or port problem — I ruled all of those out (see Evidence).

**The robot is holding a password that no longer matches the broker's.**
Studio's saved copy had also drifted out of sync, so re-provisioning kept
sending a wrong password. I have now made the broker and Studio agree:

    user      black
    password  blackbot2026
    topics    black/#

I verified this account connects **and publishes** (`rc=0`). The only copy
still wrong is the one on the robot, and only you can push that — Studio was
holding `/dev/ttyUSB0` while I worked, so I could not send it myself.

## What you need to do

1. In Studio: **Serial Monitor → Set MQTT**
2. Pick user **black**
3. The password should be prefilled as `blackbot2026`. If the field is
   blank or shows something else, type `blackbot2026` by hand.
4. Save. The robot restarts on its own.

Within about 5 seconds the serial log should read:

    Connecting MQTT… connected

Note there should be **no `(TLS)`** in that line any more. If you still see
`Connecting MQTT (TLS)…` while the settings dump above it says `TLS: no`,
your robot is running firmware older than the fix in `BuddyMQTT.cpp:210`,
and needs a reflash — see "If it still fails" below.

## How to confirm it worked, without watching the log

Run this while the robot is booting:

    journalctl -u mosquitto -f | grep 192.168.16.181

Good:

    New connection from 192.168.16.181:xxxxx on port 1883.
    New client connected from 192.168.16.181 as black (p2, c1, k30, u'black').

Bad (what it says today):

    Client black [192.168.16.181:xxxxx] disconnected: not authorised.

## If it still fails after re-provisioning

Then the robot is not storing what Studio sends, and the next thing to check
is the firmware version. Tell me which of these you see:

**A. The log still says `Connecting MQTT (TLS)…` but `TLS: no`**
Old firmware. `BuddyMQTT.cpp:210` already prints the transport correctly, so
a robot that disagrees predates that commit. Reflash and retry.

**B. The settings dump shows the wrong `User:` or `Server:`**
Provisioning is not saving at all. Capture the full block after the restart
and I will trace `handleMqtt` in `SerialProvisioning.cpp:85`.

**C. Everything looks right and it still says `not authorised`**
Then the password on the wire is not what we think. Reset it by hand and try
that exact string in Set MQTT:

    mosquitto_passwd /etc/mosquitto/passwd black
    sudo systemctl reload mosquitto

**D. `rc=-2` instead of `rc=5`**
That is a TCP failure, not an auth one — a different problem entirely, and
the firmware prints an explanation next to it.

## Evidence — what I ruled out and how

| Suspect | Verdict | How I checked |
|---|---|---|
| Firewall blocking the robot | **Not it** | `firewall_allows(1883)` → allowed, rule `allow tcp 1883 from 192.168.0.0/16`; the robot's subnet is covered |
| Broker bound to loopback only | **Not it** | `ss -tlnp` → `0.0.0.0:1883` and `[::]:1883` |
| Robot cannot reach the broker | **Not it** | Broker log shows `New connection from 192.168.16.181` on every retry — it arrives, then is rejected |
| TLS mismatch | **Not it** | Robot reports `TLS: no`; a TLS/plaintext mismatch gives `rc=-2`, not `rc=5` |
| Studio omitting the password | **Fixed earlier** | The JSON now carries all 7 fields incl. `password` and `tls:false`; 157 bytes, well under the firmware's 512-byte cap |
| Firmware JSON buffer overflow | **Not it** | `StaticJsonDocument<512>` vs a 157-byte command |
| **Wrong password** | **This is it** | `black` + Studio's stored password → `rc=135 Not authorized`. After resyncing → `rc=0 Success` |

The broker's own log is the clincher — the robot is talking to it, and being
turned away on credentials:

    Client black [192.168.16.181:49619] disconnected: not authorised.

## Why the passwords drifted

Studio generates a password, hands it to `mosquitto_passwd` (which stores
only a hash), and until recently threw its own copy away. So nothing could
ever reproduce it. Records for `black` and `studio` existed in
`mqtt_users.json` from an older build, but the broker's hashes had been
rewritten since — including by me while debugging. Both are now re-synced.

Going forward, `create_account()` records the password at creation and
`remove_account()` forgets it, so an account made through Studio will not
drift like this again. An account created by hand with `mosquitto_passwd`
still has no recorded password, and the Set MQTT dialog now says so plainly
instead of leaving an empty field that looked filled in.

## For me, next session

- `studio` account is excluded from the Set MQTT list (it owns `#` and
  shares a client ID with the app itself).
- `system.accounts()` now returns `password` alongside name and topics.
- Serial port was held by PID 530222 (`python -m studio`) throughout, which
  is why I could not drive the robot directly. If you want me to test the
  provisioning round trip myself, close Studio first and say so.
