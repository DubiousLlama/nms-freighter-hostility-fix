# /// script
# dependencies = ["pymhf[gui]>=0.2.3", "nmspy>=170671.6"]
#
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_paused = false
#
# [tool.pymhf.gui]
# always_on_top = true
#
# [tool.pymhf.logging]
# log_dir = "."
# log_level = "info"
# window_name_override = "Freighter Class Peek"
# ///
"""
Freighter Class Peek: shows the class (C/B/A/S) of NPC freighters the moment the game generates
their inventory, so you do not have to land to find out.

How it works
------------
Every procedurally generated inventory in No Man's Sky ends up in a `cGcInventoryStore`, whose
`mClass` field (offset 0x100 in the current build, per NMS.py) holds the rolled class. Items are
inserted through `cGcInventoryStore::Add`, which NMS.py already hooks with a working byte pattern
for the current build. This mod therefore:

  Layer 1 (works out of the box, no reverse engineering):
    hooks `cGcInventoryStore::Add`, ignores the player's own stores, and reports every other
    store that receives items, with its dimensions, slot count, class and the address of the
    code that called `Add`. Freighter-sized stores are highlighted. Calibration mode lets you
    learn which caller address corresponds to NPC-ship inventory generation so later events are
    tagged with confidence.

  Layer 2 (optional, needs two byte patterns for the current NMS.exe):
    hooks `cGcAISpaceshipComponent::GenerateInventory` (the function that builds an NPC ship's
    purchasable inventory) and `cGcInventoryStore::GenerateProceduralClass` (the class roll
    itself). With these the attribution is exact instead of heuristic. See README.md for how to
    obtain the patterns; leave the strings empty to run Layer 1 only.

Function names and call signatures come from the 4.13-era symbol catalogue preserved in NMS.py's
git history (tools/data.json ancestors); `mClass` and the `Add` pattern come from current NMS.py.
"""
import ctypes
import logging
import time
from collections import deque
from ctypes import _Pointer, c_bool, c_float, c_int, c_uint32, c_uint64
from dataclasses import dataclass
from typing import Annotated, Optional

from pymhf import Mod, ModState
from pymhf import FUNCDEF
from pymhf.core._internal import BASE_ADDRESS, SIZE_OF_IMAGE
from pymhf.core.hooking import Structure, function_hook, get_caller, manual_hook, on_key_pressed
from pymhf.core.memutils import get_addressof, map_struct
from pymhf.gui.decorators import BOOLEAN, INTEGER, STRING, gui_button

import nmspy.data.basic_types as basic
import nmspy.data.exported_types as nmse
import nmspy.data.types as nms
from nmspy.common import gameData
from nmspy.data.enums import cGcInventoryClass
from nmspy.decorators import main_loop

logger = logging.getLogger("FreighterClassPeek")

# ============================================================================================
# Configuration
# ============================================================================================

# Layer 2 byte patterns for the CURRENT NMS.exe. Leave empty to use Layer 1 only.
# Format is the pyMHF/IDA style: "48 89 5C 24 ? 57 48 83 EC ? ..."
SIG_GENERATE_INVENTORY = ""  # cGcAISpaceshipComponent::GenerateInventory(this, cTkSeed*, int, bool)
SIG_GENERATE_PROCEDURAL_CLASS = ""  # cGcInventoryStore::GenerateProceduralClass(this, cTkSeed*)

# Layer 1 calibration: relative addresses (NMS.exe+X) of the code that calls cGcInventoryStore::Add
# while an NPC ship inventory is generated. Fill from the calibration log (see README). Optional.
KNOWN_GENERATION_CALLERS: set[int] = set()

# Step-2 hook: once the caller-address calibration (USE_GET_CALLER) has shown where cGcInventoryStore::Add
# is called from during a visor scan of a freighter, use the "Find function start" button to turn that
# return address into the start of the enclosing function, and put it here (relative to NMS.exe, e.g.
# 0x11F7610). The mod then hooks that function as cGcAISpaceshipComponent::GenerateInventory(this, seed*,
# int, bool) and logs the component pointer and arguments every time the game generates an NPC inventory.
GENERATOR_FUNCTION_OFFSET = 0

# Alternative to typing into the GUI: put the "caller NMS+0x..." value here (e.g. 0x11F7A31), reload, and
# press "Find function start". The GUI field, if filled, takes precedence.
CALLER_ADDRESS_TO_RESOLVE = 0

# Layer 1 heuristic: stores at least this many slots big are labelled "freighter-sized".
MIN_SLOTS_FOR_FREIGHTER = 20

# Record the address of the code that called cGcInventoryStore::Add (needed for caller-based
# calibration). pyMHF implements this by rewriting bytes inside minhook's trampoline for the hooked
# function; Add is called constantly, so this is the riskiest part of the mod. Off for the first run;
# turn on once the mod is known to load cleanly.
USE_GET_CALLER = False

# Hook cGcPlayerNotifications::AddTimedMessage so the mod can show HUD messages. NMS.py marks that
# function's argument list as unconfirmed since 4.13, and a hook re-calls the original with the
# declared arguments, so a wrong list can crash the game the first time it shows any timed message.
# Off by default; the pyMHF window shows the result regardless.
ENABLE_HUD_ANNOUNCE = False

# Delay before a newly touched store is inspected, so the generator has finished writing it.
SETTLE_SECONDS = 0.25

# ============================================================================================
# Optional Layer 2 hook definitions (only created when patterns are supplied)
# ============================================================================================

if SIG_GENERATE_INVENTORY:

    class cGcAISpaceshipComponent(Structure):
        @function_hook(SIG_GENERATE_INVENTORY)
        def GenerateInventory(
            self,
            this: "_Pointer[cGcAISpaceshipComponent]",
            lpSeed: c_uint64,  # cTkSeed *
            liArg: c_int,
            lbArg: Annotated[bool, c_bool],
        ): ...


if SIG_GENERATE_PROCEDURAL_CLASS:

    class cGcInventoryStoreGen(Structure):
        @function_hook(SIG_GENERATE_PROCEDURAL_CLASS)
        def GenerateProceduralClass(
            self,
            this: "_Pointer[nms.cGcInventoryStore]",
            lpSeed: c_uint64,  # cTkSeed *
        ): ...


# ============================================================================================
# Data
# ============================================================================================


@dataclass
class InventoryEvent:
    when: float
    store_addr: int
    caller: int
    width: int
    height: int
    cells: int  # width * height; the reliable size measure
    layout_slots: int  # cGcInventoryLayout.Slots as read; observed constant, kept for diagnostics
    klass: str
    name: str
    source: str  # "GenerateInventory" | "caller-match" | "heuristic"
    repeat: bool = False  # same store address reported before with the same class

    def line(self, threshold: int = MIN_SLOTS_FOR_FREIGHTER) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(self.when))
        tag = "FREIGHTER?" if self.cells >= threshold else "small"
        rep = " (repeat)" if self.repeat else ""
        return (
            f"{ts}  class {self.klass:<1}  {self.width}x{self.height}={self.cells}  [{self.source}] {tag}{rep}  "
            f"store 0x{self.store_addr:X}  caller NMS+0x{self.caller:X}  layoutSlots={self.layout_slots}"
        )


@dataclass
class PeekState(ModState):
    announce_in_game: bool = False
    calibration_logging: bool = True
    min_slots: int = MIN_SLOTS_FOR_FREIGHTER
    last_freighter: str = "none seen yet"
    caller_to_resolve: str = ""


# ============================================================================================
# Mod
# ============================================================================================


class FreighterClassPeek(Mod):
    __author__ = "DubiousLlama"
    __description__ = "Show NPC freighter class as soon as it is generated (no landing needed)"
    __version__ = "0.1"

    state = PeekState()

    def __init__(self):
        super().__init__()
        self._pending: dict[int, tuple[float, int, bool]] = {}  # store_addr -> (t, caller, in_generate)
        self._reported: dict[int, float] = {}  # store_addr -> last report time (debounce)
        self._in_generate = False
        self._generate_component = 0
        self._player_ranges: Optional[list[tuple[int, int]]] = None
        self._notifications_addr = 0
        self._events: deque[InventoryEvent] = deque(maxlen=16)
        self._seen_callers: dict[int, int] = {}
        self._known_stores: dict[int, str] = {}  # store_addr -> last class reported
        self._burst: list[InventoryEvent] = []
        self._burst_t = 0.0
        self._in_visor_scan = False
        self._pending_scan_t = 0.0
        self._last_scan_node = 0
        self._generated_components: deque[tuple[float, int, int, int]] = deque(maxlen=16)  # (t, this, arg, flag)

    # ---------------------------------------------------------------- GUI

    @property
    @STRING("Last freighter-sized inventory", readonly=True)
    def last_freighter(self) -> str:
        return self.state.last_freighter

    @last_freighter.setter
    def last_freighter(self, value: str):
        pass

    @property
    @STRING("Recent NPC inventories", readonly=True, multiline=True, height=220)
    def recent(self) -> str:
        return "\n".join(e.line(self.state.min_slots) for e in reversed(self._events)) or "(nothing yet)"

    @recent.setter
    def recent(self, value: str):
        pass

    @property
    @INTEGER("Freighter-sized threshold (slots)")
    def min_slots(self) -> int:
        return self.state.min_slots

    @min_slots.setter
    def min_slots(self, value: int):
        self.state.min_slots = max(1, int(value))

    @property
    @BOOLEAN("Log every caller address (calibration)")
    def calibration_logging(self) -> bool:
        return self.state.calibration_logging

    @calibration_logging.setter
    def calibration_logging(self, value: bool):
        self.state.calibration_logging = value

    @property
    @BOOLEAN("Announce in-game (needs ENABLE_HUD_ANNOUNCE=True in file)")
    def announce_in_game(self) -> bool:
        return self.state.announce_in_game

    @announce_in_game.setter
    def announce_in_game(self, value: bool):
        self.state.announce_in_game = value

    @gui_button("Re-announce last freighter")
    def reannounce(self):
        logger.info(f"Last freighter-sized inventory: {self.state.last_freighter}")
        self._announce(self.state.last_freighter)

    @gui_button("Dump player inventories (validates struct offsets)")
    def dump_player_inventories(self):
        """Print the player's own inventory stores through the same field map used for NPC stores.
        Compare with what you actually own: suit grid, each ship's class and size, freighter, exocraft.
        Sensible output means the cGcInventoryStore/cGcPlayerState offsets fit this game build;
        nonsense means NMS.py is stale for this build and every NPC line above is suspect too."""
        ps = gameData.player_state
        if ps is None:
            logger.error("player_state not available (load a save first)")
            return
        logger.info(f"player_state at 0x{get_addressof(ps):X}; units={ps.muUnits} nanites={ps.muNanites} "
                    f"primary ship index={ps.miPrimaryShip}")
        for attr in ("mInventories", "mShipInventories", "mShipInventoriesCargo", "mShipInventoriesTechOnly",
                     "mVehicleInventories", "mVehicleTechInventories"):
            try:
                arr = getattr(ps, attr)
            except Exception as e:
                logger.error(f"{attr}: {e}")
                continue
            for i, store in enumerate(arr):
                try:
                    w, h = int(store.miWidth), int(store.miHeight)
                    if w == 0 and h == 0 and len(store.mStore) == 0:
                        continue
                    try:
                        name = str(store.mInventoryName).strip("\x00")
                    except Exception:
                        name = "?"
                    logger.info(
                        f"  {attr}[{i}] @0x{get_addressof(store):X}: {w}x{h} cap={int(store.miCapacity)} "
                        f"items={len(store.mStore)} class={self._class_name(store)} name={name or '-'}"
                    )
                except Exception as e:
                    logger.error(f"  {attr}[{i}]: {e}")

    @gui_button("Dump caller statistics to log")
    def dump_callers(self):
        for caller, n in sorted(self._seen_callers.items(), key=lambda kv: -kv[1]):
            logger.info(f"caller NMS+0x{caller:X}: {n} Add() calls")

    @on_key_pressed("k")
    def key_reannounce(self):
        self.reannounce()

    @property
    @STRING("Caller address to resolve (paste 0x... from a log line)")
    def caller_to_resolve(self) -> str:
        return self.state.caller_to_resolve

    @caller_to_resolve.setter
    def caller_to_resolve(self, value: str):
        self.state.caller_to_resolve = str(value).strip()

    @gui_button("Find function start for caller address")
    def find_function_start(self):
        """Walk backwards from a return address (as logged by the caller capture) to the start of the
        enclosing function. MSVC pads between functions with 0xCC bytes and aligns starts to 16 bytes, so the
        first 16-aligned byte after a run of 0xCC below the address is the usual function start. Prints the
        three nearest candidates; the nearest one is right in the large majority of cases."""
        raw = self.state.caller_to_resolve.replace("NMS+", "").strip()
        try:
            rel = int(raw, 0) if raw else int(CALLER_ADDRESS_TO_RESOLVE)
        except ValueError:
            rel = int(CALLER_ADDRESS_TO_RESOLVE)
        if rel <= 0:
            logger.error("Set 'Caller address to resolve' first, e.g. 0x11F7A31 (from a 'caller NMS+0x...' log line)")
            return
        if rel >= SIZE_OF_IMAGE:
            logger.error(
                f"0x{rel:X} is larger than NMS.exe itself (0x{SIZE_OF_IMAGE:X} bytes): that is an absolute heap "
                "address such as a 'store 0x...' value, not a 'caller NMS+0x...' offset. Use the caller value. "
                "If every caller reads NMS+0x0, set USE_GET_CALLER = True and reload; the log must then show "
                "'has a modified hook to get the calling address'."
            )
            return
        try:
            span = 0x20000
            start = max(0, rel - span)
            buf = ctypes.string_at(BASE_ADDRESS + start, rel - start)
        except Exception as e:
            logger.error(f"Cannot read memory below NMS+0x{rel:X}: {e}")
            return
        candidates = []
        i = len(buf) - 1
        while i > 2 and len(candidates) < 3:
            if buf[i] == 0xCC and buf[i - 1] == 0xCC:
                j = i + 1  # first non-padding byte after the run
                while j < len(buf) and buf[j] == 0xCC:
                    j += 1
                cand = start + j
                if cand % 16 == 0 and cand not in candidates:
                    candidates.append(cand)
                i -= 2
            else:
                i -= 1
        if not candidates:
            logger.error("No 0xCC padding found within 128 KB below that address; use a disassembler.")
            return
        for c in candidates:
            head = ctypes.string_at(BASE_ADDRESS + c, 12).hex(" ").upper()
            logger.info(f"function start candidate: NMS+0x{c:X}  (0x{rel - c:X} bytes before the caller)  bytes: {head}")
        logger.info(f"Put the nearest candidate into GENERATOR_FUNCTION_OFFSET (e.g. 0x{candidates[0]:X}) and reload.")

    # ---------------------------------------------------------------- Hooks: Layer 1

    def _after_add(
        self,
        this: _Pointer[nms.cGcInventoryStore],
        result: _Pointer[nmse.cGcInventoryIndex],
        lItem: _Pointer[nmse.cGcInventoryElement],
    ):
        try:
            addr = get_addressof(this)
            caller = self._after_add.caller_address() if USE_GET_CALLER else 0
        except Exception:  # never let a detour raise into the game
            return
        if self._is_player_store(addr):
            return
        self._seen_callers[caller] = self._seen_callers.get(caller, 0) + 1
        # Keep the earliest caller seen for this store during the current burst of Add() calls.
        if addr not in self._pending:
            self._pending[addr] = (time.time(), caller, self._in_generate)
            if self._in_visor_scan:
                self._pending_scan_t = time.time()
        else:
            t0, c0, g0 = self._pending[addr]
            self._pending[addr] = (time.time(), c0, g0 or self._in_generate)

    # Apply the hook decorator (and optionally the caller capture) to the method defined above.
    if USE_GET_CALLER:
        _after_add = get_caller(nms.cGcInventoryStore.Add.after(_after_add))
    else:
        _after_add = nms.cGcInventoryStore.Add.after(_after_add)

    if ENABLE_HUD_ANNOUNCE:

        @nms.cGcPlayerNotifications.AddTimedMessage.after
        def _capture_notifications(self, this, *args):
            # Grab the cGcPlayerNotifications instance the first time the game shows any timed message.
            if not self._notifications_addr:
                try:
                    self._notifications_addr = get_addressof(this)
                    logger.info(f"Captured cGcPlayerNotifications at 0x{self._notifications_addr:X}")
                except Exception:
                    pass

    # ---------------------------------------------------------------- Hooks: visor scan bracket

    @nms.cGcBinoculars.PopulateDiscoveryInfo.before
    def _visor_before(self, this, lDiscoveryInfo, lpTargetNode, lpTargetAttachment, lbSubmitDiscovery, lbSilent):
        self._in_visor_scan = True
        self._pending_scan_t = time.time()
        try:
            self._last_scan_node = int(lpTargetNode.lookupInt)
        except Exception:
            self._last_scan_node = 0
        logger.info(f"visor PopulateDiscoveryInfo: target node handle {self._last_scan_node} (submit={lbSubmitDiscovery})")

    @nms.cGcBinoculars.PopulateDiscoveryInfo.after
    def _visor_after(self, this, *args):
        self._in_visor_scan = False

    # ---------------------------------------------------------------- Hooks: generator by offset (step 2)

    if GENERATOR_FUNCTION_OFFSET:

        @manual_hook(
            "AIShipGenerateInventory",
            offset=GENERATOR_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=None, argtypes=[c_uint64, c_uint64, c_uint64, c_uint64]),  # registers passed through untouched
            detour_time="before",
        )
        def _generator_before(self, this, lpSeed, liArg, lbArg):
            self._in_generate = True
            self._generate_component = int(this)
            arg32 = int(liArg) & 0xFFFFFFFF
            flag8 = int(lbArg) & 0xFF
            self._generated_components.append((time.time(), int(this), arg32, flag8))
            seed_txt = ""
            try:
                if int(lpSeed) > 0x10000:
                    seed_txt = " seed bytes=" + ctypes.string_at(int(lpSeed), 16).hex(" ").upper()
            except Exception:
                pass
            logger.info(
                f"generator(this=0x{int(this):X}, seed*=0x{int(lpSeed):X}, arg={arg32} (raw 0x{int(liArg):X}), "
                f"flag={flag8} (raw 0x{int(lbArg):X})) visor_scan={self._in_visor_scan}{seed_txt}"
            )

        @manual_hook(
            "AIShipGenerateInventory",
            offset=GENERATOR_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=None, argtypes=[c_uint64, c_uint64, c_uint64, c_uint64]),  # registers passed through untouched
            detour_time="after",
        )
        def _generator_after(self, this, lpSeed, liArg, lbArg):
            self._in_generate = False

    # ---------------------------------------------------------------- Hooks: Layer 2 (optional)

    if SIG_GENERATE_INVENTORY:

        @cGcAISpaceshipComponent.GenerateInventory.before
        def _gen_before(self, this, lpSeed, liArg, lbArg):
            self._in_generate = True
            self._generate_component = get_addressof(this)
            logger.info(
                f"cGcAISpaceshipComponent::GenerateInventory(component=0x{self._generate_component:X}, "
                f"arg={liArg}, flag={lbArg})"
            )

        @cGcAISpaceshipComponent.GenerateInventory.after
        def _gen_after(self, this, lpSeed, liArg, lbArg):
            self._in_generate = False

    if SIG_GENERATE_PROCEDURAL_CLASS:

        @cGcInventoryStoreGen.GenerateProceduralClass.after
        def _class_after(self, this, lpSeed):
            try:
                store = this.contents
                logger.info(
                    f"GenerateProceduralClass -> {self._class_name(store)} for store 0x{get_addressof(this):X} "
                    f"(inside GenerateInventory={self._in_generate})"
                )
                addr = get_addressof(this)
                if addr not in self._pending:
                    self._pending[addr] = (time.time(), 0, self._in_generate)
            except Exception:
                pass

    # ---------------------------------------------------------------- Per-frame processing

    @main_loop.after
    def _process_pending(self):
        if self._burst and time.time() - self._burst_t > 1.0:
            self._flush_burst()
        if not self._pending:
            return
        now = time.time()
        done = [a for a, (t, _, _) in self._pending.items() if now - t >= SETTLE_SECONDS]
        for addr in done:
            t, caller, in_gen = self._pending.pop(addr)
            if now - self._reported.get(addr, 0) < 2.0:
                continue  # same store reported moments ago
            self._reported[addr] = now
            try:
                self._inspect_store(addr, caller, in_gen)
            except Exception as e:
                logger.debug(f"inspect failed for 0x{addr:X}: {e}")

    def _inspect_store(self, addr: int, caller: int, in_gen: bool):
        store = map_struct(addr, nms.cGcInventoryStore)
        width, height = int(store.miWidth), int(store.miHeight)
        cells = max(0, width) * max(0, height)
        try:
            layout_slots = int(store.mLayoutDescriptor.Slots)
        except Exception:
            layout_slots = -1
        klass = self._class_name(store)
        try:
            name = str(store.mInventoryName).strip("\x00")
        except Exception:
            name = ""
        if in_gen:
            source = "GenerateInventory"
        elif caller in KNOWN_GENERATION_CALLERS:
            source = "caller-match"
        elif self._in_visor_scan or (time.time() - self._pending_scan_t < SETTLE_SECONDS + 0.5):
            source = "visor-scan"
        else:
            source = "heuristic"
        repeat = self._known_stores.get(addr) == klass
        self._known_stores[addr] = klass
        now = time.time()
        ev = InventoryEvent(now, addr, caller, width, height, cells, layout_slots, klass, name, source, repeat)
        self._events.append(ev)
        if self.state.calibration_logging:
            logger.info(ev.line(self.state.min_slots))

        # Group stores generated within the same short window: a freighter fleet spawning produces a
        # burst of several stores at once (capital ship plus escorts), traders landing produce singles.
        if now - self._burst_t > 1.0:
            self._flush_burst()
        self._burst.append(ev)
        self._burst_t = now

        if cells >= self.state.min_slots or source == "GenerateInventory":
            summary = f"class {klass}  ({width}x{height}={cells} cells, {source}{', repeat' if repeat else ''})"
            self.state.last_freighter = summary
            if not repeat:
                logger.info(f"FREIGHTER-SIZED INVENTORY GENERATED: {summary}")
                self._announce(f"Freighter inventory generated: class {klass}")

    def _flush_burst(self):
        if len(self._burst) >= 2:
            big = [e for e in self._burst if e.cells >= self.state.min_slots]
            desc = ", ".join(f"{e.klass} {e.width}x{e.height}" for e in self._burst)
            if big:
                best = max(big, key=lambda e: e.cells)
                logger.info(
                    f"BURST of {len(self._burst)} stores [{desc}] -> largest: class {best.klass} "
                    f"{best.width}x{best.height} (capital freighter candidate)"
                )
            else:
                logger.info(f"burst of {len(self._burst)} small stores [{desc}]")
        self._burst = []

    # ---------------------------------------------------------------- Helpers

    @staticmethod
    def _class_name(store) -> str:
        try:
            k = store.mClass
            if hasattr(k, "name"):
                return k.name
            return cGcInventoryClass(int(k)).name
        except Exception:
            return "?"

    def _is_player_store(self, addr: int) -> bool:
        if self._player_ranges is None:
            ps = gameData.player_state
            if ps is None:
                return False
            ranges = []
            for attr in (
                "mInventories",
                "mVehicleInventories",
                "mVehicleTechInventories",
                "mShipInventories",
                "mShipInventoriesCargo",
                "mShipInventoriesTechOnly",
            ):
                try:
                    arr = getattr(ps, attr)
                    base = get_addressof(arr)
                    ranges.append((base, base + ctypes.sizeof(arr)))
                except Exception:
                    continue
            if not ranges:
                return False
            self._player_ranges = ranges
            logger.info("Player inventory address ranges cached: " + ", ".join(f"0x{a:X}-0x{b:X}" for a, b in ranges))
        return any(lo <= addr < hi for lo, hi in self._player_ranges)

    def _announce(self, text: str):
        """Show a timed HUD message. Off by default: the AddTimedMessage argument list in NMS.py is
        annotated as unconfirmed since 4.13, so a wrong call can crash the game."""
        if not ENABLE_HUD_ANNOUNCE or not self.state.announce_in_game or not self._notifications_addr:
            return
        try:
            notif = map_struct(self._notifications_addr, nms.cGcPlayerNotifications)
            msg = basic.cTkFixedString[512](text)
            colour = basic.Colour(1.0, 1.0, 1.0, 1.0)
            icon = nms.cTkSmartResHandle()
            icon.miInternalHandle = -1
            notif.AddTimedMessage(
                lsMessage=ctypes.byref(msg),
                lfDisplayTime=6.0,
                lColour=ctypes.byref(colour),
                liAudioID=0,
                lIcon=ctypes.byref(icon),
                unknown=0,
                unknown2=0,
                lbShowMessageBackground=True,
                lbShowIconGlow=False,
            )
        except Exception as e:
            logger.error(f"In-game announce failed (disable the toggle if this repeats): {e}")
