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
| `FreighterBattleIgnoreFriendlyFireDistance` | float | 0x14C4 | See the dedicated section below. Best-supported reading: the radius around a freighter battle inside which the player's friendly fire on the *escort trader ships* is not reported as a crime. It is in the player-ship globals, not the AI globals, and there is no evidence it feeds the freighter's own alert counter. Community defaults: 7200 (3.x era) / 10000 (later). |

### `GLOBALS/GCGAMEPLAYGLOBALS.GLOBAL.MBIN` (`cGcGameplayGlobals`)

| Field | Type | Offset | What we know |
|---|---|---|---|
| `FreighterCargoPodHealthFraction` | float | 0x15E4 | Default 0.8 (Xen0nex scripts). Cargo pods are not one-shot, which is consistent with the user's report that mere *hits* (not destruction) already cause hostility. |
| `FreighterBattleRadius` | float | 0x15E0 | Default 5000. |

## `FreighterBattleIgnoreFriendlyFireDistance`: what it most likely does

Evidence gathered from libMBIN's git history and Hello Games' patch notes:

1. **It is a player-ship global, not an AI global.** It lives in `GcSpaceshipGlobals`
   (`GCSPACESHIPGLOBALS.GLOBAL.MBIN`), the struct that holds the player's flight model,
   docking, cockpit and multiplayer distances. Every freighter-hostility knob
   (`FreighterAlertThreshold`, `FreighterAttackAlertThreshold`, `FreighterAlertTimeOut*`,
   `FreighterRegisterHitCooldown`, `FreighterIgnorePlayer`, `TraderIgnoreHits`) lives in
   `GcAISpaceshipGlobals` instead.
2. **Its struct slot dates to Beyond (2.0, August 2019).** In libMBIN's Beyond-era dump
   (commit `9921e1c6`, 2019-08-22) the slot at 0x104 already exists as `Unknown0x104`, right
   after `DistanceFromShipToAllowSpawningOnFreighter` and before the `AltControls` bools. The
   NEXT-era layout (commit `5089756a`, 2018-11) has an unrelated field there. libMBIN only
   learned the name in its 2.43 re-dump (commit `4f10ebfc`, 2020-06-08); the 2.41-2.43
   hotfix notes contain nothing about freighters, so the name is not tied to those patches.
3. **Beyond's patch notes describe exactly one freighter-battle friendly-fire change:**
   "The tolerance of trader ships participating in a freighter battle was increased so that
   they do not report friendly fire as a crime unless the shot is fatal." That sentence has
   all three parts of the field name: *freighter battle*, *ignore friendly fire*, and an
   implicit *distance* (what counts as "participating in" the battle).
4. **The default value fits a participation radius.** 7200 is larger than
   `FreighterBattleRadius` (5000); the later 10000 equals `SpaceBattleAnyHostileShipsRadius`.
   A radius of "how close to the battle you must be for your stray shots to be excused" is
   the natural thing to keep in step with those constants.

Conclusion (not verified in-game, but the only reading consistent with all four points): while a
freighter battle is active, the player's hits on the friendly *trader/escort ships* within this
distance of the battle are not reported to the crime/wanted system unless the hit is fatal. The
"unless fatal" part is code, not data. Nothing links this field to the freighter's own alert
accumulator, which is what closes the hangar. Raising it therefore should not fix the hangar
problem; setting it to 0 would make stray hits on escorts count as crimes again. Variant C stays
in the repo only as a cheap experiment because the reading is inferred rather than observed.

Side note for the "destruction only" goal: Hello Games implemented that exact rule for the
escort traders in Beyond ("unless the shot is fatal"). The freighter itself never received an
equivalent; it uses the alert counter with, since 4.40, a per-hit cooldown
(`FreighterRegisterHitCooldown`, added in the same commit as the alert lights and the
Echoes freighter-battle rework).

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

## Cargo pods: where the destruction penalties live

* Entity files: `MODELS/COMMON/SPACECRAFT/INDUSTRIAL/CONTAINER/CONTAINER{A,B,C,E,F,G,H}/ENTITIES/CONTAINER_{A..H}.ENTITY.MBIN`
  (from the pak hash list; Lenni's `NoStandingLoss.lua` in the community script collection edits
  `StandingChangeOnDeath` in precisely these files plus the small/tiny freighter entities).
* `cGcDestructableComponentData` on them carries `StandingChangeOnDeath[]`, `IncreaseWanted`,
  `NoConsequencesDuringPirateBattle`, `LootReward`/`LootItems`, `DamagesParentWhenDestroyed`.
* `cGcShootableComponentData` carries `Health`, `IncreaseWanted`, `IncreaseWantedThresholdTime`,
  `IgnorePlayer`, `CouldCountAsArmourForParent`.
* Player reports agree that shooting pods summons sentinels and, per the wiki, costs standing
  with the system's race. None of that goes through the freighter's alert counter, so variant A
  leaves those penalties intact. Whether pods ship with `NoConsequencesDuringPirateBattle` set is
  unknown; the AMUMSS script forces it to `false`.

## Why a "destruction only" freighter reaction is not available in data

The freighter's reaction is a single alert accumulator in `GcAISpaceshipGlobals` fed by registered
hits (`MinAggroDamage` filter, `FreighterRegisterHitCooldown` rate limit, two thresholds, timed
decay). Nothing in the globals or the component data names a "destroyed a child part" input to
it. The one speculative route is `DamagesParentWhenDestroyed` on the pod entity combined with a
`MinAggroDamage` set above any single weapon hit but below the parent-damage event, which would
make only the destruction event register. It is untested and depends on the parent-damage event
going through the same registration path; it is listed here as a follow-up experiment only.

Practical consequence: the only data-driven separation between "stray" and "deliberate" is
intensity, which is what variants E and F exploit (`tools/make_variant.py`). F scales both
thresholds; E scales decay. Both are relative to vanilla values that are not published.

## Code plugin research (plugin/freighter_class_peek.py)

Sources: NMS.py commit `bcb0eaa` (2023-08-13) preserved a 22,079-name function catalogue with
4.13 offsets (`nmspy/data/func_offsets.py`) and ctypes call signatures (`func_sigs.py`); current
NMS.py (`170671.6`, 2026-09-08) gives `cGcInventoryStore.mClass` at 0x100 and a working
byte pattern for `cGcInventoryStore::Add`.

Relevant functions (4.13 offsets, for structural matching only):

| Function | 4.13 offset | Signature |
|---|---|---|
| `cGcAISpaceshipComponent::GenerateInventory` | 0x11F7610 | `(this, cTkSeed*, int, bool) -> void` |
| `cGcAISpaceshipComponent::GetClass` | 0x478B60 | `(this) -> int` |
| `cGcAISpaceshipComponent::GetInventoryStore` | 0x11F74C0 | `(this, ?, int) -> cGcInventoryStore*` |
| `cGcAISpaceshipComponent::GetResourceSeed` | 0x1055510 | `(this, cTkSeed* out) -> cTkSeed*` |
| `cGcInventoryStore::GenerateProceduralClass` | 0x3334B0 | `(this, cTkSeed*) -> void` |
| `cGcInventoryStore::GetClass` | 0x34FDD0 | `(this) -> int` |
| `cGcAISpaceshipManager::SpawnShip` (3 overloads) | 0x11FF660 / 0x1202500 / 0x12029E0 | see catalogue |
| `cGcInteractionComponent::IsFreighterCaptainNPCInteraction` | 0xC5D7E0 | `(this) -> bool` |
| `cGcPlayerNotifications::AddTimedMessage` | 0x6F05B0 | hooked in current NMS.py (args flagged unconfirmed) |

Design consequence: the class is a field of the NPC ship's `cGcInventoryStore`, written by
`GenerateProceduralClass` inside `GenerateInventory`, and items are inserted through `Add`. The
plugin hooks `Add` (known pattern) and reads `mClass` after the store settles; the two generation
functions are optional refinements whose current-build patterns must be extracted by the user.
Open question the plugin's log answered (below): `GenerateInventory` runs at neither spawn nor
hangar entry; it runs lazily on first use, and the first use in normal play is the analysis
visor scan.

### Live findings on the current build (from the plugin's hooks, September 2026)

* An NPC freighter's inventory and class are not generated at warp-in, at hangar entry, or when
  the captain is spoken to. They are generated the first time the analysis visor scans the ship
  (`cGcBinoculars::PopulateDiscoveryInfo`) and cached in the component afterwards; repeated scans
  of the same node do not regenerate. Verified over six reloads.
* The seed handed to the store generator is the freighter's scene-node handle plus one. Node
  handles are assigned per session, so the class re-rolls after every reload (observed A, C, B,
  A, B, B for the same freighter across reloads). Save-scumming therefore works, and the roll is
  decided at scan time, not at spawn time.
* Call chain during a scan, innermost first: store generator `NMS+0x4CBC80`
  (`this` = the store, args `(cTkSeed*, class+1, 1)`; C, B, A observed as 1, 2, 3) <-
  `NMS+0x4CA440` (a store-level wrapper; hooking it crashes, it takes stack arguments) <-
  `NMS+0x4DD3A4` <- `NMS+0x4CA796` <- component generator (return address `NMS+0x1703C6C`,
  `this` = the `cGcAISpaceshipComponent`) <- lazy getter (return address `NMS+0x1703900`) <-
  visor code `NMS+0xE7AC30` / `NMS+0x53D5FB`.
* The getter is called as `getter(this=component, a1=0x670F00, a2=<pointer into NMS.exe>,
  a3=0xF367)` and returns the component's general store. It matches the 4.13 signature
  `cGcAISpaceshipComponent::GetInventoryStore(this, ?, int)`; the extra registers are replayed
  verbatim by the peek since their meaning is unconfirmed.
* `cGcAISpaceshipComponent` layout on this build: vtable at +0 = `NMS+0x4AE0EB8`; pointer to
  `cGcAISpaceshipComponentData` at +0x28 (that struct has `Class` at +0x34, `Type` at +0x38 with
  `GcAISpaceshipTypes` = None, Pirate, Police, Trader, Freighter(4), PlayerSquadron, DefenceForce,
  SwarmDrone; the observed freighter had `CombatDefinitionID` empty); general
  `cGcInventoryStore` embedded at +0x7D40; technology store at +0x7F88. Component addresses
  were identical across sessions, so the components live in a stable pool.
* Peek-from-space design that follows: scan the heap for objects whose first qword is the
  component vtable, keep those whose data pointer says Type = Freighter, and call the getter on
  each one whose store still has zero width and height. That is the same code path a visor scan
  takes, so the class it yields is the class a scan would yield, and the game keeps it.

## Unknowns to settle in-game (test plan)

0. Variant A has been confirmed working in-game by the author (hangar stays open after stray hits).
1. Install variant A. Start a capital-freighter defence, deliberately graze the freighter's
   pods/turrets a few times without destroying anything, finish the pirates. Expected: hangar
   open, reward hail. If still closed: variant A's field is not the gate; try B, then D.
2. With A installed, deliberately destroy one cargo pod. If the freighter goes hostile, the
   "destroy" path is separate from the alert accumulator and the mod matches the requested
   behaviour exactly. If it does not, A has removed all player-caused hostility (superset).
3. Variant C: expected to change only crime reporting for hits on escort traders, not freighter hostility. Test it last, at `1000000` and `0`.
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
