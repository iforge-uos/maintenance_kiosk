#!/usr/bin/env bash
# Enable an update check before the installed kiosk starts at each boot.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/enable-pi-boot-update.sh [--branch BRANCH] [--user USER]

Enable a GitHub update check at each boot of an installed Raspberry Pi kiosk.
The branch defaults to the branch checked out here. The checkout must be clean.
Run scripts/install-pi.sh first. This command does not restart the kiosk.

  --branch BRANCH  Follow this branch; it must already be checked out here.
  --user USER      Existing kiosk account (defaults to you / SUDO_USER).
  --help          Show this help without changing the machine.

The next boot checks for a fast-forward update before the kiosk starts.
No packages are installed at boot. If a check fails, the installed release stays.
Records and settings remain in /var/lib/maintenance-kiosk.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
kiosk_user="${SUDO_USER:-$(id -un)}"
branch=''
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --branch)
            (($# >= 2)) || die '--branch needs a branch name.'
            branch="$2"; shift 2 ;;
        --user)
            (($# >= 2)) || die '--user needs an existing kiosk username.'
            kiosk_user="$2"; shift 2 ;;
        *) die "Unknown option: $1 (use --help)." ;;
    esac
done
[[ "$kiosk_user" =~ ^[a-z_][a-z0-9_-]*\$?$ && "$kiosk_user" != root ]] ||
    die 'Run as your normal Pi user, or use --user EXISTING_USER.'
for command_name in git python3 systemctl getent stat install flock; do
    command -v "$command_name" >/dev/null 2>&1 || die "Missing system command: $command_name"
done
account="$(getent passwd "$kiosk_user")" || die "No existing account named $kiosk_user."
IFS=: read -r _ _ kiosk_uid _ _ kiosk_home kiosk_shell <<<"$account"
((kiosk_uid >= 1000)) || die 'Choose the regular login account used by the installed kiosk.'
[[ "$kiosk_home" == /* && -d "$kiosk_home" ]] || die 'The kiosk account must have an existing home directory.'
[[ "$kiosk_shell" != */nologin && "$kiosk_shell" != */false ]] || die 'The kiosk account must have a login shell.'
if ((EUID != 0)); then
    [[ "$(id -un)" == "$kiosk_user" ]] || die 'For another account, use sudo with --user USER.'
fi
as_user() {
    if ((EUID == 0)); then
        runuser -u "$kiosk_user" -- "$@"
    else
        "$@"
    fi
}

note 'Checking the Pi, installed kiosk and committed checkout (no changes yet).'
[[ -r /etc/os-release ]] || die 'Cannot identify this operating system.'
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    raspbian:12|raspbian:13|debian:12|debian:13) ;;
    *) die 'Raspberry Pi OS Bookworm (12) or Trixie (13) is required.' ;;
esac
case "$(uname -m)" in
    armv7l|armv8l|aarch64) ;;
    *) die 'Run this command on the Raspberry Pi.' ;;
esac
board=''
for model_path in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
    if [[ -r "$model_path" ]]; then board="$(tr -d '\0' <"$model_path")"; break; fi
done
[[ "$board" == *'Raspberry Pi'* ]] || die 'This machine does not identify itself as a Raspberry Pi.'
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die 'Python 3.11 or newer is required.'
[[ -d /run/systemd/system ]] || die 'Run this on the booted Pi OS with systemd.'

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ "$source_dir" != *$'\n'* ]] || die 'The checkout path must not contain newlines.'
[[ "$(as_user git -C "$source_dir" rev-parse --show-toplevel)" == "$source_dir" ]] || die 'Run this script from the kiosk Git checkout.'
current_branch="$(as_user git -C "$source_dir" symbolic-ref --quiet --short HEAD)" || die 'Check out the branch to follow; detached HEAD is not supported.'
[[ -n "$branch" ]] || branch="$current_branch"
[[ "$branch" != -* && "$branch" != *$'\n'* ]] || die 'Invalid branch name.'
as_user git check-ref-format "refs/heads/$branch" >/dev/null || die 'Invalid branch name.'
[[ "$current_branch" == "$branch" ]] || die "The checkout is on $current_branch. Check out $branch first."
[[ -z "$(as_user git --no-optional-locks -C "$source_dir" status --porcelain --untracked-files=no)" ]] ||
    die 'The checkout has tracked changes. Commit or preserve them before enabling updates; nothing will be reset.'
commit="$(as_user git -C "$source_dir" rev-parse --verify HEAD)"
for asset in scripts/update-pi.py deploy/systemd/maintenance-kiosk-update.service; do
    as_user git -C "$source_dir" cat-file -e "$commit:$asset" 2>/dev/null ||
        die "Required file is absent from the selected commit: $asset. Pull the published branch first."
done

for managed_dir in /opt/maintenance-kiosk /opt/maintenance-kiosk/releases; do
    [[ -d "$managed_dir" && ! -L "$managed_dir" && "$(stat -c %u "$managed_dir")" == 0 ]] ||
        die "Expected a root-owned installed kiosk directory: $managed_dir. Run scripts/install-pi.sh first."
done
[[ -L /opt/maintenance-kiosk/current ]] || die 'No installed release was found. Run scripts/install-pi.sh first.'
release_dir="$(readlink -f /opt/maintenance-kiosk/current)"
[[ "$release_dir" =~ ^/opt/maintenance-kiosk/releases/[0-9a-f]{40}$ && -d "$release_dir" ]] || die 'The current release link is invalid.'
[[ "$(stat -c %u "$release_dir")" == 0 && -f "$release_dir/.source-commit" ]] || die 'The current release has no valid installer marker.'
[[ "$(cat "$release_dir/.source-commit")" == "${release_dir##*/}" ]] || die 'The installed release marker does not match its directory.'
[[ -d /var/lib/maintenance-kiosk && ! -L /var/lib/maintenance-kiosk && "$(stat -c %u /var/lib/maintenance-kiosk)" == "$kiosk_uid" ]] ||
    die 'Existing kiosk data must belong to the selected account. Use the original kiosk user.'
for unit in maintenance-kiosk maintenance-kiosk-worker maintenance-kiosk-hardware; do
    [[ "$(systemctl show --property=LoadState --value "$unit.service")" == loaded ]] || die "The kiosk service is not installed: $unit."
    [[ "$(systemctl show --property=User --value "$unit.service")" == "$kiosk_user" ]] || die "The service $unit belongs to another account."
done
[[ "$(systemctl show --property=LoadState --value lightdm.service)" == loaded ]] || die 'The kiosk display service is not installed.'

if ((EUID != 0)); then
    command -v sudo >/dev/null 2>&1 || die 'sudo is required to install the startup service.'
    exec sudo -- bash "$source_dir/scripts/enable-pi-boot-update.sh" --branch "$branch" --user "$kiosk_user"
fi

# The manual installer and boot updater use this same lock.
lock_path=/run/lock/maintenance-kiosk-deploy.lock
[[ ! -L "$lock_path" && ( ! -e "$lock_path" || ( -f "$lock_path" && "$(stat -c %u "$lock_path")" == 0 ) ) ]] || die 'The deployment lock must be a regular file owned by root.'
exec 9>>"$lock_path"
flock -n 9 || die 'Another kiosk installation or update is in progress. Try again when it finishes.'
umask 077
config_dir=/etc/maintenance-kiosk
helper_dir=/usr/local/lib/maintenance-kiosk
config_path="$config_dir/boot-update.json"
helper_path="$helper_dir/update-pi.py"
unit_path=/etc/systemd/system/maintenance-kiosk-update.service
for managed_dir in "$config_dir" "$helper_dir"; do
    if [[ -e "$managed_dir" || -L "$managed_dir" ]]; then
        [[ -d "$managed_dir" && ! -L "$managed_dir" && "$(stat -c %u "$managed_dir")" == 0 ]] || die "Invalid managed directory: $managed_dir"
        permissions="$(stat -c %a "$managed_dir")"
        (( (8#$permissions & 0022) == 0 )) || die "Managed directory is writable by another account: $managed_dir"
    fi
done
for managed_file in "$config_path" "$helper_path" "$unit_path"; do
    if [[ -e "$managed_file" || -L "$managed_file" ]]; then
        [[ -f "$managed_file" && ! -L "$managed_file" && "$(stat -c %u "$managed_file")" == 0 ]] || die "Invalid managed file: $managed_file"
        permissions="$(stat -c %a "$managed_file")"
        (( (8#$permissions & 0022) == 0 )) || die "Managed file is writable by another account: $managed_file"
    fi
done
previous_enable_state="$(systemctl is-enabled maintenance-kiosk-update.service 2>/dev/null || true)"
[[ "$previous_enable_state" != masked* ]] || die 'The boot update service is masked. Unmask it first if automatic updates are wanted.'
stage_dir="$(mktemp -d /tmp/maintenance-kiosk-enable.XXXXXX)"
changed=false
completed=false
enable_attempted=false
cleanup() {
    local result=$?
    if $changed && ! $completed; then
        for name in config helper unit; do
            local variable="${name}_path"
            local destination="${!variable}"
            if [[ -f "$stage_dir/old-$name" ]]; then
                cp -p -- "$stage_dir/old-$name" "$destination" || true
            else
                rm -f -- "$destination" || true
            fi
        done
        if $enable_attempted && [[ "$previous_enable_state" != enabled* ]]; then
            systemctl disable maintenance-kiosk-update.service >/dev/null 2>&1 || true
        fi
        systemctl daemon-reload || true
        printf 'Boot update setup stopped; the previous managed files were restored.\n' >&2
    fi
    rm -rf -- "$stage_dir"
    return "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for name in config helper unit; do
    variable="${name}_path"
    [[ ! -f "${!variable}" ]] || cp -p -- "${!variable}" "$stage_dir/old-$name"
done
as_user git -C "$source_dir" show "$commit:scripts/update-pi.py" >"$stage_dir/helper"
as_user git -C "$source_dir" show "$commit:deploy/systemd/maintenance-kiosk-update.service" >"$stage_dir/unit"
python3 -I - "$source_dir" "$kiosk_user" "$branch" >"$stage_dir/config" <<'PY'
import json
import sys
json.dump(dict(zip(("checkout", "user", "branch"), sys.argv[1:])), sys.stdout)
sys.stdout.write("\n")
PY
install -d -o root -g root -m 0700 "$config_dir"
install -d -o root -g root -m 0755 "$helper_dir"
changed=true
install -o root -g root -m 0600 "$stage_dir/config" "$config_path"
install -o root -g root -m 0644 "$stage_dir/helper" "$helper_path"
install -o root -g root -m 0644 "$stage_dir/unit" "$unit_path"
python3 -I "$helper_path" --check
systemctl daemon-reload
enable_attempted=true
systemctl enable maintenance-kiosk-update.service
completed=true
note "Boot updates are enabled for $branch. The current kiosk session was not restarted."
note 'The next boot will check GitHub before the kiosk starts.'
note 'Check the result after boot: sudo journalctl -u maintenance-kiosk-update -b --no-pager'
note 'Disable future checks: sudo systemctl disable maintenance-kiosk-update.service'
