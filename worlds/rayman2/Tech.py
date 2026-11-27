from dataclasses import dataclass, field, asdict
from functools import cached_property
import re

from BaseClasses import CollectionState
from worlds.rayman2.Options import Rayman2Options

REGEX = re.compile(r"\s*(\(|\)|&&|\|\||[A-Z_]+)")

GLITCHES_ITEM_NAME = "Rayman 2 Glitches"


class Parser:
    """Parses an expression of required tech into Python code to execute on the tech context."""
    def __init__(self, text):
        self.tokens = REGEX.findall(text)
        self.pos = 0

    def peek(self):
        """Peeks at the next token in the input."""
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def consume(self, expected=None):
        """Peeks at the next token and consumes it if it matches [expected]."""
        token = self.peek()
        if expected is not None and token != expected:
            raise SyntaxError(f"Expected to read {expected}, got {token} instead")
        self.pos += 1
        return token

    def parse(self):
        """Attempts to parse the expression."""
        expr = self.parse_or()
        if self.peek() is not None:
            raise SyntaxError(f"Unexpected token {self.peek()}")
        return expr

    def parse_or(self):
        """Attempts to parse an or expression."""
        expr = self.parse_and()
        while self.peek() == "||":
            self.consume("||")
            rhs = self.parse_and()
            expr = f"({expr} or {rhs})"
        return expr

    def parse_and(self):
        """Attempts to parse an and expression."""
        expr = self.parse_primary()
        while self.peek() == "&&":
            self.consume("&&")
            rhs = self.parse_primary()
            expr = f"({expr} and {rhs})"
        return expr

    def parse_primary(self):
        """Attempts to parse the name of a tech type."""
        tok = self.peek()
        if tok is None:
            raise SyntaxError("Unexpected end of expression")
        if tok == "(":
            # If we start a ( this is a sub-expression not the name of something!
            # Go back up to the top level.
            self.consume()
            expr = self.parse_or()
            self.consume(")")
            return expr

        # Consume the token and write a call to check it
        self.consume()
        return f'self.has_tech_type("{tok}")'

@dataclass(frozen=True)
class Tech:
    """
    The tech required to accomplish something.

    Requirements are written with (), ||, && and:
        PURPLE_SWING
        DAMAGE_BOOST
        BACKWARDS_SLIDE_JUMP
        ELIXIR
        COBD_SKIP
        TECHNICAL
        HAS_REENTERED_FROM_THAT_ONE_SPECIFIC_EXIT
        COMPLETED_COBD
        LY_SKIP
        KAPOUEH_SKIP
        AIRSIM
        TORNADO_SKIP
        JANO_SKIP_OOB
        HOVER
        LEDGE_GRAB
        SWIM
        LAVA_HOVER
        THINK
    """
    requirements: str = ""
    purpleLumItem: str | None = None


    @cached_property
    def requires_that_one_exit(self):
        return "HAS_REENTERED_FROM_THAT_ONE_SPECIFIC_EXIT" in self.requirements

    @cached_property
    def always_true(self):
        return self.requirements == ""

    @cached_property
    def evaluator(self):
        # The syntax is close to Python so we just generate Python
        # code to execute to determine the tech.
        expression = Parser(self.requirements).parse()
        return eval("lambda self: " + expression, {})

@dataclass
class TechContext:
    player: int
    state: CollectionState
    options: Rayman2Options
    sideTempleFinishEvent: str
    cobdFinishEvent: str
    purpleLumItem: str | None = None
    types: dict[str, bool] = field(default_factory=dict)

    def is_glitch_enabled(self, option) -> bool:
        """Return true for an enabled world trick or UT's virtual glitch item."""
        return option.value == 1 or self.state.has(GLITCHES_ITEM_NAME, self.player)

    def determine_cachable_tech_type(self, tech: str) -> bool:
        """Determines if the collection state in this context has the given cachable tech types."""
        match tech:
            case "PURPLE_SWING":
                return self.state.has("Silver Lum", self.player)
            case "DAMAGE_BOOST":
                return self.is_glitch_enabled(self.options.glitched_damage_boosts)
            case "BACKWARDS_SLIDE_JUMP":
                # Backwards slide jumps requires hover to get into position!
                return (
                    self.options.movement_hover.value != 1 or self.state.has("Hover", self.player)
                ) and self.is_glitch_enabled(self.options.glitched_backwards_slide_jumps)
            case "ELIXIR":
                return self.state.has("Elixir of Life", self.player)
            case "COBD_SKIP":
                return self.is_glitch_enabled(self.options.glitched_cobd_skip)
            case "TECHNICAL":
                return self.is_glitch_enabled(self.options.glitched_technical_tricks)
            case "EARLY_ECHOING_CAVES":
                return self.is_glitch_enabled(self.options.glitched_early_echoing_caves)
            case "REVERSE_EARLY_ECHOING_CAVES":
                return self.is_glitch_enabled(self.options.glitched_reverse_early_echoing_caves)
            case "COMPLETED_COBD":
                return self.state.has(self.cobdFinishEvent, self.player)
            case "LY_SKIP":
                return (
                    self.options.movement_hover.value != 1 or self.state.has("Hover", self.player)
                ) and self.is_glitch_enabled(self.options.glitched_ly_skip)
            case "KAPOUEH_SKIP":
                return (
                    (self.options.movement_hover.value != 1 or self.state.has("Hover", self.player))
                    and (self.options.movement_ledge_grab.value != 1 or self.state.has("Ledge Grab", self.player))
                    and self.is_glitch_enabled(self.options.glitched_kapoueh_skip)
                )
            case "AIRSWIM":
                return (
                    self.options.movement_swim.value != 1 or self.state.has("Swim", self.player)
                ) and self.is_glitch_enabled(self.options.glitched_airswims)
            case "TORNADO_SKIP":
                return (
                    (self.options.movement_hover.value != 1 or self.state.has("Hover", self.player))
                    and (self.options.movement_ledge_grab.value != 1 or self.state.has("Ledge Grab", self.player))
                    and self.is_glitch_enabled(self.options.glitched_tornado_skip)
                )
            case "JANO_SKIP_OOB":
                return (
                    self.options.movement_hover.value != 1 or self.state.has("Hover", self.player)
                ) and self.is_glitch_enabled(self.options.glitched_jano_skip)
            case "HOVER":
                return (self.options.movement_hover.value != 1 or self.state.has("Hover", self.player))
            case "LEDGE_GRAB":
                return (self.options.movement_ledge_grab.value != 1 or self.state.has("Ledge Grab", self.player))
            case "SWIM":
                return (self.options.movement_swim.value != 1 or self.state.has("Swim", self.player))
            case "LAVA_HOVER":
                hover = (self.options.movement_hover.value != 1 or self.state.has("Hover", self.player))
                lavaHover = (self.options.movement_lava_hover.value != 1 or self.state.has("Lava Hover", self.player))
                return hover and lavaHover
            case "THINK":
                # You get think from the first yellow lum in Woods of Light, so to have think
                # they must be able to reach this.
                return self.state.can_reach_region("learn_10", self.player)
            case _:
                raise KeyError(f"Unknown tech type '{tech}'")

    def has_tech_type(self, tech: str) -> bool:
        """Returns whether the given state has the given tech type."""
        match tech:
            case "PURPLE_SWING":
                if self.options.fragmented_silver_lums.value == 1:
                    return self.state.has(self.purpleLumItem, self.player)
            case "HAS_REENTERED_FROM_THAT_ONE_SPECIFIC_EXIT":
                return self.state.has(self.sideTempleFinishEvent, self.player) or (
                        self.is_glitch_enabled(self.options.glitched_plum_wall_climb) and self.has_tech_type("HOVER") and (self.has_tech_type("TECHNICAL") or self.has_tech_type("PURPLE_SWING"))
                )
        
        # Read all other types from the cache so we can re-use them!
        if tech in self.types:
            return self.types[tech]
        else:
            result = self.determine_cachable_tech_type(tech)
            self.types[tech] = result
            return result

    def has_tech(self, tech: Tech) -> bool:
        """Returns whether this context has the given [tech]."""
        if tech.always_true:
            return True
        self.purpleLumItem = tech.purpleLumItem
        return tech.evaluator(self)