# Research notes: what actually makes an NPC freighter hostile

This file records what was verified, from where, and what remains an assumption. Nothing in
the mod was tested in-game by the author of these notes (no copy of the game in the build
environment). Treat the "Unknowns" section as the test plan.

## Game version and mod format

* Latest game version at time of writing: **7.0 "Cosmos"** (2026-09-09) with patch **7.01**
  (2026-09-10). Sources: Hello Games release log / press coverage (gematsu.com, pushsquare.com,
  the fandom wiki page "Update 7.00").
* libMBIN (the community's ground truth for the game's data structures) is at version
  `7.02.0.1`, commit `9375313` dated 2026-09-11, i.e. it tracks the Cosmos data layout. All
  field names below come from that commit.
* Since 5.50 (Worlds Part II) mods are loose files under `GAMEDATA\MODS\<ModName>\` mirroring
  the game's internal paths, and a `.EXML` file is a **partial patch**: only the properties it
  lists are overridden. A `.MBIN` (or full `.MXML`) fully replaces the file. Sources: the
  fandom/miraheze "Mods" pages, "Principles of EXML Modification", the AMUMSS README
  ("NMS >= v5.5, mod are sub-folders in NMS GAMEDATA\MODS folder").
* The partial file uses Hello Games' native MXML syntax. libMBIN writes the root as
  `<Data template="c<ClassName>">` (`NMSTemplate.cs` line 1522: `Template = "c" + type.Name`),
  floats as `0.000000`-style decimals and booleans as lowercase `true`/`false`
  (`NMSTemplate.cs` lines 1230-1241).
* Globals now live at `GLOBALS\...` (not `METADATA\GLOBALS\...`): the AMUMSS pak hash list for
  build 155759 lists `GLOBALS/GCAISPACESHIPGLOBALS.GLOBAL.MBIN` and
  `GLOBALS/GCSPACESHIPGLOBALS.GLOBAL.MBIN`.

## Fields that plausibly govern the behaviour

### `GLOBALS/GCAISPACESHIPGLOBALS.GLOBAL.MBIN` (`cGcAISpaceshipGlobals`)

| Field | Type | Offset (libMBIN 7.02) | What we know |
|---|---|---|---|
| `FreighterAlertThreshold` | float | 0x9C4 | Alert level at which the freighter goes to "alert" (lights). Babscoole's *Even Angrier Freighters* / *Pissed Off Freighters* set it to `0` to make freighters alert instantly. |
| `FreighterAttackAlertThreshold` | float | 0x9D0 | Alert level at which it starts shooting at you. Same mods set it to `0` = attack on first hit. **Direction is therefore certain: higher = more tolerant.** Default value is not published anywhere we could reach. |
| `FreighterAlertTimeOutMinTime` | float | 0x9C8 | Presumably: seconds after the last registered hit before the alert level starts to decay. |
| `FreighterAlertTimeOutRate` | float | 0x9CC | Presumably: decay per second. |
| `FreighterRegisterHitCooldown` | float | 0x9E8 | Presumably: minimum spacing between hits that count towards alert (so one burst does not count N times). |
| `FreighterAttackDisengageDistance` | float | 0x9D4 | Distance at which an attacking freighter gives up. Community default 3000 (MyersP `X-SpaceshipGlobals.lua`). Evidence that the hostile state is *not* a permanent latch. |
| `MinAggroDamage` | int | 0xA60 | Minimum damage of a hit for AI ships to register aggro. Gumsk notes default 100 (2.42 era); the angry-freighter mods set it to 0. |
| `FreighterIgnorePlayer` | bool | 0xEED | Developer toggle; freighter AI ignores the player. |
| `FreightersAlwaysAttackPlayer` | bool | 0xEEE | Developer toggle; opposite of the above (used by *Pissed Off Freighters*). |

### `GLOBALS/GCSPACESHIPGLOBALS.GLOBAL.MBIN` (`cGcSpaceshipGlobals`)

| Field | Type | Offset | What we know |
|---|---|---|---|
| `FreighterBattleIgnoreFriendlyFireDistance` | float | 0x14C4 | Hypn0tick's Modular Flight Framework: "the range at which freighters will ignore friendly fire", game default 7200 (older) / 10000 (newer). Whether the check is "inside" or "outside" that range is not documented. |

### `GLOBALS/GCGAMEPLAYGLOBALS.GLOBAL.MBIN` (`cGcGameplayGlobals`)

| Field | Type | Offset | What we know |
|---|---|---|---|
| `FreighterCargoPodHealthFraction` | float | 0x15E4 | Default 0.8 (Xen0nex scripts). Cargo pods are not one-shot, which is consistent with the user's report that mere *hits* (not destruction) already cause hostility. |
| `FreighterBattleRadius` | float | 0x15E0 | Default 5000. |

### Per-entity destructible data (`cGcDestructableComponentData`)

`NoConsequencesDuringPirateBattle` (bool), `IncreaseWanted` (int), `StandingChangeOnDeath`
(int[]) exist on every destructible entity. They plausibly control what happens when a cargo
pod or turret is *destroyed*, and are Hello Games' own hook for "no consequences during pirate
battles". The freighter cargo-pod entity files could not be located from the pak hash lists
alone (the `INDUSTRIAL/CARGO/*.SCENE.MBIN` parts carry no ENTITY files of their own; the
destructible component is presumably inherited from a shared entity such as
`MODELS/COMMON/SPACECRAFT/INDUSTRIAL/SHARED/ENTITIES/HULL_A.ENTITY.MBIN` or attached in the
scene). This is the most promising route for a "destruction only" rule and is left as a
follow-up that needs the actual scene files.

## Player-side evidence about the mechanism

* Steam discussions consistently say hull hits do not anger the freighter; only cargo pods and
  turrets do ("Hitting other parts of the freighter does not cause them to turn against you,
  just their cargo pods and turrets").
* A hostile civilian freighter can be reset by re-summoning / warping it, i.e. the state is
  per-instance runtime AI state, not persisted.
* Player squadrons (wingmen) also register as the player's friendly fire in some reports.

## What a "clear hostility when the captain hails you" mod would need

The battle is driven by mission sequences (`GcMissionSequenceFreighterEngage`,
`GcMissionSequenceFreighterDefend`, condition `GcMissionConditionFreighterBattle` with status
`Reward`). The reward step can only run `GcReward*` actions. libMBIN 7.02 lists 196 reward
types; the only ones touching hostility are `GcRewardWantedLevel` (sentinel/police wanted
level) and `GcRewardStanding`/`GcRewardFactionStanding` (faction reputation). **There is no
reward action that resets an AI freighter's alert/attack state**, so this cannot be done with
data edits alone. It would need a code-level hook (an ASI/DLL plugin patching the freighter AI
component), which is outside the scope of an MXML mod. Variant B (fast alert decay) is the
closest data-only approximation.

## Unknowns to settle in-game (test plan)

1. Install variant A. Start a capital-freighter defence, deliberately graze the freighter's
   pods/turrets a few times without destroying anything, finish the pirates. Expected: hangar
   open, reward hail. If still closed: variant A's field is not the gate; try B, then D.
2. With A installed, deliberately destroy one cargo pod. If the freighter goes hostile, the
   "destroy" path is separate from the alert accumulator and the mod matches the requested
   behaviour exactly. If it does not, A has removed all player-caused hostility (superset).
3. Variant C: test both `1000000` and `0`; keep whichever reduces hostility, or drop it.
4. Run `tools/read_globals.py` on the extracted globals to record the vanilla values; then
   tune B's `FreighterAlertTimeOutMinTime` if you want a longer grace period.

## Sources

* libMBIN source (monkeyman192/MBINCompiler, commit 9375313): field names, offsets, MXML syntax.
* MetaIdea/nms-amumss-lua-mod-script-collection (commit of 2026-09-09): Babscoole
  "Even Angrier Freighters.lua" / "Pissed Off Freighters.lua", Hypn0tick "Modular Flight
  Framework", Xen0nex PTSd scripts, Gumsk gShip Flight, MyersP X-SpaceshipGlobals.lua.
* HolterPhylo/AMUMSS: README (GAMEDATA\MODS layout) and pak hash list v155759 (file paths).
* Steam community threads on freighter friendly fire (summarised via web search; direct fetch
  blocked in the build environment).
