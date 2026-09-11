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
| `GAMEDATA/MODS/FreighterFriendlyFireTolerance` (**A**) | `FreighterAttackAlertThreshold` → 1e9 | Confirmed working in-game. Hits can never escalate to hostility. **Personal use only**: it also removes the freighter's retaliation against deliberate attacks (see "Publishing"). |
| `variants/B-AutoForgive` | `FreighterAlertTimeOutMinTime` → 3 s, `FreighterAlertTimeOutRate` → 1e6 | Keeps hostility for a sustained deliberate attack, but the freighter forgives you a few seconds after the last hit, so the hangar is open by the time the captain hails you. Closest data-only version of "clear hostility at the reward call". Can be installed alongside A. |
| `variants/C-IgnoreBattleFriendlyFire` | `FreighterBattleIgnoreFriendlyFireDistance` → 1e6 (in `GCSPACESHIPGLOBALS`) | Low expectations. Research (see docs/RESEARCH.md) indicates this is the radius inside which your hits on the *escort trader ships* are excused as crimes during a battle, not a freighter-hostility knob. Kept as a cheap experiment. |
| `variants/D-IgnorePlayer` | `FreighterIgnorePlayer` → true | Sledgehammer: freighter AI ignores the player completely, including deliberate pod farming. Only if A and B both fail. |
| **F, strict** (generate with `tools/make_variant.py --mode strict`, or `VARIANT = "F"` in the AMUMSS script) | both alert thresholds × 5, nothing else | **Recommended for publishing.** Vanilla behaviour in every respect except that the stray-hit budget per battle is five times larger. A deliberate attack on a cargo pod still crosses the threshold within seconds and the freighter stays hostile exactly as in vanilla. |
| **E, forgive** (`--mode forgive` / `VARIANT = "E"`) | vanilla thresholds; alert bar drains in ~15 s, starting 2 s after the last hit | Sparse hits never accumulate; continuous fire trips hostility within a second or two; the freighter forgives you ~15 s after you stop. Closest to "forgive unless you benefited", but an attacker can dock again shortly after a raid. |
| **EF** (`--mode both` / `VARIANT = "EF"`) | F thresholds and E decay | Most lenient of the deterrent-keeping options. |

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

## Publishing: keeping a deterrent against deliberate attacks

Variant A works but is not publishable as-is: with the attack threshold unreachable, a player can
sit next to a civilian freighter and destroy its cargo pods for loot without the freighter ever
shooting back. Three facts from the data decide how to close that hole:

1. **The freighter's hostility is one counter, fed by hits.** There is no separate "the player
   destroyed something" trigger in the globals. Whatever you do to the counter applies to
   stray shots and to deliberate pod fire alike. The only lever that separates them is
   *intensity*: a stray hit is one shot every many seconds, a pod kill is continuous fire.
2. **Pod destruction has its own penalties that do not go through that counter.** The capital
   freighter cargo pods are the `CONTAINER_A` to `CONTAINER_H` entities under
   `MODELS/COMMON/SPACECRAFT/INDUSTRIAL/CONTAINER/`. Their destructible component carries
   `StandingChangeOnDeath` (a community "NoStandingLoss" script zeroes exactly that field on
   exactly those files) and `IncreaseWanted`, and the shootable component carries its own
   `IncreaseWanted` with a threshold time. So even under variant A, farming pods costs faction
   standing and summons sentinels. The AMUMSS script additionally forces
   `NoConsequencesDuringPirateBattle` to false on those entities so a running battle does not
   suspend the penalties.
3. **Hello Games' own "unless the shot is fatal" rule exists only for escort traders**, not
   for the freighter (Beyond patch notes). A data-only mod cannot add it to the freighter.

So the publishable choice is **F (strict)**: multiply both alert thresholds by five and leave
decay alone. Accidental hits need a budget five times larger than vanilla before the hangar
closes, which covers "extremely careful" play with margin, while a deliberate pod attack still
trips hostility in seconds and persists like vanilla. It is the smallest deviation from vanilla
that fixes the complaint, so it is also the easiest to defend on a mod page. If five turns out to
be too tight or too loose, it is one number.

**E (forgive)** is the alternative if you prefer the freighter to calm down on its own. It keeps
the retaliation during an attack and the pod penalties, but hostility fades about 15 s after the
last hit, so a raider gets docking rights back quickly. Say so on the mod page if you ship it.

Both are defined relative to your game's vanilla values, which are not published anywhere, so
either build with the AMUMSS script (it multiplies in place) or generate the EXML:

```
python3 tools/read_globals.py  path/to/GLOBALS/GCAISPACESHIPGLOBALS.GLOBAL.MBIN   # see the vanilla numbers
python3 tools/make_variant.py --mode strict --mbin path/to/GLOBALS/GCAISPACESHIPGLOBALS.GLOBAL.MBIN
```

The generated folder under `variants/E-Calibrated/GAMEDATA/MODS/` installs like any other variant.
Test protocol for F: (1) careful battle, expect open hangar; (2) deliberately kill one pod with
sustained fire, expect hostility, sentinels and a standing hit; (3) if (1) still closes the hangar,
raise `--threshold-mult`; if (2) never trips, lower it.

## Building it yourself / other mods that edit the same file

`amumss/FreighterFriendlyFireTolerance.lua` builds the same patch with
[AMUMSS](https://github.com/HolterPhylo/AMUMSS) from your own game files, prints the vanilla
values it replaces, and merges with other scripts that touch `GCAISPACESHIPGLOBALS`. Set
`VARIANT` at the top of the script (`F` by default); `PODS_KEEP_CONSEQUENCES` also patches the cargo-pod entities.

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

## Companion code plugin: see the class before landing

`plugin/freighter_class_peek.py` is a pyMHF/NMS.py mod that reports an NPC freighter's class the
moment the game rolls it, by hooking the inventory generation path. It is a separate, code-level
tool with its own requirements and caveats; see `plugin/README.md`.

## Compatibility

* Game: 7.0/7.01 Cosmos. Field names verified against libMBIN 7.02.0.1 (2026-09-11).
* Conflicts only with other mods that override the same properties in
  `GCAISPACESHIPGLOBALS.GLOBAL` (for example "Even Angrier Freighters" / "Pissed Off
  Freighters", which set the same thresholds to 0). Mod priority in the in-game mod list decides.
* Multiplayer: globals are client-side; other players' freighters are unaffected.
