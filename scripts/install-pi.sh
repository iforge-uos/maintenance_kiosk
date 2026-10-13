#!/usr/bin/env bash
# Install the committed kiosk checkout on an existing Raspberry Pi OS system.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/install-pi.sh [--check] [--user EXISTING_USER]

Installs this committed checkout on Raspberry Pi OS Bookworm/Trixie (32/64-bit).
The web app, delivery worker, GPIO watcher and browser run as your existing user.
Sets up a lightweight, automatic kiosk login for the next reboot. Does not reboot.

  --check       Read-only prerequisite checks; no sudo, packages or system changes.
  --user USER   Existing non-root login account (defaults to you / SUDO_USER).
  --help        Show this help without checking or changing the machine.

Application: /opt/maintenance-kiosk/current (versioned releases are retained)
Data:        /var/lib/maintenance-kiosk (existing records/settings are retained)
Local URL:   http://127.0.0.1:5000

An Internet connection and sudo access are needed for installation. Google,
email, Discord and GPIO are configured afterwards in the kiosk Settings page.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
check_only=false
kiosk_user="${SUDO_USER:-$(id -un)}"
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --check) check_only=true; shift ;;
        --user)
            (($# >= 2)) || die '--user needs an existing non-root username.'
            kiosk_user="$2"; shift 2 ;;
        *) die "Unknown option: $1 (use --help)." ;;
    esac
done
[[ "$kiosk_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || die 'Invalid login username.'
[[ "$kiosk_user" != root ]] || die 'Run as your normal Pi user, or use --user EXISTING_USER. The browser must not run as root.'
account="$(getent passwd "$kiosk_user")" || die "No existing account named $kiosk_user."
IFS=: read -r _ _ kiosk_uid kiosk_gid _ kiosk_home kiosk_shell <<<"$account"
((kiosk_uid >= 1000)) || die 'Choose an existing regular login account (UID 1000 or higher).'
[[ "$kiosk_home" == /* && -d "$kiosk_home" ]] || die "The account needs an existing home directory: $kiosk_home"
[[ "$kiosk_shell" != */nologin && "$kiosk_shell" != */false ]] || die 'The kiosk account must have a login shell.'

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ "$source_dir" != *$'\n'* ]] || die 'The checkout path must not contain newlines.'
as_user() {
    if ((EUID == 0)); then
        runuser -u "$kiosk_user" -- "$@"
    else
        "$@"
    fi
}

failures=0
check_fail() { printf 'FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }
note 'Checking this Raspberry Pi and the committed checkout (no changes yet).'
if [[ -r /etc/os-release ]]; then
    # This is the operating system's root-owned identification file.
    . /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in
        raspbian:12|raspbian:13|debian:12|debian:13)
            note "OS: ${PRETTY_NAME:-$ID $VERSION_ID}" ;;
        *) check_fail 'Use Raspberry Pi OS Bookworm (12) or Trixie (13). Older releases need an OS upgrade first.' ;;
    esac
else
    check_fail 'Cannot identify this operating system (/etc/os-release is missing).'
fi
machine="$(uname -m)"
case "$machine" in
    armv7l|armv8l|aarch64) note "CPU: $machine" ;;
    *) check_fail "This installer requires an ARM Raspberry Pi; found $machine." ;;
esac
board=''
for model_path in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
    if [[ -r "$model_path" ]]; then
        board="$(tr -d '\0' <"$model_path")"
        break
    fi
done
if [[ "$board" == *'Raspberry Pi'* ]]; then
    note "Board: $board"
else
    check_fail 'This machine does not identify itself as a Raspberry Pi in its device tree.'
fi
if command -v python3 >/dev/null 2>&1; then
    python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
        check_fail 'Python 3.11 or newer is required; use Bookworm or Trixie.'
else
    check_fail 'Python 3 is missing. Install the OS python3 package first.'
fi
for command_name in git apt-get apt-cache systemctl tar df getent stat flock; do
    command -v "$command_name" >/dev/null 2>&1 || check_fail "Missing system command: $command_name"
done
[[ -d /run/systemd/system ]] || check_fail 'Run this on the booted Pi OS, which must use systemd.'
if ((EUID != 0)) && [[ "$(id -un)" != "$kiosk_user" ]]; then
    check_fail 'For another login account, invoke sudo bash scripts/install-pi.sh --user USER.'
fi

commit=''
if command -v git >/dev/null 2>&1; then
    if commit="$(as_user git -C "$source_dir" rev-parse --verify HEAD 2>/dev/null)"; then
        if [[ -n "$(as_user git --no-optional-locks -C "$source_dir" status --porcelain --untracked-files=no)" ]]; then
            check_fail 'The checkout has tracked changes. Commit or preserve them in another checkout before installation; nothing will be reset.'
        fi
        required_assets=(
            requirements-pi.lock kiosk/__init__.py kiosk/pi_hardware.py
            scripts/install-pi.sh scripts/kiosk-browser.sh scripts/kiosk-session.sh
            scripts/check-pi-runtime.py
            deploy/maintenance-kiosk.desktop
            deploy/systemd/maintenance-kiosk.service
            deploy/systemd/maintenance-kiosk-worker.service
            deploy/systemd/maintenance-kiosk-hardware.service
        )
        for asset in "${required_assets[@]}"; do
            as_user git -C "$source_dir" cat-file -e "$commit:$asset" 2>/dev/null ||
                check_fail "Required file is absent from the selected commit: $asset. Use the latest published kiosk branch."
        done
        note "Release: $commit"
    else
        check_fail 'Run this script from the Git checkout that your login account can read.'
    fi
fi

for managed_dir in /opt/maintenance-kiosk /opt/maintenance-kiosk/releases /var/lib/maintenance-kiosk; do
    if [[ -L "$managed_dir" || ( -e "$managed_dir" && ! -d "$managed_dir" ) ]]; then
        check_fail "Expected a real directory, not a symlink or other file: $managed_dir"
    fi
done
for managed_dir in /opt/maintenance-kiosk /opt/maintenance-kiosk/releases; do
    if [[ -d "$managed_dir" && "$(stat -c %u "$managed_dir")" != 0 ]]; then
        check_fail "Existing application directory must be owned by root: $managed_dir"
    fi
done
if [[ -d /var/lib/maintenance-kiosk && "$(stat -c %u /var/lib/maintenance-kiosk)" != "$kiosk_uid" ]]; then
    check_fail 'Existing kiosk data belongs to another account. Rerun with the original --user; ownership and records will not be changed.'
fi
if [[ -e /opt/maintenance-kiosk/current && ! -L /opt/maintenance-kiosk/current ]]; then
    check_fail '/opt/maintenance-kiosk/current already exists and is not a release symlink.'
fi
disk_path=/opt
[[ -d "$disk_path" ]] || disk_path=/
available_kib="$(df -Pk "$disk_path" | awk 'NR == 2 {print $4}')"
if [[ "$available_kib" =~ ^[0-9]+$ ]] && ((available_kib >= 1572864)); then
    note "Free application disk space: $((available_kib / 1024)) MiB"
else
    check_fail 'At least 1.5 GiB free space is required for the browser and application packages.'
fi
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    lightdm_state="$(systemctl is-enabled lightdm.service 2>/dev/null || true)"
    [[ "$lightdm_state" != masked* ]] || check_fail 'LightDM is administratively masked. Unmask it first if this Pi should start the kiosk display.'
fi
if [[ -L /etc/systemd/system/display-manager.service ]]; then
    display_manager="$(readlink -f /etc/systemd/system/display-manager.service)"
    [[ "$display_manager" == */lightdm.service ]] ||
        check_fail "A different display manager is configured: $display_manager. Switch to LightDM before installing the kiosk."
fi
((failures == 0)) || die "$failures prerequisite check(s) failed. No changes were made."
note "Kiosk login account: $kiosk_user"
if $check_only; then
    note 'Checks passed. This read-only check does not test package downloads or connected hardware.'
    exit 0
fi
if ((EUID != 0)); then
    command -v sudo >/dev/null 2>&1 || die 'sudo is required for package and service installation.'
    exec sudo -- bash "$source_dir/scripts/install-pi.sh" --user "$kiosk_user"
fi

# Do not deploy while the boot updater or another installer owns this lock.
lock_path=/run/lock/maintenance-kiosk-deploy.lock
[[ ! -L "$lock_path" && ( ! -e "$lock_path" || ( -f "$lock_path" && "$(stat -c %u "$lock_path")" == 0 ) ) ]] || die 'The deployment lock must be a regular file owned by root.'
exec 9>>"$lock_path"
flock -n 9 || die 'Another kiosk installation or update is in progress. Try again when it finishes.'

umask 022
lightdm_masked_by_installer=false
temporary_release=''
temporary_file=''
cleanup() {
    local result=$?
    [[ -z "$temporary_file" ]] || rm -f -- "$temporary_file"
    [[ -z "$temporary_release" ]] || rm -rf -- "$temporary_release"
    if $lightdm_masked_by_installer; then
        systemctl unmask --runtime lightdm.service || true
    fi
    if ((result != 0)); then
        printf '\nInstallation stopped. Existing records and previous releases were retained.\n' >&2
        printf 'Resolve the error above and rerun this same command. Service logs:\n' >&2
        printf '  sudo journalctl -u maintenance-kiosk -u maintenance-kiosk-worker -u maintenance-kiosk-hardware -n 80 --no-pager\n' >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

note 'Installing OS packages; downloads can take several minutes on a Pi 3A+.'
# Do not let package post-install scripts start or restart the live display.
systemctl mask --runtime lightdm.service
lightdm_masked_by_installer=true
export DEBIAN_FRONTEND=noninteractive
apt-get update
chromium_package=''
for candidate in chromium chromium-browser; do
    version="$(apt-cache policy "$candidate" | awk '$1 == "Candidate:" {print $2; exit}')"
    if [[ -n "$version" && "$version" != '(none)' ]]; then
        chromium_package="$candidate"
        break
    fi
done
[[ -n "$chromium_package" ]] || die 'No Chromium package is available from the configured OS repositories.'
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-cryptography python3-cffi \
    python3-markupsafe python3-lgpio \
    ca-certificates curl git xserver-xorg xserver-xorg-input-libinput \
    lightdm openbox x11-xserver-utils unclutter fonts-dejavu-core "$chromium_package"
systemctl unmask --runtime lightdm.service
lightdm_masked_by_installer=false

release_dir="/opt/maintenance-kiosk/releases/$commit"
install -d -o root -g root -m 0755 /opt/maintenance-kiosk /opt/maintenance-kiosk/releases
if [[ -e "$release_dir" || -L "$release_dir" ]]; then
    [[ -d "$release_dir" && ! -L "$release_dir" ]] || die "Invalid existing release path: $release_dir"
    [[ "$(stat -c %u "$release_dir")" == 0 ]] || die 'Existing release must be owned by root.'
    [[ -f "$release_dir/.source-commit" && "$(cat "$release_dir/.source-commit")" == "$commit" ]] ||
        die "Existing release has no matching installer marker: $release_dir"
else
    temporary_release="$(mktemp -d "/opt/maintenance-kiosk/releases/.${commit}.XXXXXX")"
    as_user git -C "$source_dir" archive --format=tar "$commit" | tar -x -C "$temporary_release"
    printf '%s\n' "$commit" >"$temporary_release/.source-commit"
    chmod 0755 "$temporary_release"
    mv -- "$temporary_release" "$release_dir"
    temporary_release=''
fi
note 'Installing the pinned Python runtime (OS packages supply compiled dependencies).'
if [[ -L "$release_dir/.venv" ]]; then
    # Boot updates share a validated runtime when dependency files are unchanged.
    # Do not modify that runtime: an older retained release also depends on it.
    runtime_dir="$(readlink -f "$release_dir/.venv")"
    [[ "$runtime_dir" =~ ^/opt/maintenance-kiosk/releases/[0-9a-f]{40}/\.venv$ && -d "$runtime_dir" ]] ||
        die 'The shared Python runtime is outside a managed release.'
    runtime_release="${runtime_dir%/.venv}"
    for runtime_path in "$runtime_release" "$runtime_dir" "$runtime_dir/bin"; do
        [[ -d "$runtime_path" && ! -L "$runtime_path" && "$(stat -c %u "$runtime_path")" == 0 ]] ||
            die "The shared Python runtime must be owned by root: $runtime_path"
        permissions="$(stat -c %a "$runtime_path")"
        (( (8#$permissions & 0022) == 0 )) || die "The shared Python runtime is writable by another account: $runtime_path"
    done
    [[ -f "$runtime_release/.source-commit" && "$(cat "$runtime_release/.source-commit")" == "${runtime_release##*/}" ]] ||
        die 'The shared Python runtime has no matching installer marker.'
    for dependency_file in requirements.txt requirements-pi.lock; do
        cmp -s "$release_dir/$dependency_file" "$runtime_release/$dependency_file" ||
            die "The shared Python runtime has different dependencies: $dependency_file"
    done
    note 'Reusing the validated shared Python runtime; its packages will not be changed.'
else
    python3 -I -m venv --system-site-packages "$release_dir/.venv"
    PIP_CONFIG_FILE=/dev/null PIP_NO_INPUT=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
        "$release_dir/.venv/bin/python" -I -m pip install --no-cache-dir --no-deps \
        --require-hashes --only-binary=:all: -r "$release_dir/requirements-pi.lock"
fi
# Check this app's dependency closure, excluding unrelated desktop Python tools
# inherited from --system-site-packages that a blanket pip check would include.
"$release_dir/.venv/bin/python" -I "$release_dir/scripts/check-pi-runtime.py"
install -d -o "$kiosk_uid" -g "$kiosk_gid" -m 0700 /var/lib/maintenance-kiosk

groups_to_add=()
for group in gpio video render input; do
    if getent group "$group" >/dev/null; then groups_to_add+=("$group"); fi
done
if ((${#groups_to_add[@]})); then
    group_csv="$(IFS=,; printf '%s' "${groups_to_add[*]}")"
    usermod -a -G "$group_csv" "$kiosk_user"
fi

# Replace only files managed by this installer; keep other OS configuration.
install -d -m 0755 /etc/systemd/system /etc/lightdm/lightdm.conf.d /usr/share/xsessions \
    /etc/systemd/system/lightdm.service.d
for unit in maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware; do
    temporary_file="$(mktemp /etc/systemd/system/.maintenance-kiosk.XXXXXX)"
    sed "s/@KIOSK_USER@/$kiosk_user/g" "$release_dir/deploy/systemd/$unit.service" >"$temporary_file"
    chmod 0644 "$temporary_file"
    mv -f -- "$temporary_file" "/etc/systemd/system/$unit.service"
    temporary_file=''
done
install -o root -g root -m 0644 "$release_dir/deploy/maintenance-kiosk.desktop" \
    /usr/share/xsessions/maintenance-kiosk.desktop
temporary_file="$(mktemp /etc/lightdm/lightdm.conf.d/.maintenance-kiosk.XXXXXX)"
cat >"$temporary_file" <<EOF
# Managed by maintenance-kiosk/scripts/install-pi.sh
[Seat:*]
autologin-user=$kiosk_user
autologin-user-timeout=0
user-session=maintenance-kiosk
autologin-session=maintenance-kiosk
EOF
chmod 0644 "$temporary_file"
mv -f -- "$temporary_file" /etc/lightdm/lightdm.conf.d/90-maintenance-kiosk.conf
temporary_file=''
# On existing Pi OS desktops, the main lightdm.conf may override every conf.d
# setting. Select our config explicitly without editing the original OS files.
temporary_file="$(mktemp /etc/systemd/system/lightdm.service.d/.maintenance-kiosk.XXXXXX)"
cat >"$temporary_file" <<'EOF'
# Managed by maintenance-kiosk/scripts/install-pi.sh
[Service]
ExecStart=
ExecStart=/usr/sbin/lightdm --config=/etc/lightdm/lightdm.conf.d/90-maintenance-kiosk.conf
EOF
chmod 0644 "$temporary_file"
mv -f -- "$temporary_file" /etc/systemd/system/lightdm.service.d/90-maintenance-kiosk.conf
temporary_file=''

# Publish a complete release with one atomic rename; retain all older releases.
temporary_file="/opt/maintenance-kiosk/.current.$$"
ln -s -- "$release_dir" "$temporary_file"
mv -Tf -- "$temporary_file" /opt/maintenance-kiosk/current
temporary_file=''
systemctl daemon-reload
systemctl enable maintenance-kiosk.service maintenance-kiosk-worker.service maintenance-kiosk-hardware.service
systemctl enable lightdm.service
systemctl set-default graphical.target
systemctl restart maintenance-kiosk.service maintenance-kiosk-worker.service maintenance-kiosk-hardware.service

note 'Checking the local web service (up to 60 seconds).'
healthy=false
for ((attempt = 0; attempt < 20; attempt++)); do
    if curl --silent --fail --max-time 1 --noproxy '*' http://127.0.0.1:5000/api/health >/dev/null; then
        healthy=true
        break
    fi
    sleep 2
done
$healthy || die 'The local web service did not become healthy. Inspect the service logs shown below.'
for unit in maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware; do
    systemctl is-active --quiet "$unit.service" || die "$unit.service is not running. Inspect its journal."
done
cat <<EOF

Installed successfully. The web app is at http://127.0.0.1:5000.
Records and settings stay in /var/lib/maintenance-kiosk across updates.

When ready, run: sudo reboot
The next boot automatically signs in $kiosk_user and opens the kiosk touchscreen.
The current display session was not restarted by this installer.
Use Settings to connect Google Sheets, Discord and email, then Settings > Testing.
GPIO stays in its existing configured mode; a new installation starts in mock mode.
EOF
