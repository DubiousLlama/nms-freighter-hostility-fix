#!/usr/bin/env python3
"""
Print the freighter-hostility related values from raw No Man's Sky GLOBALS .MBIN files
without needing MBINCompiler or .NET.

Usage:
    python3 tools/read_globals.py path/to/GCAISPACESHIPGLOBALS.GLOBAL.MBIN [more .MBIN files]

Where to get the .MBIN files: they live inside PCBANKS/NMSARC.*.pak. Any pak extractor works
(AMUMSS unpacks them into TOOLS/UNPACKED_DECOMPILED_PAKs/, NMSPE or PSArcTool also work).

How it works: a .MBIN is a 0x20-byte header followed by the flat C++ struct. Field offsets
below are copied from libMBIN 7.02.0.1 (commit 9375313, 2026-09-11):
  libMBIN/Source/NMS/Globals/GcAISpaceshipGlobals.cs
  libMBIN/Source/NMS/Globals/GcSpaceshipGlobals.cs
  libMBIN/Source/NMS/Globals/GcGameplayGlobals.cs
Hello Games reshuffles these structs in most updates, so if the sanity checks below fail,
refresh the offsets from the current libMBIN source before trusting the output.
"""
import struct
import sys

HEADER_SIZE = 0x20
MBIN_MAGIC = 0xCCCCCCCC

FIELDS = {
    "GCAISPACESHIPGLOBALS": [
        # (name, offset, struct format)
        ("FreighterAlertThreshold",          0x9C4, "<f"),
        ("FreighterAlertTimeOutMinTime",     0x9C8, "<f"),
        ("FreighterAlertTimeOutRate",        0x9CC, "<f"),
        ("FreighterAttackAlertThreshold",    0x9D0, "<f"),
        ("FreighterAttackDisengageDistance", 0x9D4, "<f"),   # community-reported default 3000
        ("FreighterRegisterHitCooldown",     0x9E8, "<f"),
        ("MinAggroDamage",                   0xA60, "<i"),
        ("FreighterIgnorePlayer",            0xEED, "<?"),
        ("FreightersAlwaysAttackPlayer",     0xEEE, "<?"),
    ],
    "GCSPACESHIPGLOBALS": [
        ("FreighterBattleIgnoreFriendlyFireDistance", 0x14C4, "<f"),  # community-reported default 7200 / 10000
        ("FreighterBattleRangeBoost",                 0x14C8, "<f"),
    ],
    "GCGAMEPLAYGLOBALS": [
        ("FreighterBattleRadius",           0x15E0, "<f"),   # community-reported default 5000
        ("FreighterCargoPodHealthFraction", 0x15E4, "<f"),   # community-reported default 0.8
        ("FreighterFuelRodHealthFraction",  0x15E8, "<f"),
    ],
}

SANITY = {
    # value that, if seen, strongly suggests the offsets still line up
    "GCAISPACESHIPGLOBALS": ("FreighterAttackDisengageDistance", 3000.0),
    "GCGAMEPLAYGLOBALS":    ("FreighterBattleRadius", 5000.0),
}


def read(path):
    data = open(path, "rb").read()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MBIN_MAGIC:
        sys.exit(f"{path}: not an MBIN (magic 0x{magic:08X})")
    name = path.replace("\\", "/").split("/")[-1].upper()
    key = next((k for k in FIELDS if name.startswith(k)), None)
    if key is None:
        sys.exit(f"{path}: no field table for this file (known: {', '.join(FIELDS)})")
    print(f"== {name}  ({len(data)} bytes)")
    values = {}
    for fname, off, fmt in FIELDS[key]:
        abs_off = HEADER_SIZE + off
        if abs_off + struct.calcsize(fmt) > len(data):
            print(f"  {fname:<42} <beyond end of file: struct layout has changed>")
            continue
        (val,) = struct.unpack_from(fmt, data, abs_off)
        values[fname] = val
        print(f"  {fname:<42} = {val!r}   @0x{abs_off:X}")
    if key in SANITY:
        fname, expected = SANITY[key]
        got = values.get(fname)
        ok = got is not None and abs(got - expected) < 1e-3
        print(f"  sanity: {fname} == {expected} -> {'OK' if ok else 'MISMATCH (offsets may be stale)'}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        read(p)
