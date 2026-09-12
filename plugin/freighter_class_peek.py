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
from ctypes import _Pointer, c_bool, c_int, c_uint64
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
GENERATOR_FUNCTION_OFFSET = 0x4CBC80

# Alternative to typing into the GUI: put the "caller NMS+0x..." value here (e.g. 0x11F7A31), reload, and
# press "Find function start". The GUI field, if filled, takes precedence.
CALLER_ADDRESS_TO_RESOLVE = 0

# Step 3: the function at GENERATOR_FUNCTION_OFFSET turned out to be a cGcInventoryStore method
# (its `this` is the store). The AI ship component's own generator is its caller. With
# GENERATOR_FUNCTION_OFFSET set, the mod logs "store generator called from NMS+0x..."; resolve that
# address with the finder and put the function start here to hook the component-level generator and
# log the component pointer (`this`) and the seed it passes.
AI_GENERATOR_FUNCTION_OFFSET = 0  # 0x4CA440 crashed (stack args); the stack walk below replaces this hook

# Step 4: the function containing the "called from NMS+0x1703900" return address is the component's lazy
# inventory getter (it derives the seed from the component and calls the generator). Resolve that address
# with the finder and put the function start here; the mod logs its arguments and return value on the
# next scan. This is the function the peek-from-space will call.
GETTER_FUNCTION_OFFSET = 0

# Layer 1 heuristic: stores at least this many slots big are labelled "freighter-sized".
MIN_SLOTS_FOR_FREIGHTER = 20

# Record the address of the code that called cGcInventoryStore::Add (needed for caller-based
# calibration). pyMHF implements this by rewriting bytes inside minhook's trampoline for the hooked
# function; Add is called constantly, so this is the riskiest part of the mod. Off for the first run;
# turn on once the mod is known to load cleanly.
USE_GET_CALLER = True

# Hook cGcPlayerNotifications::AddTimedMessage so the mod can show HUD messages. NMS.py marks that
# function's argument list as unconfirmed since 4.13, and a hook re-calls the original with the
# declared arguments, so a wrong list can crash the game the first time it shows any timed message.
# Off by default; the pyMHF window shows the result regardless.
ENABLE_HUD_ANNOUNCE = False

# Delay before a newly touched store is inspected, so the generator has finished writing it.
SETTLE_SECONDS = 0.25

# Step 5, peek from space. These are the facts recorded from a natural visor scan on the current build
# (see docs/RESEARCH.md). The peek scans process memory for cGcAISpaceshipComponent instances (objects
# whose first qword is the component vtable), keeps the ones whose data says Type = Freighter, and calls the
# component's lazy inventory getter on each one whose store is still empty. That is exactly what a visor
# scan does, so the class it produces is the class you would get by scanning, and the game caches it.
COMPONENT_VTABLE_OFFSET = 0x4AE0EB8  # NMS+X of the vtable; 0 = use the value captured from a natural scan
COMPONENT_DATA_PTR_OFFSET = 0x28  # component+0x28 -> cGcAISpaceshipComponentData (Class +0x34, Type +0x38)
COMPONENT_STORE_OFFSET = 0x7D40  # general cGcInventoryStore embedded in the component (the getter returns it)
COMPONENT_TECH_STORE_OFFSET = 0x7F88  # technology store, for reference only
AI_SHIP_TYPE_FREIGHTER = 4  # GcAISpaceshipTypes: None, Pirate, Police, Trader, Freighter, PlayerSquadron, ...
COMPONENT_SNAPSHOT_SIZE = 0x8A60  # observed pool stride between components; bytes diffed around a natural getter call
# Offset of the ship's scene-node handle inside the component, once found (the natural-scan hook searches the
# component for the visor's target handle and logs candidate offsets). 0 = unknown. With it set, the peek
# skips components whose handle is 0 (pool slots that are not live ships) and prints the handle per ship.
COMPONENT_NODE_HANDLE_OFFSET = 0

# Register arguments seen on the getter during a natural scan: getter(this=component, a1, a2, a3). The mod
# records the live values the first time a visor scan runs in a session and replays those. Before that it
# falls back to these, which are last session's raw registers (a2 looked like a pointer into NMS.exe, so it
# may be stale after a relaunch); if the fallback crashes, scan one freighter first and peek after that.
GETTER_DEFAULT_ARGS = (0x670F00, 0x7FF76E042BB0, 0xF367)
PEEK_ALLOW_DEFAULT_GETTER_ARGS = True

# Memory-scan limits. Only committed, private, read-write regions are scanned; single regions larger than
# this are skipped (the component pool is far smaller). A full scan of a multi-GB process takes seconds and
# runs on a background thread; once a hit is found, later peeks scan only that region.
PEEK_SCAN_MAX_REGION_MB = 2048
PEEK_MIN_REGION_KB = 64
PEEK_KEY = "p"

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


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", ctypes.c_uint32), ("PartitionId", ctypes.c_uint16),
                ("RegionSize", ctypes.c_uint64), ("State", ctypes.c_uint32),
                ("Protect", ctypes.c_uint32), ("Type", ctypes.c_uint32)]


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
    peek_result: str = "(no peek yet)"


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
        self._freighter_component = 0
        self._component_vtable = 0
        self._store_offset_in_component = 0
        # Peek-from-space state
        self._getter_args: Optional[tuple[int, int, int]] = None  # captured from a natural visor scan
        self._getter_addr_auto = 0  # function start resolved from the AI generator's caller, if hooked
        self._peek_queue: deque[int] = deque()  # component addresses waiting for a getter call (game thread)
        self._peek_lines: deque[str] = deque(maxlen=12)
        self._peek_running = False
        self._component_region: Optional[tuple[int, int]] = None  # (base, size) of the region holding components
        self._component_snapshot: Optional[tuple[int, Optional[bytes]]] = None  # (component, bytes before the getter)
        self._last_seed_bytes: bytes = b""  # seed passed to the store generator on its last call

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
    @STRING("Peek results (freighter classes)", readonly=True, multiline=True, height=160)
    def peek_result(self) -> str:
        return "\n".join(reversed(self._peek_lines)) or self.state.peek_result

    @peek_result.setter
    def peek_result(self, value: str):
        pass

    @gui_button("Peek freighter classes from space (memory scan + generate)")
    def peek_freighters(self):
        self._start_peek(full=False)

    @gui_button("Peek: full memory rescan")
    def peek_freighters_full(self):
        self._start_peek(full=True)

    @on_key_pressed(PEEK_KEY)
    def key_peek(self):
        self._start_peek(full=False)

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
        candidates = self._function_start_candidates(rel)
        if not candidates:
            logger.error("No 0xCC padding found within 128 KB below that address; use a disassembler.")
            return
        for c in candidates:
            head = ctypes.string_at(BASE_ADDRESS + c, 12).hex(" ").upper()
            logger.info(f"function start candidate: NMS+0x{c:X}  (0x{rel - c:X} bytes before the caller)  bytes: {head}")
        logger.info(f"Put the nearest candidate into GENERATOR_FUNCTION_OFFSET (e.g. 0x{candidates[0]:X}) and reload.")

    @staticmethod
    def _function_start_candidates(rel: int, limit: int = 3) -> list[int]:
        """Nearest 16-aligned bytes following a run of 0xCC padding below NMS+rel, nearest first."""
        try:
            span = 0x20000
            start = max(0, rel - span)
            buf = ctypes.string_at(BASE_ADDRESS + start, rel - start)
        except Exception as e:
            logger.error(f"Cannot read memory below NMS+0x{rel:X}: {e}")
            return []
        candidates: list[int] = []
        i = len(buf) - 1
        while i > 2 and len(candidates) < limit:
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
        return candidates

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

        @get_caller
        @manual_hook(
            "StoreGenerateInventory",
            offset=GENERATOR_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=c_uint64, argtypes=[c_uint64] * 8),  # 4 register + 4 stack slots, passed through
            detour_time="before",
        )
        def _generator_before(self, this, lpSeed, liArg, lbArg, *_stack):
            self._in_generate = True
            self._generate_component = int(this)
            if self._in_visor_scan and self._freighter_component and not self._store_offset_in_component:
                off = int(this) - self._freighter_component
                if 0 < off < 0x20000:
                    self._store_offset_in_component = off
                    logger.info(f"general store offset inside the component = 0x{off:X}")
            try:
                caller = self._generator_before.caller_address()
                logger.info(f"store generator called from NMS+0x{caller:X} (resolve this to hook the component generator)")
            except Exception:
                caller = 0
            if self._in_visor_scan and caller:
                try:
                    chain = self._stack_return_addresses(caller)
                    logger.info("call chain during scan, innermost first: " + ", ".join(f"NMS+0x{a:X}" for a in chain[:12]))
                    logger.info("resolve the 2nd/3rd entries with the finder; the component generator is one of them")
                except Exception as e:
                    logger.error(f"stack walk failed: {e}")
            arg32 = int(liArg) & 0xFFFFFFFF
            flag8 = int(lbArg) & 0xFF
            self._generated_components.append((time.time(), int(this), arg32, flag8))
            seed_txt = ""
            try:
                if int(lpSeed) > 0x10000:
                    self._last_seed_bytes = ctypes.string_at(int(lpSeed), 16)
                    seed_txt = " seed bytes=" + self._last_seed_bytes.hex(" ").upper()
            except Exception:
                pass
            logger.info(
                f"generator(this=0x{int(this):X}, seed*=0x{int(lpSeed):X}, arg={arg32} (raw 0x{int(liArg):X}), "
                f"flag={flag8} (raw 0x{int(lbArg):X})) visor_scan={self._in_visor_scan}{seed_txt}"
            )

        @manual_hook(
            "StoreGenerateInventory",
            offset=GENERATOR_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=c_uint64, argtypes=[c_uint64] * 8),  # 4 register + 4 stack slots, passed through
            detour_time="after",
        )
        def _generator_after(self, this, lpSeed, liArg, lbArg, *_stack):
            self._in_generate = False
            try:
                store = map_struct(int(this), nms.cGcInventoryStore)
                logger.info(f"store generator done: store 0x{int(this):X} now class {self._class_name(store)} "
                            f"{int(store.miWidth)}x{int(store.miHeight)}")
            except Exception:
                pass

    if AI_GENERATOR_FUNCTION_OFFSET:

        @get_caller
        @manual_hook(
            "AIShipComponentGenerateInventory",
            offset=AI_GENERATOR_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=c_uint64, argtypes=[c_uint64] * 8),  # 4 register + 4 stack slots, passed through
            detour_time="before",
        )
        def _ai_generator_before(self, this, a1, a2, a3, *_stack):
            self._generated_components.append((time.time(), int(this), int(a2) & 0xFFFFFFFF, int(a3) & 0xFF))
            extra = ""
            try:
                if int(a1) > 0x10000:
                    extra = " a1 bytes=" + ctypes.string_at(int(a1), 16).hex(" ").upper()
            except Exception:
                pass
            caller = 0
            try:
                caller = self._ai_generator_before.caller_address()
                extra += f" called from NMS+0x{caller:X}"
            except Exception:
                pass
            t = int(this)
            if caller and self._in_visor_scan and not GETTER_FUNCTION_OFFSET and not self._getter_addr_auto:
                cands = self._function_start_candidates(caller)
                if cands:
                    self._getter_addr_auto = cands[0]
                    logger.info(f"getter function start resolved automatically: NMS+0x{cands[0]:X} "
                                "(the peek will call this; set GETTER_FUNCTION_OFFSET to pin it)")
            if self._is_player_store(t):
                kind = "= PLAYER-STATE store (not the freighter path; ignore unless visor_scan=True)"
            elif t in self._known_stores or t in self._pending:
                kind = "= a known NPC STORE, climb one more level"
            else:
                kind = "(not a store we know of: component candidate)"
            logger.info(
                f"upper generator(this=0x{t:X} {kind}, a1=0x{int(a1):X}, a2=0x{int(a2):X}, a3=0x{int(a3):X}) "
                f"visor_scan={self._in_visor_scan}{extra}"
            )
            if self._in_visor_scan and "component" in kind:
                self._freighter_component = t
                self._analyse_component(t)

    if GETTER_FUNCTION_OFFSET:

        @manual_hook(
            "AIShipComponentGetInventory",
            offset=GETTER_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=c_uint64, argtypes=[c_uint64] * 8),
            detour_time="before",
        )
        def _getter_before(self, this, a1, a2, a3, *_stack):
            logger.info(
                f"getter(this=0x{int(this):X}, a1=0x{int(a1):X}, a2=0x{int(a2):X}, a3=0x{int(a3):X}) "
                f"visor_scan={self._in_visor_scan}"
            )
            if self._in_visor_scan and self._getter_args is None:
                self._getter_args = (int(a1), int(a2), int(a3))
                logger.info("getter arguments captured from the natural scan; the peek will replay them")
            if self._in_visor_scan:
                try:
                    pre = self._classify_component(int(this))
                    if pre:
                        logger.info(f"getter pre-state: store {self._store_desc(pre)} hangar='{pre['hangar']}'")
                    self._component_snapshot = (int(this), self._read(int(this), COMPONENT_SNAPSHOT_SIZE))
                    snap = self._component_snapshot[1]
                    if snap and self._last_scan_node:
                        import struct as _struct
                        offs = []
                        for delta in (0, 1, -1):
                            pat = _struct.pack("<I", (self._last_scan_node + delta) & 0xFFFFFFFF)
                            i = snap.find(pat)
                            while i != -1:
                                if i % 4 == 0:
                                    offs.append(f"+0x{i:X}(handle{delta:+d})" if delta else f"+0x{i:X}")
                                i = snap.find(pat, i + 1)
                        logger.info(f"node handle {self._last_scan_node} found in the component at: "
                                    f"{', '.join(offs) if offs else 'nowhere (stored differently)'}; "
                                    "put a plain '+0x...' offset into COMPONENT_NODE_HANDLE_OFFSET")
                except Exception as e:
                    logger.debug(f"pre-state read failed: {e}")

        @manual_hook(
            "AIShipComponentGetInventory",
            offset=GETTER_FUNCTION_OFFSET,
            func_def=FUNCDEF(restype=c_uint64, argtypes=[c_uint64] * 8),
            detour_time="after",
        )
        def _getter_after(self, this, a1, a2, a3, *_stack, _result_=None):
            try:
                r = int(_result_) if _result_ is not None else 0
            except Exception:
                r = 0
            note = ""
            if r and (r in self._known_stores or r in self._pending or abs(r - int(this)) < 0x20000):
                note = " (looks like a store pointer inside the component)"
                if self._in_visor_scan:
                    self._freighter_component = int(this)
                    if not self._component_vtable:
                        self._analyse_component(int(this))
            logger.info(f"getter returned 0x{r:X}{note}")
            if self._in_visor_scan:
                self._log_component_changes(int(this))

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
        if self._peek_queue:
            self._run_peek_queue()
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

    # ---------------------------------------------------------------- Component analysis

    def _analyse_component(self, comp: int):
        """Log what identifies this component: its vtable (shared by all AI ship components) and the
        offset of the pointer to its cGcAISpaceshipComponentData (Type at +0x38, Freighter = 4)."""
        img_lo, img_hi = BASE_ADDRESS, BASE_ADDRESS + SIZE_OF_IMAGE
        try:
            vt = int.from_bytes(ctypes.string_at(comp, 8), "little")
            if img_lo <= vt < img_hi:
                self._component_vtable = vt
                logger.info(f"component vtable = NMS+0x{vt - BASE_ADDRESS:X} (identifies cGcAISpaceshipComponent instances)")
            else:
                logger.info(f"component first qword 0x{vt:X} is not inside NMS.exe (no vtable at +0)")
        except Exception as e:
            logger.error(f"cannot read component: {e}")
            return
        import struct as _struct
        try:
            head = ctypes.string_at(comp, 0x400)
        except Exception as e:
            logger.error(f"cannot read component head: {e}")
            return
        found = 0
        for off in range(0, 0x400 - 8, 8):
            (p,) = _struct.unpack_from("<Q", head, off)
            if p < 0x10000 or p > 0x7FFFFFFFFFFF:
                continue
            try:
                blk = ctypes.string_at(p, 0x40)
            except Exception:
                continue
            cls, typ = _struct.unpack_from("<II", blk, 0x34)
            if typ == 4 and cls == 0:
                ident = blk[0x20:0x30].split(b"\x00")[0]
                try:
                    ident_s = ident.decode("ascii")
                except Exception:
                    ident_s = ident.hex()
                logger.info(
                    f"component data pointer candidate at component+0x{off:X} -> 0x{p:X}: Type=4 (Freighter), "
                    f"Class=0, CombatDefinitionID='{ident_s}'"
                )
                found += 1
        if not found:
            logger.info("no cGcAISpaceshipComponentData pointer found in the first 0x400 bytes with Type=4/Class=0; "
                        "will try a wider window next time")

    # ---------------------------------------------------------------- Peek from space

    def _start_peek(self, full: bool):
        """Entry point for the button and the key. Runs the memory scan on a background thread (the GUI
        and keyboard threads must not block, and the scan must not touch the game thread); getter calls
        are queued for the game thread and executed one per frame by _process_pending."""
        if self._peek_running:
            logger.info("peek already running")
            return
        if not (GETTER_FUNCTION_OFFSET or self._getter_addr_auto):
            logger.error("No getter address: set GETTER_FUNCTION_OFFSET (or hook the AI generator and scan one "
                         "freighter so it resolves automatically)")
            return
        if self._getter_args is None and not PEEK_ALLOW_DEFAULT_GETTER_ARGS:
            logger.error("Getter arguments not captured yet: visor-scan one freighter first, then peek")
            return
        import threading
        self._peek_running = True
        threading.Thread(target=self._peek_scan, args=(full,), name="FreighterPeekScan", daemon=True).start()

    # -- raw memory helpers (ReadProcessMemory on our own process: a bad address fails instead of faulting)

    def _k32(self):
        k32 = ctypes.windll.kernel32
        k32.VirtualQuery.restype = ctypes.c_size_t
        k32.VirtualQuery.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
        k32.ReadProcessMemory.restype = ctypes.c_int
        k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t,
                                          ctypes.POINTER(ctypes.c_size_t)]
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        return k32

    def _read(self, addr: int, size: int) -> Optional[bytes]:
        if addr < 0x10000 or addr > 0x7FFFFFFFFFFF:
            return None
        k32 = self._k32()
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(k32.GetCurrentProcess(), addr, buf, size, ctypes.byref(got)) or got.value != size:
            return None
        return buf.raw

    def _query_region(self, addr: int) -> Optional[tuple[int, int, int, int, int]]:
        mbi = MEMORY_BASIC_INFORMATION()
        if not self._k32().VirtualQuery(addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            return None
        return mbi.BaseAddress, mbi.RegionSize, mbi.State, mbi.Protect, mbi.Type

    def _scannable_regions(self) -> list[tuple[int, int]]:
        """Committed, private, plain read-write regions (the heap), excluding NMS.exe itself."""
        out = []
        addr = 0x10000
        img_lo, img_hi = BASE_ADDRESS, BASE_ADDRESS + SIZE_OF_IMAGE
        while addr < 0x7FFFFFFF0000:
            q = self._query_region(addr)
            if q is None:
                break
            base, size, state, protect, typ = q
            if (state == 0x1000 and typ == 0x20000 and protect == 0x04  # MEM_COMMIT, MEM_PRIVATE, PAGE_READWRITE
                    and PEEK_MIN_REGION_KB * 1024 <= size <= PEEK_SCAN_MAX_REGION_MB * 1024 * 1024
                    and not (base < img_hi and base + size > img_lo)):
                out.append((base, size))
            addr = base + size
        return out

    def _scan_region(self, base: int, size: int, needle: bytes) -> list[int]:
        k32 = self._k32()
        proc = k32.GetCurrentProcess()
        chunk = 8 << 20
        overlap = len(needle) - 1
        buf = ctypes.create_string_buffer(chunk + overlap)
        got = ctypes.c_size_t(0)
        hits: list[int] = []
        seen: set[int] = set()
        off = 0
        while off < size:
            n = min(chunk + overlap, size - off)
            if not k32.ReadProcessMemory(proc, base + off, buf, n, ctypes.byref(got)) or not got.value:
                off += chunk
                continue
            data = buf.raw[: got.value]
            i = data.find(needle)
            while i != -1:
                a = base + off + i
                if a % 8 == 0 and a not in seen:
                    seen.add(a)
                    hits.append(a)
                i = data.find(needle, i + 1)
            off += chunk
        return hits

    def _classify_component(self, comp: int) -> Optional[dict]:
        """Read the fields the peek relies on. Returns None if the object does not look like a component."""
        import struct as _struct
        head = self._read(comp, COMPONENT_DATA_PTR_OFFSET + 8)
        if head is None:
            return None
        (data_ptr,) = _struct.unpack_from("<Q", head, COMPONENT_DATA_PTR_OFFSET)
        blk = self._read(data_ptr, 0x40)
        if blk is None:
            return None
        cls, typ = _struct.unpack_from("<II", blk, 0x34)
        if typ > 16 or cls > 16:
            return None
        store = comp + COMPONENT_STORE_OFFSET
        sblk = self._read(store, 0x104)
        if sblk is None:
            return None
        w, h = _struct.unpack_from("<hh", sblk, 0x80)
        (items,) = _struct.unpack_from("<I", sblk, 0x8C)  # TkStd::tk_vector: allocated at +0, size at +4, ptr at +8
        (klass,) = _struct.unpack_from("<I", sblk, 0x100)
        # cGcAISpaceshipComponentData.Hangar is a cTkModelResource at +0: Filename is a VariableSizeString
        # (pointer, length). Capital freighters have a hangar model; the small fleet freighters do not.
        hangar = ""
        h_ptr, h_len = _struct.unpack_from("<QI", blk, 0)
        if 0 < h_len < 0x200:
            raw = self._read(h_ptr, h_len)
            if raw:
                hangar = raw.split(b"\x00")[0].decode("ascii", errors="replace")
        (res_handle,) = _struct.unpack_from("<i", blk, 0x18)
        node = -1
        if COMPONENT_NODE_HANDLE_OFFSET:
            nb = self._read(comp + COMPONENT_NODE_HANDLE_OFFSET, 4)
            if nb:
                (node,) = _struct.unpack_from("<I", nb, 0)
        return {"comp": comp, "data": data_ptr, "type": typ, "class_data": cls, "w": w, "h": h, "items": items,
                "klass": klass, "hangar": hangar, "res_handle": res_handle, "node": node,
                "hangar_raw": blk[:0x20].hex(), "store_raw": sblk[0x88:0x98].hex()}

    @staticmethod
    def _store_desc(c: dict) -> str:
        try:
            k = cGcInventoryClass(c["klass"]).name
        except Exception:
            k = f"?{c['klass']}"
        cap = " hangar" if c.get("hangar") or c.get("res_handle", 0) not in (0, -1) else ""
        node = f" node={c['node']}" if c.get("node", -1) >= 0 else ""
        return f"class {k} {c['w']}x{c['h']} items={c['items']}{cap}{node}"

    def _peek_scan(self, full: bool):
        try:
            self._peek_scan_inner(full)
        except Exception as e:
            logger.error(f"peek scan failed: {e!r}")
        finally:
            self._peek_running = False

    def _peek_scan_inner(self, full: bool):
        vt_abs = (BASE_ADDRESS + COMPONENT_VTABLE_OFFSET) if COMPONENT_VTABLE_OFFSET else self._component_vtable
        if not vt_abs:
            logger.error("component vtable unknown: set COMPONENT_VTABLE_OFFSET or visor-scan one freighter first")
            return
        needle = vt_abs.to_bytes(8, "little")

        regions: list[tuple[int, int]] = []
        if not full:
            seeds = [a for a in (self._freighter_component,) if a]
            if self._component_region:
                seeds.insert(0, self._component_region[0])
            for a in seeds:
                q = self._query_region(a)
                if q and q[2] == 0x1000:
                    regions.append((q[0], q[1]))
                    break
        scope = "known region" if regions else "full heap"
        if not regions:
            regions = self._scannable_regions()
        total = sum(sz for _, sz in regions)
        logger.info(f"peek: scanning {len(regions)} region(s), {total / (1 << 20):.0f} MB ({scope}) for vtable "
                    f"NMS+0x{vt_abs - BASE_ADDRESS:X} ...")
        t0 = time.time()
        hits: list[int] = []
        for base, size in regions:
            hits.extend(self._scan_region(base, size, needle))
        logger.info(f"peek: {len(hits)} vtable hit(s) in {time.time() - t0:.1f} s")
        if not hits and not full and scope == "known region":
            logger.info("peek: nothing in the known region, falling back to a full scan")
            self._component_region = None
            self._peek_scan_inner(True)
            return

        comps = [c for c in (self._classify_component(h) for h in hits) if c]
        by_type: dict[int, int] = {}
        for c in comps:
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
        type_names = {0: "None", 1: "Pirate", 2: "Police", 3: "Trader", 4: "Freighter", 5: "PlayerSquadron",
                      6: "DefenceForce", 7: "SwarmDrone"}
        logger.info("peek: components by type: " + ", ".join(
            f"{type_names.get(t, t)}={n}" for t, n in sorted(by_type.items())) if comps else "peek: no components")
        if comps and not self._component_region:
            q = self._query_region(comps[0]["comp"])
            if q:
                self._component_region = (q[0], q[1])
                logger.info(f"peek: component region 0x{q[0]:X} ({q[1] >> 10} KB) remembered for later peeks")

        freighters = [c for c in comps if c["type"] == AI_SHIP_TYPE_FREIGHTER]
        if not freighters:
            self._peek_lines.append(f"{time.strftime('%H:%M:%S')}  no freighter components in memory")
            return
        queued = 0
        groups: dict[int, int] = {}
        for c in freighters:
            groups[c["data"]] = groups.get(c["data"], 0) + 1
        logger.info("peek: freighter components by data pointer (one entity definition each): " + ", ".join(
            f"0x{d:X} x{n}" for d, n in sorted(groups.items(), key=lambda kv: kv[1])))
        freighters.sort(key=lambda c: (groups[c["data"]], -c["node"]))  # rarest definition (capital?) first
        for c in freighters:
            logger.info(f"peek: freighter comp 0x{c['comp']:X} data 0x{c['data']:X} store {self._store_desc(c)} "
                        f"hangar='{c['hangar']}' res={c['res_handle']} data[0:0x20]={c['hangar_raw']} "
                        f"mStore[0x88:0x98]={c['store_raw']}")
            if COMPONENT_NODE_HANDLE_OFFSET and c["node"] == 0:
                logger.info(f"peek: comp 0x{c['comp']:X} has node handle 0 (not a live ship); skipped")
                continue
            if c["items"] > 0 or c["w"] * c["h"] > 1:
                # A store with items has been generated (the getter is idempotent: repeated visor scans of the
                # same ship never regenerated). Dimensions are not a valid test: the constructed, not yet
                # generated store already reads 1x1 with a non-C class value.
                line = f"{time.strftime('%H:%M:%S')}  freighter comp 0x{c['comp']:X}: already generated, {self._store_desc(c)}"
                self._peek_lines.append(line)
                logger.info(line)
            else:
                self._peek_queue.append(c["comp"])
                queued += 1
        if queued:
            logger.info(f"peek: {queued} ungenerated freighter inventor{'y' if queued == 1 else 'ies'} queued; "
                        "the getter runs on the game thread over the next frames")

    def _log_component_changes(self, comp: int):
        """Diff the component against the snapshot taken before the natural getter call and report the
        offsets that changed outside the two stores: candidates for the 'inventory generated' flag."""
        snap = self._component_snapshot
        self._component_snapshot = None
        if not snap or snap[0] != comp or snap[1] is None:
            return
        before = snap[1]
        after = self._read(comp, len(before))
        if after is None:
            return
        skip = [(COMPONENT_STORE_OFFSET, COMPONENT_STORE_OFFSET + 0x248),
                (COMPONENT_TECH_STORE_OFFSET, COMPONENT_TECH_STORE_OFFSET + 0x248)]
        ranges: list[list[int]] = []
        for i in range(len(before)):
            if before[i] == after[i] or any(lo <= i < hi for lo, hi in skip):
                continue
            if ranges and i <= ranges[-1][1] + 1:
                ranges[-1][1] = i
            else:
                ranges.append([i, i])
        if not ranges:
            logger.info("component bytes outside the stores did not change across the getter call")
            return
        desc = []
        for lo, hi in ranges[:24]:
            a, b = lo & ~7, min(len(before), (hi | 7) + 1)
            desc.append(f"+0x{lo:X}..0x{hi:X}: {before[a:b].hex()} -> {after[a:b].hex()}")
        logger.info(f"component changed at {len(ranges)} place(s) outside the stores across the getter call "
                    "(generated-flag candidates): " + "; ".join(desc))

    def _run_peek_queue(self):
        """Game thread only (called from the main-loop hook). One getter call per frame."""
        comp = self._peek_queue.popleft()
        off = GETTER_FUNCTION_OFFSET or self._getter_addr_auto
        args = self._getter_args or (GETTER_DEFAULT_ARGS if PEEK_ALLOW_DEFAULT_GETTER_ARGS else None)
        if not off or args is None:
            logger.error("peek: getter address or arguments unavailable; queue cleared")
            self._peek_queue.clear()
            return
        info = self._classify_component(comp)
        if info is None or info["type"] != AI_SHIP_TYPE_FREIGHTER:
            logger.info(f"peek: component 0x{comp:X} vanished or changed since the scan; skipped")
            return
        src = "captured" if self._getter_args else "default"
        logger.info(f"peek: calling getter NMS+0x{off:X}(0x{comp:X}, {', '.join(f'0x{a:X}' for a in args)}) [{src} args]")
        try:
            fn = ctypes.CFUNCTYPE(c_uint64, c_uint64, c_uint64, c_uint64, c_uint64)(BASE_ADDRESS + off)
            ret = int(fn(comp, *args))
        except Exception as e:
            logger.error(f"peek: getter call failed: {e!r}; queue cleared")
            self._peek_queue.clear()
            return
        store_addr = ret if abs(ret - comp) < 0x20000 else comp + COMPONENT_STORE_OFFSET
        try:
            store = map_struct(store_addr, nms.cGcInventoryStore)
            klass = self._class_name(store)
            w, h = int(store.miWidth), int(store.miHeight)
        except Exception as e:
            logger.error(f"peek: cannot read store 0x{store_addr:X}: {e}")
            return
        after = self._classify_component(comp) or {}
        node = f" node={after['node']}" if after.get("node", -1) >= 0 else ""
        seed = f" seed={self._last_seed_bytes[:8].hex()}" if self._last_seed_bytes else ""
        line = (f"{time.strftime('%H:%M:%S')}  freighter comp 0x{comp:X} data 0x{after.get('data', 0):X}: "
                f"class {klass} {w}x{h}{node}{seed} (generated by peek)")
        if w * h <= 1:
            logger.warning("peek: the getter returned but the store is still 1x1; the call did not generate "
                           "(wrong getter, wrong arguments, or this component is not a live ship)")
        self._peek_lines.append(line)
        self.state.peek_result = line
        self.state.last_freighter = f"class {klass}  ({w}x{h}={w * h} cells, peek)"
        logger.info("PEEK RESULT: " + line)
        self._announce(f"Freighter class {klass}")

    # ---------------------------------------------------------------- Stack walk

    def _stack_return_addresses(self, anchor_rel: int, max_frames: int = 16) -> list[int]:
        """Recover the live call chain outward from a known return address.

        Scans the thread's committed stack for qwords that point into NMS.exe right after a CALL
        instruction. Dead frames below the current stack pointer also contain such values, so the
        known immediate caller (`anchor_rel`, from pyMHF's caller capture) is used as the anchor: its
        highest occurrence is the live frame, and the matching values above it, in ascending address
        order, are the outer frames. Windows only (uses kernel32)."""
        k32 = ctypes.windll.kernel32
        lo, hi = ctypes.c_uint64(), ctypes.c_uint64()
        k32.GetCurrentThreadStackLimits(ctypes.byref(lo), ctypes.byref(hi))

        k32.VirtualQuery.restype = ctypes.c_size_t
        k32.VirtualQuery.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
        img_lo, img_hi = BASE_ADDRESS, BASE_ADDRESS + SIZE_OF_IMAGE
        anchor_abs = BASE_ADDRESS + anchor_rel
        import struct as _struct

        def is_call_site(q: int) -> bool:
            try:
                pre = ctypes.string_at(q - 7, 7)
            except Exception:
                return False
            return (
                pre[2] == 0xE8                                                  # call rel32
                or (pre[1] == 0xFF and pre[2] == 0x15)                          # call [rip+rel32]
                or (pre[5] == 0xFF and 0xD0 <= pre[6] <= 0xD7)                  # call reg
                or (pre[4] == 0x41 and pre[5] == 0xFF and 0xD0 <= pre[6] <= 0xD7)  # call r8..r15
                or (pre[4] == 0xFF and pre[5] in (0x50, 0x51, 0x52, 0x53, 0x55, 0x56, 0x57))  # call [reg+disp8]
                or (pre[1] == 0xFF and pre[2] in (0x90, 0x91, 0x92, 0x93, 0x95, 0x96, 0x97))  # call [reg+disp32]
            )

        hits: list[tuple[int, int]] = []  # (stack address, value)
        addr = lo.value
        while addr < hi.value:
            mbi = MEMORY_BASIC_INFORMATION()
            if not k32.VirtualQuery(addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            end = min(mbi.BaseAddress + mbi.RegionSize, hi.value)
            committed = mbi.State == 0x1000 and not (mbi.Protect & 0x100) and (mbi.Protect & 0x04)
            if committed:
                try:
                    buf = ctypes.string_at(addr, end - addr)
                    for i, (q,) in enumerate(_struct.iter_unpack("<Q", buf[: len(buf) - len(buf) % 8])):
                        if img_lo <= q < img_hi:
                            hits.append((addr + 8 * i, q))
                except Exception:
                    pass
            addr = end

        anchors = [sa for sa, v in hits if v == anchor_abs]
        if not anchors:
            logger.error(f"anchor NMS+0x{anchor_rel:X} not found on the stack; chain unavailable")
            return []
        live = max(anchors)
        chain = [anchor_rel]
        for sa, v in hits:
            if sa > live and is_call_site(v):
                rel = v - BASE_ADDRESS
                if chain[-1] != rel:
                    chain.append(rel)
                if len(chain) >= max_frames:
                    break
        return chain

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
