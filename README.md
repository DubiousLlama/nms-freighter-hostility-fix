# Freighter Friendly-Fire Tolerance (No Man's Sky 7.x "Cosmos")

Stops a civilian capital freighter from turning hostile (and locking its hangar) because a few
stray shots hit it while you were defending it from pirates.

The game keeps an "alert" counter per freighter. Every registered hit on its cargo pods or
turrets adds to it; when it crosses `FreighterAttackAlertThreshold` the freighter shoots at you
and closes its doors. The default (variant A) raises that threshold so far that no amount of
glancing fire can reach it. Three alternative variants are included because the exact
mechanism has not been confirmed in-game (see [docs/RESEARCH.md](docs/RESEARCH.md)).

## Install (default variant A)

1. Copy the `FreighterFriendlyFireTolerance` folder from `GAMEDATA/MODS/` in this repo into
   `<No Man's Sky>\GAMEDATA\MODS\` so you end up with

   ```
   No Man's Sky\GAMEDATA\MODS\FreighterFriendlyFireTolerance\GLOBALS\GCAISPACESHIPGLOBALS.GLOBAL.EXML
   ```

2. Make sure `GAMEDATA\PCBANKS\DISABLEMODS.TXT` does not exist (delete it if it does).
3. Launch the game. The mod shows up in the in-game mod list (`GCMODSETTINGS.MXML` is
   generated automatically); leave it enabled.

No `.pak`, no MBINCompiler, no AMUMSS needed. The `.EXML` is a partial patch: it overrides one
property and leaves the rest of the file at the game's values, so it survives patches unless
Hello Games renames the field.

To uninstall, delete the folder.

## Variants

| Folder | What it changes | When to use it |
|---|---|---|
| `GAMEDATA/MODS/FreighterFriendlyFireTolerance` (**A**, default) | `FreighterAttackAlertThreshold` → 1e9 | First thing to try. Hits can never escalate to hostility. |
| `variants/B-AutoForgive` | `FreighterAlertTimeOutMinTime` → 3 s, `FreighterAlertTimeOutRate` → 1e6 | Keeps hostility for a sustained deliberate attack, but the freighter forgives you a few seconds after the last hit, so the hangar is open by the time the captain hails you. Closest data-only version of "clear hostility at the reward call". Can be installed alongside A. |
| `variants/C-IgnoreBattleFriendlyFire` | `FreighterBattleIgnoreFriendlyFireDistance` → 1e6 (in `GCSPACESHIPGLOBALS`) | Low expectations. Research (see docs/RESEARCH.md) indicates this is the radius inside which your hits on the *escort trader ships* are excused as crimes during a battle, not a freighter-hostility knob. Kept as a cheap experiment. |
| `variants/D-IgnorePlayer` | `FreighterIgnorePlayer` → true | Sledgehammer: freighter AI ignores the player completely, including deliberate pod farming. Only if A and B both fail. |

Install a variant exactly like A: copy the folder under `GAMEDATA/MODS/` inside the variant
directory into the game's `GAMEDATA\MODS\`. Variants A, B and C touch different properties and
can coexist; D supersedes A.

## Does it match "only destroying something triggers hostility"?

Partly, and honestly: the evidence says hits and destruction both feed the same hostility
logic, but it is not known whether destroying a pod goes through the alert counter or a
separate "destroyed" path.

* If destruction is a separate path, variant A gives you exactly the requested rule.
* If it is the same counter, variant A removes all player-caused hostility on freighters
  (a superset of what you asked for). Variant B is then the closest match: deliberate,
  sustained fire still angers the freighter, but it forgives quickly.

The cleanest "destruction only" implementation would flip `NoConsequencesDuringPirateBattle`
on the cargo-pod/turret destructible entities instead of touching the globals. That needs the
freighter scene files to find which entity carries the component; it is documented as a
follow-up in `docs/RESEARCH.md`.

## Alternative you asked about: clear hostility when the captain radios the reward

Not possible with data edits. The reward hail is a mission-sequence step and can only run
`GcReward*` actions; libMBIN 7.02 has no reward that resets a freighter's AI alert/attack
state (only sentinel wanted level and faction standing). It would need a code hook (ASI/DLL
plugin patching the freighter AI), which is a different class of mod with its own
anti-cheat/compatibility caveats. Variant B is the data-only approximation.

## Building it yourself / other mods that edit the same file

`amumss/FreighterFriendlyFireTolerance.lua` builds the same patch with
[AMUMSS](https://github.com/HolterPhylo/AMUMSS) from your own game files, prints the vanilla
values it replaces, and merges with other scripts that touch `GCAISPACESHIPGLOBALS`. Set
`VARIANT` at the top of the script.

`tools/read_globals.py` prints the vanilla values of every field this mod cares about straight
from an extracted `.MBIN` (no .NET needed), with a sanity check that the struct offsets still
line up with the current game build:

```
python3 tools/read_globals.py "path/to/GLOBALS/GCAISPACESHIPGLOBALS.GLOBAL.MBIN"
```

## Test plan

1. Variant A only. Defend a freighter, graze its pods and turrets a handful of times, kill all
   pirates. Expect the hangar to stay open and the reward hail to arrive.
2. Same, but destroy one cargo pod on purpose. Note whether the freighter goes hostile; this
   tells you which of the two cases above you are in.
3. If A changed nothing, add B; if still nothing, try D. C is unlikely to matter; test it last.

Please report which variant worked so the default can be corrected.

## Compatibility

* Game: 7.0/7.01 Cosmos. Field names verified against libMBIN 7.02.0.1 (2026-09-11).
* Conflicts only with other mods that override the same properties in
  `GCAISPACESHIPGLOBALS.GLOBAL` (for example "Even Angrier Freighters" / "Pissed Off
  Freighters", which set the same thresholds to 0). Mod priority in the in-game mod list decides.
* Multiplayer: globals are client-side; other players' freighters are unaffected.
