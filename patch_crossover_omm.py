#!/usr/bin/env python3
"""Enable Logitech OMM USB access in a compatible installed CrossOver copy.

This patcher contains no CrossOver or Logitech software. It only changes a
recognizable USB filter in a locally installed CrossOver library and the
existing OMM shortcut. Tested with CrossOver 26.3; other builds are scanned.
"""

import argparse
import hashlib
import json
import os
import plistlib
import pwd
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


TESTED_ORIGINAL_SHA256 = "8e8097ba17a62e76895042f2fc4a8e6a4a5a6d0b6ea51e5c3506626c4a2450eb"
TESTED_PATCHED_SHA256 = "37e56c277e4df2840a4c58a1bae3d2d24ac95566753d247f9244a0d52efc3e22"
ORIGINAL_BYTES = bytes.fromhex("41 83 fe 01 75 43 41 81 e4 fe ff 00 00 41 83 fc 04 75 36")
PATCHED_BYTES = bytes.fromhex("81 7d cc 6d 04 00 00 75 40 41 f6 c6 ff 75 3a 90 90 90 90")
LIBRARY_RELATIVE = Path("Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winebus.so")
WINE_RELATIVE = Path("Contents/SharedSupport/CrossOver/bin/wine")
SHORTCUT_NAME = "OnboardMemoryManager_2.6.1749.lnk"

def logged_in_home() -> Path:
    """Use the real user's home if the script was started with sudo."""
    user = os.environ.get("SUDO_USER") if os.geteuid() == 0 else None
    return Path(pwd.getpwnam(user).pw_dir) if user else Path.home()


USER_HOME = logged_in_home()
BACKUP_DIR = USER_HOME / "Library/Application Support/CrossOver OMM Patcher"


def preserve_owner(path: Path, reference: Path) -> None:
    if os.geteuid() == 0:
        stat = reference.stat()
        os.chown(path, stat.st_uid, stat.st_gid)


def own_backup(path: Path) -> None:
    user = os.environ.get("SUDO_USER") if os.geteuid() == 0 else None
    if user:
        account = pwd.getpwnam(user)
        os.chown(path, account.pw_uid, account.pw_gid)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_section(data: bytes) -> tuple:
    """Return the executable __text range of a thin x86_64 Mach-O library."""
    if data[:4] != b"\xcf\xfa\xed\xfe" or len(data) < 32:
        raise RuntimeError("Expected a thin x86_64 Mach-O winebus.so; unsupported binary layout")
    if struct.unpack_from("<I", data, 4)[0] != 0x01000007:
        raise RuntimeError("This patch only supports the x86_64 Wine USB bridge")
    command_count = struct.unpack_from("<I", data, 16)[0]
    position = 32
    for _ in range(command_count):
        if position + 8 > len(data):
            break
        command, command_size = struct.unpack_from("<II", data, position)
        if command_size < 8 or position + command_size > len(data):
            break
        if command == 0x19 and command_size >= 72:  # LC_SEGMENT_64
            section_count = struct.unpack_from("<I", data, position + 64)[0]
            if 72 + section_count * 80 > command_size:
                raise RuntimeError("Malformed Mach-O section table")
            for section_index in range(section_count):
                section = position + 72 + section_index * 80
                name = data[section : section + 16].split(b"\0", 1)[0]
                segment = data[section + 16 : section + 32].split(b"\0", 1)[0]
                if name == b"__text" and segment == b"__TEXT":
                    size = struct.unpack_from("<Q", data, section + 40)[0]
                    offset = struct.unpack_from("<I", data, section + 48)[0]
                    if offset + size > len(data):
                        raise RuntimeError("Mach-O text section extends past the file")
                    return offset, offset + size
        position += command_size
    raise RuntimeError("Could not find the executable Mach-O text section")


def filter_position(data: bytes, pattern: bytes) -> Optional[int]:
    """Find one filter with the tested register and vendor-ID stack layout."""
    start, end = text_section(data)
    positions = []
    cursor = start
    while True:
        position = data.find(pattern, cursor, end)
        if position < 0:
            break
        positions.append(position)
        cursor = position + 1
    if len(positions) > 1:
        raise RuntimeError("Found multiple possible USB filters; no changes made")
    if not positions:
        return None
    position = positions[0]
    vendor_store = b"\x89\x4d\xcc"       # mov ecx,-0x34(rbp)
    vendor_load = b"\x44\x8b\x4d\xcc"    # mov -0x34(rbp),r9d
    open_device = b"\x48\x89\xdf\x31\xf6\xe8"
    if (
        data.rfind(vendor_store, max(start, position - 0x180), position) < 0
        or data.find(vendor_load, position + len(pattern), min(end, position + 0x100)) < 0
        or data[position + len(pattern) : position + len(pattern) + len(open_device)] != open_device
    ):
        raise RuntimeError("USB filter found, but its surrounding code differs from the tested layout")
    return position


def version_of(app: Path) -> str:
    with (app / "Contents/Info.plist").open("rb") as f:
        info = plistlib.load(f)
    if info.get("CFBundleIdentifier") != "com.codeweavers.CrossOver":
        raise RuntimeError(f"Not a CrossOver app: {app}")
    return str(info.get("CFBundleShortVersionString", "unknown"))


def backup_for(original_sha: str) -> Path:
    return BACKUP_DIR / f"winebus.{original_sha}.original"


def record_for(patched_sha: str) -> Path:
    return BACKUP_DIR / f"patch.{patched_sha}.json"


def patch_record(patched_sha: str) -> Optional[dict]:
    record_path = record_for(patched_sha)
    if not record_path.exists():
        return None
    record = json.loads(record_path.read_text())
    if record.get("patched_sha256") != patched_sha:
        raise RuntimeError(f"Invalid patch record: {record_path}")
    return record


def restorable_backup(patched_sha: str) -> Optional[Path]:
    record = patch_record(patched_sha)
    if record is not None:
        original_sha = record.get("original_sha256", "")
        backup = backup_for(original_sha)
        if backup.exists() and digest(backup) == original_sha:
            return backup
        raise RuntimeError(f"Original USB library backup is missing or invalid: {backup}")
    if patched_sha == TESTED_PATCHED_SHA256:
        legacy = BACKUP_DIR / "winebus.so.original"
        if legacy.exists() and digest(legacy) == TESTED_ORIGINAL_SHA256:
            return legacy
    return None


def filter_state(data: bytes) -> tuple:
    original_position = filter_position(data, ORIGINAL_BYTES)
    patched_position = filter_position(data, PATCHED_BYTES)
    if original_position is not None and patched_position is not None:
        raise RuntimeError("Both original and patched filters are present; no changes made")
    return original_position, patched_position


def replace_file(source: Path, destination: Path) -> None:
    """Replace one file atomically, retaining the source file's mode."""
    fd, temp_name = tempfile.mkstemp(prefix=".omm-patch-", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out)
        os.chmod(temp, source.stat().st_mode)
        preserve_owner(temp, destination)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def find_shortcut(bottle: Path) -> Optional[Path]:
    matches = list((bottle / "desktopdata/cxmenu").glob(f"StartMenu.*/{SHORTCUT_NAME}"))
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one OMM shortcut in {bottle}; found {len(matches)}")
    return matches[0] if matches else None


def new_shortcut(app: Path, bottle: Path) -> bytes:
    wine = app / WINE_RELATIVE
    target = f"C:/users/crossover/AppData/Roaming/Microsoft/Windows/Start Menu/{SHORTCUT_NAME}"
    return (
        "#!/bin/sh\n"
        f'exec "{wine}" --bottle "{bottle.name}" --check --wait-children --start "{target}" "$@"\n'
    ).encode()


def apply(app: Path, bottle: Path) -> None:
    version = version_of(app)
    library = app / LIBRARY_RELATIVE
    shortcut = find_shortcut(bottle)
    data = library.read_bytes()
    current = hashlib.sha256(data).hexdigest()
    original_position, patched_position = filter_state(data)
    if patched_position is not None:
        if restorable_backup(current) is None:
            raise RuntimeError("USB filter appears patched, but its original backup is missing; no changes made")
        print("CrossOver USB patch is already installed.")
    elif original_position is not None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        own_backup(BACKUP_DIR)
        original_backup = backup_for(current)
        if original_backup.exists() and digest(original_backup) != current:
            raise RuntimeError(f"Existing backup is not the expected original: {original_backup}")
        if not original_backup.exists():
            shutil.copy2(library, original_backup)
            own_backup(original_backup)
        modified = data[:original_position] + PATCHED_BYTES + data[original_position + len(ORIGINAL_BYTES) :]
        temp_dir = Path(tempfile.mkdtemp(prefix=".omm-winebus-", dir=library.parent))
        temp = temp_dir / "winebus.so"
        try:
            temp.write_bytes(modified)
            os.chmod(temp, library.stat().st_mode)
            subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(temp)], check=True)
            subprocess.run(["codesign", "--verify", str(temp)], check=True)
            signed_data = temp.read_bytes()
            if filter_position(signed_data, PATCHED_BYTES) != original_position:
                raise RuntimeError("Signed patch did not retain the expected USB filter layout")
            patched_sha = hashlib.sha256(signed_data).hexdigest()
            record = {
                "version": version,
                "original_sha256": current,
                "patched_sha256": patched_sha,
                "offset": original_position,
            }
            record_path = record_for(patched_sha)
            if record_path.exists():
                if patch_record(patched_sha) != record:
                    raise RuntimeError(f"Existing patch record differs: {record_path}")
            else:
                record_path.write_text(json.dumps(record, indent=2) + "\n")
                own_backup(record_path)
            preserve_owner(temp, library)
            os.replace(temp, library)
        finally:
            temp.unlink(missing_ok=True)
            temp_dir.rmdir()
        print(f"Patched the installed CrossOver {version} USB bridge at offset 0x{original_position:x}.")
    else:
        raise RuntimeError(
            f"CrossOver {version} does not contain the tested USB filter layout; no changes made."
        )

    if shortcut is None:
        print(f"No OMM 2.6.1749 shortcut found in {bottle}; USB bridge is ready. Install OMM, then rerun apply.")
        return
    before = shortcut.read_bytes()
    after = new_shortcut(app, bottle)
    if before != after:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        own_backup(BACKUP_DIR)
        shortcut_backup = BACKUP_DIR / "OMM shortcut.before-patch"
        if not shortcut_backup.exists():
            shortcut_backup.write_bytes(before)
            own_backup(shortcut_backup)
        fd, temp_name = tempfile.mkstemp(prefix=".omm-shortcut-", dir=shortcut.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(after)
            os.chmod(temp, 0o755)
            preserve_owner(temp, shortcut)
            os.replace(temp, shortcut)
        finally:
            temp.unlink(missing_ok=True)
        print("Pointed the OMM bottle shortcut to the regular CrossOver app.")
    else:
        print("OMM bottle shortcut already points to the regular CrossOver app.")
    print("Done. Restart CrossOver before opening OMM from its bottle.")


def restore(app: Path, bottle: Path) -> None:
    version_of(app)
    library = app / LIBRARY_RELATIVE
    data = library.read_bytes()
    current = hashlib.sha256(data).hexdigest()
    original_position, patched_position = filter_state(data)
    if patched_position is not None:
        original_backup = restorable_backup(current)
        if original_backup is None:
            raise RuntimeError("No matching original USB library backup; no changes made")
        replace_file(original_backup, library)
        print("Restored the original CrossOver USB bridge.")
    elif original_position is None:
        raise RuntimeError("Installed CrossOver USB filter layout is unrecognized; no changes made")
    shortcut_backup = BACKUP_DIR / "OMM shortcut.before-patch"
    if shortcut_backup.exists():
        shortcut = find_shortcut(bottle)
        if shortcut is not None:
            replace_file(shortcut_backup, shortcut)
            print("Restored the previous OMM bottle shortcut.")
    print("Done. Restart CrossOver.")


def status(app: Path, bottle: Path) -> None:
    version = version_of(app)
    data = (app / LIBRARY_RELATIVE).read_bytes()
    current = hashlib.sha256(data).hexdigest()
    original_position, patched_position = filter_state(data)
    tested = " (tested)" if version == "26.3" and current in (TESTED_ORIGINAL_SHA256, TESTED_PATCHED_SHA256) else " (not tested)"
    print("CrossOver:", version + tested)
    print("USB bridge:", "patched" if patched_position is not None else "ready to patch" if original_position is not None else "unsupported filter layout")
    shortcut = find_shortcut(bottle)
    print("OMM shortcut:", "not found" if shortcut is None else "regular CrossOver" if shortcut.read_bytes() == new_shortcut(app, bottle) else "other launch path")
    print("Original backup available:", restorable_backup(current) is not None if patched_position is not None else backup_for(current).exists())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "apply", "restore"), help="Check, install, or undo the patch")
    parser.add_argument("--app", type=Path, default=Path("/Applications/CrossOver.app"), help="Installed CrossOver app")
    parser.add_argument(
        "--bottle", type=Path,
        default=USER_HOME / "Library/Application Support/CrossOver/Bottles/OMM",
        help="Existing OMM bottle",
    )
    args = parser.parse_args()
    try:
        {"status": status, "apply": apply, "restore": restore}[args.action](args.app.resolve(), args.bottle.resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
