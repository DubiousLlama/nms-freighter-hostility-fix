# Freighter Class Peek (code plugin)

Shows an NPC freighter's class (C/B/A/S) the moment the game rolls it, without landing. This is
a **code-level mod** built on [pyMHF](https://github.com/monkeyman192/pyMHF) and
[NMS.py](https://github.com/monkeyman192/NMS.py): a Python process attaches to `NMS.exe`, hooks
a few functions, and shows results in its own always-on-top window (and optionally as an in-game
HUD message). It is separate from the freighter-hostility data mod in this repo and can be run
with or without it.

Status: **written but not run against the game** (no game binary is available in the environment
this was authored in). Layer 1 relies only on hooks NMS.py already ships for the current build
(`cGcInventoryStore::Add` and the `mClass` field), so it should load as-is; it needs a test pass.
Layer 2 needs two byte patterns you must extract from your `NMS.exe`.

## Requirements

* Windows, Steam or GOG `NMS.exe` 7.x. NMS.py `170671.6` targets the Cosmos-era build.
* Python 3.10 to 3.13 from python.org (not the Store build). NMS.py notes 3.14 is not yet supported.
* `pip install "pymhf[gui]" nmspy`

## Run

```
pymhf run path\to\plugin\freighter_class_peek.py
```

pyMHF launches the game (or attaches, per its config), opens a GUI window with a tab for this mod
and a log window. Alternatively copy the file into `GAMEDATA\MODS\` and run `pymhf run nmspy`,
which loads every mod in that folder.

## What you see

The mod tab shows:

* **Last freighter-sized inventory**: e.g. `class A (8x6, 48 slots, heuristic)`.
* **Recent NPC inventories**: the last dozen non-player inventories the game populated, newest
  first, each with timestamp, class, dimensions, slot count, attribution source, and the address
  of the code that inserted the items.
* Toggles for calibration logging and the experimental in-game announcement, a slot threshold,
  and buttons to re-announce or dump caller statistics. Key `k` re-announces the last result.

The log window mirrors the same lines.

## How it works, and how to calibrate it

Mechanism (from NMS.py's field map and the 4.13 symbol catalogue in its git history):

* Every generated inventory is a `cGcInventoryStore`; its `mClass` field is the rolled class.
* An NPC ship's purchasable inventory is built by `cGcAISpaceshipComponent::GenerateInventory`,
  which rolls the class via `cGcInventoryStore::GenerateProceduralClass` and inserts items via
  `cGcInventoryStore::Add`.
* NMS.py ships a working current-build pattern for `Add` and the offset of `mClass`, but not for
  the two generation functions.

**Layer 1 (default)** hooks `Add`, drops calls whose store lies inside the player's own inventory
arrays, waits a quarter second for the generator to finish, then reads the store's class, size
and name. Stores with at least the threshold number of slots are reported as freighter-sized.

Calibration, once:

1. Leave "Log every caller address" on. Warp into a system where a freighter battle triggers.
2. Watch the log at the moment the capital freighter warps in. One or two lines with a large
   slot count will appear, all sharing a caller address like `NMS+0x11F7A31`.
3. Put that address (or addresses) into `KNOWN_GENERATION_CALLERS` at the top of the file and
   reload the mod from the GUI. Matching events are then tagged `caller-match` instead of
   `heuristic`.

This also answers the open question of *when* the class is rolled. If lines appear at warp-in,
the peek works as intended. If they only appear when you enter the hangar, the game rolls the
class on landing and no display trick can beat that; the mod then only saves you the walk to
the captain.

**Layer 2 (optional)** makes attribution exact. Fill `SIG_GENERATE_INVENTORY` and
`SIG_GENERATE_PROCEDURAL_CLASS` with byte patterns for your `NMS.exe` and reload. Events inside
`GenerateInventory` are tagged `GenerateInventory`, and every class roll is logged directly.

Finding the two patterns requires a disassembler. The approach NMS.py's author documents:

* Open `NMS.exe` in IDA or Ghidra. Open the macOS build of the game (it keeps symbol names) or
  the 4.13 PC build for reference; the 4.13 offsets of both functions are in the NMS.py history
  (`cGcAISpaceshipComponent::GenerateInventory` at `0x11F7610`,
  `cGcInventoryStore::GenerateProceduralClass` at `0x3334B0`).
* Match the functions by structure. `GenerateProceduralClass` is small: it takes the store and
  a seed, reads the wealth-indexed class probability table from the loaded `INVENTORYTABLE`
  data (`ClassProbabilityData`, 4 rows of 4 floats), draws a random number, and writes the
  result to the store at offset `0x100`. A write of a 32-bit value to `[rcx+100h]` (or the
  register holding `this`) near the end of a short function that reads a 4x4 float table is the
  signature to look for. `GenerateInventory` is its only caller during NPC ship generation.
* Produce a unique byte pattern for the start of each function with IDA Fusion or SigmakerEX
  (as NMS.py's README recommends) and paste it into the config strings.

If you get the patterns, contributing them upstream to NMS.py (`tools/data.json` and
`nmspy/data/types.py`) helps everyone.

## Caveats

* Game updates change patterns and offsets. When NMS.py updates, this mod usually follows; when
  NMS.py is behind the game, wait.
* The in-game announcement calls `cGcPlayerNotifications::AddTimedMessage`, whose argument list
  NMS.py marks as "changed since 4.13, might need to confirm". It is off by default and wrapped
  in error handling, but a wrong call can still crash the game. Test on a save you can lose.
* Layer 1 cannot tell an NPC freighter from other large NPC inventories on its own; the timing
  and slot count are the tell, and calibration or Layer 2 removes the ambiguity.
* NMS.py deliberately excludes online functionality. Using it in multiplayer sessions is at
  your own discretion; this mod only reads memory and shows text.
