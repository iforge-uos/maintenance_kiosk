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
2. Enable the **Google Sheets API** and **Google Drive API** in Google Cloud. Create a web application OAuth client and download its JSON file. Follow [Google setup](setup.md#oauth-as-the-owner) to set the consent screen and callback URL. A service account is also supported. Keep staff as Viewers; edit records through the kiosk.
3. Open **Settings → Google storage**. Under **Google OAuth client JSON**, select **Choose file**, select the downloaded JSON, then select **Store credential**. Set **Connection method** to **Google account (OAuth)**. Enter the **Workbook ID**, or load a text or JSON file with its ID or Google Sheets URL. Save the settings. Select **Connect Google account** and complete consent with the intended Google account.
4. Open **Settings → Discord webhooks**. Add a destination for each channel. Save a new destination before you store its credential. Under **Discord webhook URL**, select **Choose file** to load its text or JSON file, then select **Store credential**. In **Settings → Email notifications**, enter SMTP settings and store the password separately.
5. In **Settings → Testing**, check the Google connection, then run the explicit Sheets write, email, and Discord tests you want. Confirm messages arrive before enabling production notifications and Google synchronization.

Local maintenance records work without these connections. Pending deliveries remain on the Pi during an outage. No PostgreSQL is needed.

File selection uses the device that runs the browser. On the Pi, use local files or a mounted USB drive. To use files on your Mac, open the Pi app through the SSH tunnel below and select them in the Mac browser. Stored credentials stay on the Pi. Use UTF-8 files of no more than 64 KiB each; see [accepted file formats](setup.md#files-for-credentials-and-workbook-ids). Imports do not enable integrations or send test messages. They preserve other settings, including technician names and semester information.

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

### Automatic updates at boot

Use this option to check a GitHub branch each time you switch on or reboot the Pi. The example uses **`codex/restore-kiosk-style`**, the branch that contains this kiosk version. Only commits pushed to GitHub can reach the Pi.

Do this once in a terminal **on the Pi**, as the same normal user who installed the kiosk. These commands assume the original checkout is `~/maintenance_kiosk`. Change that path if you installed it elsewhere. Check that `git status --short` is empty and `git branch --show-current` shows `codex/restore-kiosk-style` before you continue. Save any local edits first.

```sh
cd ~/maintenance_kiosk &&
git pull --ff-only origin codex/restore-kiosk-style &&
bash scripts/install-pi.sh &&
bash scripts/enable-pi-boot-update.sh --branch codex/restore-kiosk-style
```

Wait for the success message. Then reboot:

```sh
sudo reboot
```

The setup enables `maintenance-kiosk-update.service` for future boots. It does not run an update immediately. At boot, the service checks GitHub before the kiosk starts. It allows up to 50 seconds for the network to become available. The whole update attempt has a 180-second limit. A check with a working connection and no new commit usually takes a few seconds. The kiosk can take longer to open while the update check runs. There are no further automatic checks until the next boot.

When a new commit is available, the service checks the update and prepares a release from that exact commit. It checks Python dependencies and opens Flask pages with temporary test data under the normal Pi user. It then selects the new release before the kiosk starts. These checks do not send notifications or change your maintenance records.

If the Pi is offline, or an update fails its checks, the Pi starts the installed release. The same rule applies if the checkout has changes to tracked files, is on a different branch, or cannot accept a fast-forward update. The service does not reset local changes or delete untracked files. It does not install OS or Python packages at each boot.

Keep the original checkout at the same path and on the selected branch. Records, settings, credentials, and queued deliveries stay in `/var/lib/maintenance-kiosk`. Keep old release directories in `/opt/maintenance-kiosk/releases/`; a new release can use the Python environment from an older release.

Automatic updates cover application code and content that can use the installed dependencies and deployment configuration. If dependency files, OS setup, services, browser scripts, or updater files change, the service keeps the installed release and records that a manual installation is required. Use the manual update procedure below, then run the enable command again.

To check the setup and the latest boot:

```sh
systemctl is-enabled maintenance-kiosk-update.service
journalctl -b -u maintenance-kiosk-update.service --no-pager
readlink -f /opt/maintenance-kiosk/current
```

The first command must show `enabled`. The second shows the update result for this boot. The last shows the installed release path. Test the updater at the next reboot. Do not start the updater manually while the kiosk is running; it refuses to change a running installation.

For a private repository, configure unattended, read-only GitHub access for the normal Pi user. Cloud environment access does not transfer to the Pi. A read-only SSH deploy key is one option. Add GitHub to that user's SSH known hosts during setup. A password prompt or an SSH agent that starts after login cannot provide access at boot. Use an HTTPS or SSH repository URL without an embedded token or password.

To stop future automatic updates, run:

```sh
sudo systemctl disable maintenance-kiosk-update.service
```

The kiosk still starts automatically. This command does not interrupt an update that is already in progress.

To select a different branch, first check out that branch in the original checkout. Pull its commits and run the manual installer. Then run `bash scripts/enable-pi-boot-update.sh --branch YOUR_BRANCH`, with the exact branch name. Do not only change the branch in the checkout; the updater checks that it matches the configured branch.

### Update the app manually

In the original Git checkout, check that `git status --short` is empty and you are on `codex/restore-kiosk-style`. Preserve any local edits before continuing. Then run:

```sh
cd ~/maintenance_kiosk
git status --short
git branch --show-current
git pull --ff-only origin codex/restore-kiosk-style &&
bash scripts/install-pi.sh
```

The installer deploys the checked-out commit and restarts the services. It preserves `/var/lib/maintenance-kiosk`, rejects changes to tracked files, and excludes untracked files. If Git reports conflicts or a non-fast-forward update, stop and resolve them without resetting away your changes.

If you use automatic updates, refresh their setup after a manual installation:

```sh
bash scripts/enable-pi-boot-update.sh --branch codex/restore-kiosk-style
```

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
sudo systemctl disable maintenance-kiosk-update.service
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
