from dataclasses import dataclass
import json
from random import Random
from typing import Any, Callable, TextIO, Tuple
from importlib.resources import files

from BaseClasses import CollectionState, Tutorial, ItemClassification, Item, Region, Location, Entrance
from worlds.AutoWorld import WebWorld, World
from .Data import item_table, location_table, rayman_item_name_to_id, create_rayman_location_names
from .Generator import GeneratorState
from .Layout import SubLevelInfo, Tech, LevelInfo, levels, extra_levels, rayman_location_hints, rayman_portal_ids
from .Options import create_option_groups, Rayman2Options
from .Tech import GLITCHES_ITEM_NAME, TechContext


def get_world_version() -> tuple[int, ...]:
    """Read the APWorld version from its package metadata."""
    metadata = json.loads(files(__package__).joinpath("archipelago.json").read_text(encoding="utf-8"))
    return tuple(int(part) for part in metadata.get("world_version", "0.0.0").split("."))


class Rayman2Item(Item):
    game: str = "Rayman 2"


class Rayman2Location(Location):
    game: str = "Rayman 2"


@dataclass
class Rayman2Lum:
    region: Region
    tech: Tech


class Rayman2Web(WebWorld):
    option_groups = create_option_groups()
    tutorials = [Tutorial(
        "Multiworld Setup Guide",
        "A guide for setting up Rayman 2 with Archipelago.",
        "English",
        "setup_en.md",
        "setup/en",
        ["Aeltumn"]
    )]


class Rayman2World(World):
    """
    Rayman 2 is a classic 3D platformer where Rayman sets out on an adventure to obtain four
    masks to defeat an army of Robo-Pirates attacking the Glade of Dreams.
    """

    game = "Rayman 2"
    web = Rayman2Web()
    topology_present = True

    item_name_to_id = rayman_item_name_to_id
    location_name_to_id = create_rayman_location_names()

    options_dataclass = Rayman2Options
    options: Rayman2Options

    # Universal Tracker integration. Slot data is used to rebuild the exact
    # generated room layout and the options which produced it.
    ut_can_gen_without_yaml = True
    glitches_item_name = GLITCHES_ITEM_NAME

    def __init__(self, multiworld, player):
        super(Rayman2World, self).__init__(multiworld, player)
        self.levelChains = {}
        self.portalEvents = {}
        self.sideTempleFinishEvent = "Finish plum_20"
        self.cobdFinishEvent = None
        self.bundle_lums = []
        self.bundle_tech = []
        self.bundle_regions = []
        self.last_tally = 0
        self.last_item_bundle = []
        self.last_tech_bundle = set()
        self.last_region_bundle = []

        # If not in lumsanity mode we have to determine the available lums
        # using accessible regions which means we have to check reachability
        # in entrance requirements on lum gate portals. So we disable this setting!
        # It'd be complicated to work around so we eat the performance cost for R2.
        self.explicit_indirect_conditions = False

    def generate_early(self) -> None:
        """Restore the host's options and randomized layout for UT's fake generation."""
        if not getattr(self.multiworld, "generation_is_fake", False):
            return

        passthrough = getattr(self.multiworld, "re_gen_passthrough", {})
        slot_data = passthrough.get(self.game, {})
        if not slot_data:
            return

        # Options that affect locations, access rules, or generated room layout
        # are serialized by fill_slot_data. Use Option.from_any so Choice,
        # Range, FreeText, and Toggle values are reconstructed consistently.
        for option_name, value in slot_data.get("options", {}).items():
            option = getattr(self.options, option_name, None)
            if option is not None:
                setattr(self.options, option_name, option.from_any(value))

        # The room randomizer is intentionally not rerun under UT. Reuse the
        # exact chain order and portal events selected by the host generation.
        self.levelChains = {
            chain_name: list(chain_rooms)
            for chain_name, chain_rooms in slot_data.get("level_chains", {}).items()
        }
        self.portalEvents = {
            int(portal): event
            for portal, event in slot_data.get("portal_finish_events", {}).items()
        }
        self.sideTempleFinishEvent = slot_data.get(
            "side_temple_finish_event", self.sideTempleFinishEvent
        )
        self.cobdFinishEvent = slot_data.get("cobd_finish_event", self.cobdFinishEvent)

    def can_obtain_lums(self, state: CollectionState, count):
        """Determines if [count] lums can be obtained using [state] based on the bundle lums list."""
        # Update reachable regions and determine currently available items
        if state.stale[self.player]:
            state.update_reachable_regions(self.player)
        reachable_regions = set(state.reachable_regions[self.player])
        available_items = state.prog_items[self.player].copy()

        # Early-exit if we already know the answer as we just checked for this state!
        if available_items == self.last_item_bundle and reachable_regions == self.last_region_bundle:
            return self.last_tally >= count

        # Determine the tech available based on this state
        context = TechContext(self.player, state, self.options, self.sideTempleFinishEvent, self.cobdFinishEvent)
        all_tech = set()
        for tech in self.bundle_tech:
            if context.has_tech(tech):
                all_tech.add(tech)

        # Early-exit if we already know the answer as we just checked for this tech!
        if reachable_regions == self.last_region_bundle and all_tech == self.last_tech_bundle:
            return self.last_tally >= count

        # Re-compute the tally
        tally = 0
        for lum in self.bundle_lums:
            if lum.tech in all_tech and lum.region in reachable_regions:
                tally += 1

        # Store the tally for next time
        self.last_tally = tally
        self.last_tech_bundle = all_tech
        self.last_item_bundle = available_items
        self.last_region_bundle = reachable_regions
        return tally >= count

    def apply_access_requirement(self, accessible, tech: Tech):
        """Applies the relevant access requirement to an accessible object."""
        if not tech.always_true:
            accessible.access_rule = lambda state, tech=tech: TechContext(self.player, state, self.options,
                                                                          self.sideTempleFinishEvent,
                                                                          self.cobdFinishEvent).has_tech(tech)

    def create_level(self, sublevels: dict[str, SubLevelInfo], levelChain: list[str]) -> Tuple[
        Region | None, Region | None]:
        """Creates a new level from a level info that can be entered from source."""
        if len(sublevels) == 0:
            return [None, None]

        firstRegion: Region | None = None
        lastRegion: Region | None = None
        for subLevelName, subLevelInfo in sublevels.items():
            # Create this level and connect it
            region = Region(subLevelName, self.player, self.multiworld)
            self.multiworld.regions.append(region)

            # Create an event for finishing this sub-region
            self.create_level_finish_event(region, subLevelInfo)

            # Add this level to the chain for this level
            levelChain.append(subLevelName)

            # Store the first and last regions
            if firstRegion is None:
                firstRegion = region
            lastRegion = region
        return [firstRegion, lastRegion]

    def create_level_finish_event(self, region: Region, levelInfo: SubLevelInfo) -> Location:
        """Creates an event for finishing this level."""
        event = self.create_event(region, f"Finish {region.name}")
        if levelInfo is not None:
            self.apply_access_requirement(event, levelInfo.exitRequirement)
        return event

    def create_event(self, region: Region, location_name: str, item_name: str = None) -> Location:
        """Creates a new generic event in the given region that requires finishing the given level."""
        if item_name is None:
            item_name = location_name
        event_location = Location(self.player, location_name, None, region)
        event_location.show_in_spoiler = False
        event_item = Item(item_name, ItemClassification.progression, None, self.player)
        event_location.place_locked_item(event_item)
        region.locations.append(event_location)
        return event_location

    def create_entrance_portal(self, source: Region, name: str, portals: int = 0, lum_gate: int | None = None,
                               require_all_masks: bool = False,
                               extra_rule: Callable[[CollectionState], bool] = None) -> Entrance:
        """Creates a new portal on the source which requires unlocking portals and possibly lum gates or masks but can be accessed itself without any requirements."""
        portal = source.create_exit(name)

        # Require the minimum amount of portals to be made previously so this one is reachable
        alreadyCheckedLums = False
        if portals > 0:
            if self.options.unlock_level_checks.value:
                if self.options.instant_portal_access.value:
                    unlockItem = rayman_location_hints[rayman_portal_ids[portals - 1]]
                    portal.access_rule = lambda state, unlockItem=unlockItem: state.has(unlockItem, self.player)
                else:
                    # Since we only unlock portals in order and here we don't check if you have earned
                    # the requirements to access each portal we have to re-check against the maximum
                    # lum gate you have encountered up until this point.
                    lumRequirement = 0
                    if portals >= 16:
                        lumRequirement = max(lumRequirement, self.options.fourth_gate_required.value)
                    if portals >= 14:
                        lumRequirement = max(lumRequirement, self.options.third_gate_required.value)
                    if portals >= 9:
                        lumRequirement = max(lumRequirement, self.options.second_gate_required.value)
                    if portals >= 5:
                        lumRequirement = max(lumRequirement, self.options.first_gate_required.value)

                    portal.access_rule = lambda state, portals=portals,lumRequirement=lumRequirement: (
                            self.has_portals_up_to(state,portals) and self.get_lums(state) >= lumRequirement)
                    alreadyCheckedLums = True
            else:
                # In instant portal mode we don't require anything!
                if self.options.instant_portal_access.value:
                    portal.access_rule = lambda state: True
                else:
                    portal.access_rule = lambda state, portals=portals: state.has(
                        self.portalEvents.get(portals, "Finish Unknown Level"),
                        self.player)

        # Determine the lum requirement to reach this portal
        if lum_gate is not None and not alreadyCheckedLums:
            # Determine the lum requirement based on when the last lum gate was
            lumRequirement = 0
            match lum_gate:
                case 0:
                    lumRequirement = self.options.first_gate_required.value
                case 1:
                    lumRequirement = self.options.second_gate_required.value
                case 2:
                    lumRequirement = self.options.third_gate_required.value
                case 3:
                    lumRequirement = self.options.fourth_gate_required.value
                case 4:
                    lumRequirement = self.options.walk_of_life_required.value
                case 5:
                    lumRequirement = self.options.walk_of_power_required.value

            base = portal.access_rule
            portal.access_rule = lambda state, base=base, lumRequirement=lumRequirement: self.get_lums(
                state) >= lumRequirement and base(state)

        # If this is a mask requiring level we add that as a requirement!
        if require_all_masks and self.options.end_goal != 4:
            base = portal.access_rule
            portal.access_rule = lambda state, base=base: (state.has("Water Mask", self.player) and \
                                                           state.has("Earth Mask", self.player) and \
                                                           state.has("Fire Mask", self.player) and \
                                                           state.has("Air Mask", self.player)) and \
                                                          base(state)

        # Add the extra rule for this portal if one is given.
        if extra_rule is not None:
            base = portal.access_rule
            portal.access_rule = lambda state, base=base, extra_rule=extra_rule: extra_rule(state) and base(state)

        return portal

    def has_portals_up_to(self, state: CollectionState, portals: int) -> bool:
        """Returns whether all portals up to and including the given portal are present."""
        for i in range(0, min(portals, 17)):
            if not state.has(rayman_location_hints[rayman_portal_ids[i]], self.player):
                return False
        return True

    def get_lums(self, state: CollectionState) -> int:
        """Returns the amount of lums currently within state. Accounts for accessible lums in non-lumsanity."""
        if self.options.lumsanity.value:
            bundleSize = self.options.lum_bundle_size.value
            leftoverBundleSize = 710 % self.options.lum_bundle_size.value
            return state.prog_items[self.player]["Lum"] + (bundleSize * state.prog_items[self.player]["Lum Bundle"]) + (
                    leftoverBundleSize * state.prog_items[self.player]["Leftover Lum Bundle"]) + (
                    5 * state.prog_items[self.player]["Super Lum"])
        else:
            # Start with all super lums you have
            lumCount = (5 * state.prog_items[self.player]["Super Lum"])

            # Crawl through all available levels
            allLevels: list[LevelInfo] = []
            allLevels += levels
            allLevels += extra_levels
            zones = []
            context = TechContext(self.player, state, self.options, self.sideTempleFinishEvent, self.cobdFinishEvent)
            for levelInfo in allLevels:
                for subLevelName, subLevelInfo in levelInfo.sublevels.items():
                    # If this region is reachable count all lums in the region, also check
                    # if they have the necessary tech to get the ones behind a requirement
                    if state.can_reach_region(subLevelName, self.player):
                        zones.append(subLevelName)
                        lumCount += len(subLevelInfo.checks.regularLums)

                        for tech, checks in subLevelInfo.behindRequirements.items():
                            if context.has_tech(tech):
                                lumCount += len(checks.regularLums)

            return lumCount

    def create_regions(self) -> None:
        """Creates all regions available in the game."""
        is_ut = getattr(self.multiworld, "generation_is_fake", False)
        # Start by creating the menu
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)

        # Go through all levels to create regions and items
        portal = 0
        last_lum_gate = None
        for levelInfo in levels:
            levelChain = []
            if levelInfo.chain is not None and not is_ut:
                self.levelChains[levelInfo.chain] = levelChain
            firstRegion, lastRegion = self.create_level(levelInfo.sublevels, levelChain)
            if firstRegion is None or lastRegion is None:
                continue

            # Create a portal for each level on the hall of doors
            portalsRequired = portal

            # Update the lum gate based on the last we saw
            if levelInfo.lumGate is not None:
                last_lum_gate = levelInfo.lumGate

            mapmonde_exit = self.create_entrance_portal(menu, f"Portal #{portal + 1}", portalsRequired, last_lum_gate,
                                                        levelInfo.requireAllMasks)
            portal += 1

            # Connect the portal automatically to non-randomised levels (Crow's Nest)
            if levelInfo.chain is None:
                mapmonde_exit.connect(firstRegion)

        # Go through the extra levels and create them separately
        for extraLevelInfo in extra_levels:
            last_level: Region = None
            extra_rule: Callable[[CollectionState], bool] = None
            match extraLevelInfo.displayName:
                case "The Fairy Glade #2 - Revisit":
                    last_level = self.get_region("cask_10")
                case "The Sanctuary of Stone and Fire - Side Temple":
                    last_level = self.get_region("plum_00")
                    tech = Tech("HOVER && (TECHNICAL || PURPLE_SWING)", "Stone and Fire 1 Swings")
                    extra_rule = lambda state, tech=tech: TechContext(self.player, state, self.options,
                                                                      self.sideTempleFinishEvent,
                                                                      self.cobdFinishEvent).has_tech(tech)
                case "The Cave of Bad Dreams":
                    last_level = self.get_region("Ski_10")
                    extra_rule = lambda state: state.has("Knowledge of the Cave of Bad Dreams", self.player)
                case "The Walk of Life":
                    last_level = self.get_region("chase_10")
                case "The Walk of Power":
                    last_level = self.get_region("earth_10")
                case _:
                    raise KeyError(f"Unknown extra level {extraLevelInfo.displayName}")

            # Create this level itself
            levelChain = []
            if extraLevelInfo.chain is not None and not is_ut:
                self.levelChains[extraLevelInfo.chain] = levelChain
            firstRegion, lastRegion = self.create_level(extraLevelInfo.sublevels, levelChain)
            if firstRegion is None:
                continue

            # Create an entrance in the source level
            self.create_entrance_portal(last_level, f"Portal to {firstRegion.name}", lum_gate=extraLevelInfo.lumGate,
                                        require_all_masks=extraLevelInfo.requireAllMasks, extra_rule=extra_rule)

        # Go through all location and create them
        for data in location_table:
            region = self.multiworld.get_region(data.region, self.player)

            # If not on lumsanity, don't shuffle in the lums!
            if data.itemName == "Lum" and not self.options.lumsanity.value:
                continue

            # If we're in bundling mode, group up the lums!
            if data.itemName == "Lum" and self.options.lum_bundle_size.value > 1:
                # In lum bundle mode we store all unplaced lums in a collection and create
                # items on the menu with combined requirements at the end.
                self.bundle_lums.append(Rayman2Lum(region, data.tech))

                # Track unique tech and regions to easily re-compute them
                if data.tech not in self.bundle_tech:
                    self.bundle_tech.append(data.tech)
                if region not in self.bundle_regions:
                    self.bundle_regions.append(region)
                continue

            # If we're not on fragmented mode we don't shuffle any locations marked
            # as being for the fragmented silver lums
            if not self.options.fragmented_silver_lums.value:
                if data.fragmented:
                    continue

            # Don't create portal items if level checks are off
            if self.options.unlock_level_checks.value != 1:
                if data.portalUnlocks > 0:
                    continue

            # Don't shuffle movement options that are not set to be randomised
            if not self.options.movement_hover:
                if data.itemName == "Hover":
                    continue
            if not self.options.movement_swim:
                if data.itemName == "Swim":
                    continue
            if not self.options.movement_ledge_grab:
                if data.itemName == "Ledge Grab":
                    continue
            if not self.options.movement_lava_hover:
                if data.itemName == "Lava Hover":
                    continue

            location = Rayman2Location(self.player, data.displayName, data.id, region)

            # Add an access rule based on the tech type and this region being accessible!
            if not data.tech.always_true:
                self.apply_access_requirement(location, data.tech)

            # If these require chain completion, add a custom requirement!
            if data.chainCompletion is not None:
                base = location.access_rule
                level_chain = data.chainCompletion
                location.access_rule = lambda state, base=base, level_chain=level_chain: base(state) and state.has(
                    f"Finish {self.levelChains.get(level_chain)[-1]}",
                    self.player)

            # Portals cannot be collected unless you finish the level chain that gives them and until you
            # have at least the last lum gate amount of lums
            if self.options.unlock_level_checks.value == 1:
                if data.portalUnlocks > 0:
                    base = location.access_rule
                    portals = data.portalUnlocks
                    location.access_rule = lambda state, base=base, portals=portals: base(state) and state.has(
                        self.portalEvents.get(portals, "Finish Unknown Level"),
                        self.player)

            # Add this location to this region
            region.locations.append(location)

        # Set the victory condition
        match self.options.end_goal.value:
            case 1:
                self.multiworld.completion_condition[self.player] = lambda state: state.has("Finish Rhop_10",
                                                                                            self.player)
            case 2:
                self.multiworld.completion_condition[self.player] = lambda state: state.has("Finish vulca_20",
                                                                                            self.player)
            case 3:
                self.multiworld.completion_condition[self.player] = lambda state: (
                        state.has("Finish Rhop_10", self.player) and
                        self.get_lums(state) >= 1000 and
                        state.prog_items[self.player]["Cage"] >= 80
                )
            case 4:
                self.multiworld.completion_condition[self.player] = lambda state: state.has("Finish Rhop_10",
                                                                                            self.player)
            case 5:
                self.multiworld.completion_condition[self.player] = lambda state: (
                        state.has("Finish Rhop_10", self.player) and
                        state.prog_items[self.player]["Cage"] >= 80
                )

        # Create lum bundles
        if self.options.lumsanity.value and self.options.lum_bundle_size.value > 1:
            total = len(self.bundle_lums)
            assert(total == 710)
            bundleSize = self.options.lum_bundle_size.value
            bundleCount = total // bundleSize
            leftoverBundleSize = total % bundleSize
            if bundleCount > 0:
                for i in range(0, bundleCount):
                    name = f"Lum Bundle #{i + 1}"
                    amount = bundleSize * (i + 1)
                    location = Rayman2Location(self.player, name, self.location_name_to_id[name], menu)
                    location.access_rule = lambda state, amount=amount: self.can_obtain_lums(state, amount)
                    menu.locations.append(location)
            if leftoverBundleSize > 0:
                location = Rayman2Location(self.player, "Leftover Lum Bundle",
                                           self.location_name_to_id["Leftover Lum Bundle"], menu)
                location.access_rule = lambda state, total=total: self.can_obtain_lums(state, total)
                menu.locations.append(location)

    def connect_levels(self):
        """Connects together regions based on the level chains set."""
        is_ut = getattr(self.multiworld, "generation_is_fake", False)
        # Create a mapping of all sub levels and their info
        subLevelsById = {}
        allLevels: list[LevelInfo] = []
        allLevels += levels
        allLevels += extra_levels
        for levelInfo in allLevels:
            for subLevelName, subLevelInfo in levelInfo.sublevels.items():
                subLevelsById[subLevelName] = subLevelInfo

        # Go through all level chains and hook them up
        mapmonde = self.get_region("Menu")
        portal = 0
        for chainId, chainLevels in self.levelChains.items():
            index = 0
            firstRegion: Region | None = None
            lastRegion: Region | None = None

            # If this is a revisit we have to connect up the room properly back to where
            # it originated from.
            isRevisit = chainId == "side_temple" or chainId == "fairy_glade_revisit"

            for subLevelName in chainLevels:
                index += 1

                # Find the data for this level
                subLevelInfo = subLevelsById[subLevelName]
                if subLevelInfo is None:
                    raise KeyError(f"Could not find sub level called {subLevelName}")

                # Find the region for this level
                region = self.get_region(subLevelName)

                # If this is the first region we connect it to the map portal, this gets skipped
                # on the revisits as they are at the end of the line and there's no portals left
                if index == 1:
                    portal += 1
                    portalName = f"Portal #{portal}"
                    mapmonde_exit = next(
                        (x for x in mapmonde.get_exits() if x.name == portalName and x.connected_region is None), None)
                    if mapmonde_exit is not None:
                        mapmonde_exit.connect(region)

                # If this is not the first level of this chain create a connection between
                if lastRegion is not None:
                    connection = f"{lastRegion.name} -> {region.name}"
                    exit = lastRegion.create_exit(connection)
                    exit.access_rule = lambda state, lastRegion=lastRegion: state.has(f"Finish {lastRegion.name}",
                                                                                      self.player)
                    exit.connect(region)

                # Create connections for EEC and reverse EEC
                if subLevelName == "learn_31" and (
                    self.options.glitched_early_echoing_caves.value or is_ut
                ):
                    eec_tech = Tech("EARLY_ECHOING_CAVES")
                    exit = self.create_entrance_portal(
                        region,
                        "EEC",
                        extra_rule=lambda state, tech=eec_tech: TechContext(
                            self.player, state, self.options, self.sideTempleFinishEvent,
                            self.cobdFinishEvent
                        ).has_tech(tech),
                    )
                    exit.connect(self.get_region("Learn_32"))

                if subLevelName == "Learn_32" and (
                    self.options.glitched_reverse_early_echoing_caves.value or is_ut
                ):
                    tech = Tech(
                        "REVERSE_EARLY_ECHOING_CAVES && HOVER && PURPLE_SWING",
                        "Fairy Glade Revisit Swing",
                    )
                    exit = self.create_entrance_portal(region, "Reverse EEC",
                                                       extra_rule=lambda state, tech=tech: TechContext(self.player,
                                                                                                       state,
                                                                                                       self.options,
                                                                                                       self.sideTempleFinishEvent,
                                                                                                       self.cobdFinishEvent).has_tech(
                                                           tech))
                    exit.connect(self.get_region("learn_31"))

                # If this region has a portal exit we link it up
                regionExits = list(region.get_exits())
                for regionExit in regionExits:
                    if regionExit.name.startswith("Portal to"):
                        target = None
                        match regionExit.name:
                            case "Portal to plum_20":
                                target = "side_temple"
                            case "Portal to Learn_32":
                                target = "fairy_glade_revisit"
                            case "Portal to vulca_10":
                                target = "cave_of_bad_dreams"
                            case "Portal to Ly_10":
                                target = "walk_of_life"
                            case "Portal to Ly_20":
                                target = "walk_of_power"
                            case _:
                                raise KeyError(f"Invalid exit name for side-level: {regionExit.name}")

                        # Connect this portal to the first level of the target chain
                        targetRegion = self.levelChains[target][0]
                        regionObj = self.get_region(targetRegion)
                        regionExit.connect(regionObj)

                # Store the first and last regions
                if firstRegion is None:
                    firstRegion = region
                lastRegion = region

            # If this is a revisit we have to hook up the end of the chain back to where it came from
            last_level: Region | None = None
            match chainId:
                case "side_temple":
                    last_level = self.get_region("plum_00")
                case "fairy_glade_revisit":
                    last_level = self.get_region("cask_10")
                case _:
                    continue

            # If this is the last of a revisit we need to hook it back up to where we came from
            if isRevisit:
                connection = f"{lastRegion.name} -> {last_level.name}"
                exit = lastRegion.create_exit(connection)
                exit.access_rule = lambda state, lastRegion=lastRegion: state.has(f"Finish {lastRegion.name}",
                                                                                  self.player)
                exit.connect(last_level)

    def connect_entrances(self) -> None:
        """Connect entrances of any disconnected regions in room randomisation mode."""
        # UT receives the actual generated room chains in slot data. Connect
        # those chains, but never run the custom room randomizer a second time.
        if getattr(self.multiworld, "generation_is_fake", False):
            self.connect_levels()
            return

        # If we're in room randomisation mode, generate the layout!
        if self.options.room_randomisation.value:
            # Run the custom generator
            random = Random(self.random.getrandbits(64))
            if self.options.room_randomisation_seed.value != "None" and self.options.room_randomisation_seed.value != "":
                random.seed(self.options.room_randomisation_seed.value)
            generator = GeneratorState(random, self.options.lumsanity.value, self.options.fixed_level_lengths.value)
            generator.assemble_initial_levels(self.options)
            generator.generate()

            # Connect up the map based on the generator's work, ignoring the base level chains
            self.levelChains = generator.levelChains
            self.sideTempleFinishEvent = generator.collected.sideTempleFinishEvent

        # Determine the portal events based on the level chains
        portalId = 0
        all_levels = []
        all_levels += levels
        all_levels += extra_levels
        for level in all_levels:
            portalId += 1

            if level.chain is None:
                # Set a portal event on the non-randomised levels as well!
                levelId = list(level.sublevels.keys())[-1]
                self.portalEvents[portalId] = f"Finish {levelId}"
                continue

            # Determine the contents of this chain
            output = self.levelChains[level.chain]

            # Store the finish events for the COBD or regular portals
            # into data so we can check against them for portal unlocks
            if level.chain == "cave_of_bad_dreams":
                self.cobdFinishEvent = f"Finish {output[-1]}"
            elif portalId <= 18:
                self.portalEvents[portalId] = f"Finish {output[-1]}"

        self.connect_levels()

    def create_item(self, item: str,
                    classification: ItemClassification = ItemClassification.progression) -> Rayman2Item:
        """Creates a new Rayman 2 item using the item id table."""
        if item == self.glitches_item_name:
            return Rayman2Item(item, ItemClassification.progression, None, self.player)
        return Rayman2Item(item, classification, self.item_name_to_id[item], self.player)

    def create_items(self):
        """Creates all items based on the item table."""
        itempool = []
        for item in item_table:
            # If not on lumsanity, don't shuffle in the lums!
            if item.displayName == "Lum" and (
                    not self.options.lumsanity.value or self.options.lum_bundle_size.value > 1):
                continue

            # If we're on fragmented mode we don't shuffle the silver lums, and outside fragmented
            # mode don't shuffle the fragmented items.
            if self.options.fragmented_silver_lums.value:
                if item.displayName == "Silver Lum":
                    continue
            else:
                if item.fragmented:
                    continue

            # Don't shuffle movement options that are not set to be randomised
            if self.options.movement_hover.value != 1:
                if item.displayName == "Hover":
                    continue
            if self.options.movement_swim.value != 1:
                if item.displayName == "Swim":
                    continue
            if self.options.movement_ledge_grab.value != 1:
                if item.displayName == "Ledge Grab":
                    continue
            if self.options.movement_lava_hover.value != 1:
                if item.displayName == "Lava Hover":
                    continue

            # Don't create portal items if level checks are off
            if self.options.unlock_level_checks.value != 1:
                if item.portalUnlocks > 0:
                    continue

            # Make an item filler based on the list of end goals it provided
            if self.options.end_goal.value in item.endGoals:
                itempool.append(self.create_item(item.displayName, item.progressionClassification))
            else:
                itempool.append(self.create_item(item.displayName, ItemClassification.filler))

        # Add lum bundle items with auto-generated ids
        if self.options.lumsanity.value and self.options.lum_bundle_size.value > 1:
            bundleCount = 710 // self.options.lum_bundle_size.value
            leftoverBundleSize = 710 % self.options.lum_bundle_size.value
            if bundleCount > 0:
                for i in range(0, bundleCount):
                    itempool.append(
                        self.create_item("Lum Bundle", ItemClassification.progression_deprioritized_skip_balancing))
            if leftoverBundleSize > 0:
                itempool.append(self.create_item("Leftover Lum Bundle",
                                                 ItemClassification.progression_deprioritized_skip_balancing))

        self.multiworld.itempool += itempool

    @staticmethod
    def interpret_slot_data(slot_data: dict[str, Any]) -> dict[str, Any]:
        """Ask UT to regenerate using the original slot data as passthrough."""
        return slot_data

    def get_filler_item_name(self) -> str:
        """There cannot be filler items!"""
        raise KeyError("Rayman 2 does not have filler items.")

    def fill_slot_data(self):
        """Includes all information needed by the game into the slot data."""
        slot_data = {
            "level_chains": self.levelChains,
            "lum_gates": [
                self.options.first_gate_required.value,
                self.options.second_gate_required.value,
                self.options.third_gate_required.value,
                self.options.fourth_gate_required.value,
                self.options.walk_of_life_required.value,
                self.options.walk_of_power_required.value
            ],
            "damage_link": self.options.damage_link.value,
            "automatic_movement": (
                    self.options.movement_hover.value +
                    2 * self.options.movement_swim.value +
                    4 * self.options.movement_ledge_grab.value +
                    8 * self.options.movement_lava_hover.value
            ),
            "fragmented_lums": self.options.fragmented_silver_lums.value,
            "death_link": self.options.death_link.value,
            "death_link_amnesty": self.options.death_link_amnesty.value,
            "better_level_portals": self.options.improved_portal_teleporting.value,
            "end_goal": self.options.end_goal.value,
            "room_randomisation": self.options.room_randomisation.value,
            "accessible_portals": self.options.instant_portal_access.value,
            "unlock_levels": self.options.unlock_level_checks.value,
            "lumsanity": self.options.lumsanity.value,
            "lum_bundle_size": self.options.lum_bundle_size.value,
            "portal_finish_events": self.portalEvents,
            "side_temple_finish_event": self.sideTempleFinishEvent,
            "cobd_finish_event": self.cobdFinishEvent,
        }

        # Store every game option that can affect generated locations, logic,
        # room order, or in-game behavior. UT starts without the player's YAML.
        slot_data["options"] = self.options.as_dict(
            "end_goal",
            "death_link",
            "death_link_amnesty",
            "damage_link",
            "room_randomisation",
            "room_randomisation_seed",
            "restrictive_room_randomisation",
            "lum_bundle_size",
            "lumsanity",
            "fragmented_silver_lums",
            "instant_portal_access",
            "unlock_level_checks",
            "fixed_level_lengths",
            "improved_portal_teleporting",
            "movement_hover",
            "movement_ledge_grab",
            "movement_swim",
            "movement_lava_hover",
            "glitched_early_echoing_caves",
            "glitched_technical_tricks",
            "glitched_jano_skip",
            "glitched_tornado_skip",
            "glitched_plum_wall_climb",
            "glitched_damage_boosts",
            "glitched_backwards_slide_jumps",
            "glitched_airswims",
            "glitched_cobd_skip",
            "glitched_kapoueh_skip",
            "glitched_reverse_early_echoing_caves",
            "glitched_ly_skip",
            "first_gate_required",
            "second_gate_required",
            "third_gate_required",
            "fourth_gate_required",
            "walk_of_life_required",
            "walk_of_power_required",
        )
        slot_data["world_version"] = list(get_world_version())
        return slot_data

    def write_spoiler(self, spoiler_handle: TextIO) -> None:
        """Includes decided level swaps in the spoiler file for debugging."""
        if not self.options.room_randomisation:
            return

        spoiler_handle.write(f"\n{self.player_name} (Rayman 2) level chains:\n")
        for id, chain in self.levelChains.items():
            spoiler_handle.write(f"{id}: {chain}\n")
