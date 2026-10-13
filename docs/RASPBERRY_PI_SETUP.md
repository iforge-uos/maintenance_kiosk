# Raspberry Pi 3A+ setup

These instructions use your **existing Raspberry Pi OS installation** and preserve the original **800 × 480** kiosk interface. The installer supports Raspberry Pi OS **Bookworm (12) or Trixie (13)**, in 32-bit or 64-bit form, with Python 3.11 or newer. It installs a lightweight fullscreen browser session and starts the app automatically after reboot.

Use a working screen, Wi-Fi connection, a reliable **5 V / 2.5 A** power supply, and at least **2 GB free on the SD card**. On the 3A+'s 512 MB RAM, close other applications. A USB keyboard is useful for setup; SSH also works. There is no need to reflash a supported installation. For a future fresh installation, Raspberry Pi OS Lite uses fewer resources.

## 1. Install from the Pi's terminal

Log in as your normal Pi user with `sudo` access. Run this block in Terminal on the Pi, or after connecting over SSH. **Do not run the whole installer with `sudo`**; it requests elevated access for system changes itself.

```sh
sudo apt update
sudo apt install -y git
git clone --branch codex/restore-kiosk-style --single-branch https://github.com/iforge-uos/maintenance_kiosk.git
cd maintenance_kiosk
bash scripts/install-pi.sh --check
bash scripts/install-pi.sh
```

When installation reports success, reboot:

```sh
sudo reboot
```

If this repository is private, GitHub will require your account's repository access. If you already have this checkout, use the update instructions below instead of cloning over it.

`--check` checks compatibility without installing or changing the system. If it reports an older OS such as Bullseye, back up your files and use a supported Raspberry Pi OS release first; the installer does not upgrade the operating system.

The installer installs the OS packages and Python dependencies, copies the committed app into `/opt/maintenance-kiosk/releases/`, and points `/opt/maintenance-kiosk/current` at that release. It configures these services under your existing user:

- `maintenance-kiosk.service`: web app, one Gunicorn worker with two threads.
- `maintenance-kiosk-worker.service`: Google Sheets synchronization and configured notifications.
- `maintenance-kiosk-hardware.service`: physical trigger/LED worker; waits until you configure real GPIO in Settings.

It uses LightDM, Xorg, Openbox, and Chromium for an automatic fullscreen kiosk session. On an existing desktop installation this changes the boot session to the kiosk; your files remain. It uses your working display configuration and does not change display boot settings. Set the screen to its native **800 × 480** resolution before installation if necessary.

Records, settings, credentials, and queued deliveries live in `/var/lib/maintenance-kiosk`, outside the code releases. Installation does not send test messages or replace existing kiosk settings. It does not reboot until you run the command above. On an existing configured installation, the restarted worker resumes its queued deliveries.

## 2. Configure the kiosk

After reboot, the touchscreen opens <http://127.0.0.1:5000> automatically. Open **Settings** to edit printers, technicians, checklist items, and other options.

Google and notification accounts need credentials that an installer cannot create for you:

1. In Google Drive, sign in as **`threedprintingiforge@gmail.com`** and create the maintenance spreadsheet. This account should own it.
2. Enable the **Google Sheets API** and **Google Drive API** in Google Cloud. Create a service account and download its JSON key. Share the spreadsheet with that service account's email as **Editor**. Keep staff as Viewers; edit records through the kiosk.
3. In **Settings → Google**, enter the spreadsheet ID, select service-account authentication, and save the JSON in its dedicated credential control. See [Google setup](setup.md#google-drive-and-sheets) for details and the OAuth alternative.
4. In **Settings → Discord**, add each channel destination and save its webhook URL in its credential control. In **Settings → Email**, enter SMTP settings and save the password separately.
5. In **Settings → Testing**, check the Google connection, then run the explicit Sheets write, email, and Discord tests you want. Confirm messages arrive before enabling production notifications and Google synchronization.

Local maintenance records work without these connections. Pending deliveries remain on the Pi during an outage. No PostgreSQL is needed.

For physical buttons and LEDs, configure the **`gpiozero`** hardware backend and pin mapping in Settings. Pin numbers are **BCM GPIO numbers**, not physical header positions. The installer supplies the OS GPIO driver; no pins are driven by the default configuration. Check the wiring, then use the bounded physical input and LED tests before relying on automatic maintenance triggers. See [GPIO setup](setup.md#gpio-on-the-raspberry-pi).

## 3. Maintain or troubleshoot it

Use **Ctrl+Alt+F2** with a keyboard to reach a terminal, or use SSH. **Alt+F4 only closes Chromium temporarily**; the kiosk session restarts it. To connect from a Mac, use `ssh YOUR_PI_USER@YOUR_PI_HOSTNAME.local`, substituting the username and hostname you chose in Raspberry Pi OS. The Pi's `whoami`, `hostname`, and `hostname -I` commands show its user, hostname, and IP address.

```sh
systemctl status maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware --no-pager
curl http://127.0.0.1:5000/api/health
journalctl -u maintenance-kiosk -u maintenance-kiosk-worker -u maintenance-kiosk-hardware -n 100 --no-pager
```

The app listens only on the Pi's loopback address. To configure it using your Mac's browser, run this on the Mac and leave the SSH connection open:

```sh
ssh -L 5000:127.0.0.1:5000 YOUR_PI_USER@YOUR_PI_HOSTNAME.local
```

Then open <http://127.0.0.1:5000> on the Mac. Stop any local Mac copy using that port first. The kiosk has no login; keep its server bound to loopback.

### Update the app

In the original Git checkout, check that `git status --short` is empty and you are on `codex/restore-kiosk-style`. Preserve any local edits before continuing. Then run:

```sh
cd ~/maintenance_kiosk
git status --short
git branch --show-current
git pull --ff-only origin codex/restore-kiosk-style
bash scripts/install-pi.sh
```

The installer deploys the checked-out commit and restarts the services. It preserves `/var/lib/maintenance-kiosk`, rejects changes to tracked files, and excludes untracked files. If Git reports conflicts or a non-fast-forward update, stop and resolve them without resetting away your changes.

### Back up records and credentials

Stop all three services for a consistent backup, copy the data, then start them again:

```sh
sudo systemctl stop maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware
umask 077
sudo tar -cz -C /var/lib maintenance-kiosk > "$HOME/maintenance-kiosk-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
sudo systemctl start maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware
```

Keep the backup private: it includes credentials. Copy it off the SD card for recovery. Google Sheets reports and non-secret UI exports are not a complete backup of local settings, credentials, and queued work.

The installer does not import an older checkout's `instance/` directory. If you already have records there, stop the old app and workers, back up both data directories, and copy the **entire** old directory into the new data location while all services are stopped. Do this only when the destination has no records you need to keep; do not merge journals. Restore ownership to the installer user and keep the directory private before restarting the services.

### Disable kiosk startup

From SSH or Ctrl+Alt+F2, stop automatic app startup and remove the installer's display configuration. This retains the application and data:

```sh
sudo systemctl disable --now maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware
sudo rm -f /etc/lightdm/lightdm.conf.d/90-maintenance-kiosk.conf /etc/systemd/system/lightdm.service.d/90-maintenance-kiosk.conf
sudo rm -f /usr/share/xsessions/maintenance-kiosk.desktop
sudo systemctl daemon-reload
```

For a previous **desktop** installation, reboot to use its original LightDM configuration and select the normal desktop session. For a previous **Lite/console** installation, restore console boot before rebooting:

```sh
sudo systemctl disable lightdm
sudo systemctl set-default multi-user.target
sudo reboot
```

This installer is checked in development, but a cloud test cannot verify your Pi's screen, performance, GPIO wiring, or actual message delivery. Verify startup after reboot and the physical tests on your Pi.
