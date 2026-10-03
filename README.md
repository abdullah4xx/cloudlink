# CloudLink (LAN)

Share files between a Linux PC and an Android phone on the same Wi-Fi. No internet, no account, no relay server.
Every device is both a server and a client; devices are paired once by comparing a 6-digit code, and every
connection afterwards is mutually authenticated and end-to-end encrypted. Spec: `protocol/PROTOCOL.md`.

```
protocol/    PROTOCOL.md + test_vectors.json (single source of truth; Python and Kotlin both test against it)
linux_app/   Python core (core/), CLI (main_cli.py), GTK4 UI (main_gtk.py), tests/
android/     Kotlin app: foreground service + Jetpack Compose UI, unit tests
packaging/   build_deb.sh, .desktop file
.github/     CI: Python tests + .deb, Android unit tests + debug APK
```

## Linux
```
sudo apt install ./cloudlink_2.0.0_all.deb     # or: packaging/build_deb.sh
sudo ufw allow 47616/tcp && sudo ufw allow 47615/udp   # if you use ufw
cloudlink-gtk                                   # GUI
cloudlink devices | pair <ip> | ls <dev> [path] | get <dev> <path> | put <dev> <file> [dest] | serve
```
Dependencies: `python3-cryptography`, `python3-gi`, `gir1.2-gtk-4.0`.

## Android
Build with GitHub Actions (artifact `cloudlink-apk`) or locally: `cd android && gradle assembleDebug`
(Android SDK + JDK 17). On first run: allow notifications and **All files access** (Settings → shown as a banner).
The app starts a foreground service so paired devices can reach the phone while the app is closed.

## Pairing
1. Same Wi-Fi. Open CloudLink on both. 2. Tap **Pair** next to the other device (or use *Connect by IP*).
3. Accept on the other device. 4. Both screens show the same 6 digits — confirm **Same**.

## Network notes
Discovery uses UDP broadcast (255.255.255.255 and each interface's directed broadcast) on port 47615.
Guest Wi-Fi / "AP isolation" blocks device-to-device traffic entirely — nothing (including manual IP) works there.
Routers that only drop broadcasts: use *Connect by IP* (Android shows the IP in Wi-Fi settings). mDNS is a possible later addition.
