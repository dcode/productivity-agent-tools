"""A catalog form's UI policies: which questions are shown and required, given the answers.

The portal sends a form's UI policies with the form itself (``sc_cat_item.policy``)
and applies them in the browser as answers change: on the work instance,
Subcategory is shown and required only when Category is Hardware, and Hostname
and Network only for a Hardware desktop or laptop. The server does not check them
on submission, so this module evaluates them the way the portal does, against the
final answers.

A policy is a list of conditions and a list of actions. Conditions read like an
encoded query: each joins the one before it with AND, or with OR when its ``or``
flag is set, and ``newquery`` starts a new group (groups are ORed). When the
conditions hold, the actions apply; when they do not and the policy is "reverse
if false", the opposite applies. Later policies win over earlier ones. A policy
using an operator this module does not know is not applied, and is reported.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from productivity_agent_tools.payload import objects, text, value

_PREFIX = "variables."


def _split(values: str) -> list[str]:
    """Split a comma-separated condition value, as IN takes it."""
    return [part.strip() for part in values.split(",")]


# How each condition operator tests an answer (the stored value, "" when unanswered).
OPERATORS: dict[str, Callable[[str, str], bool]] = {
    "=": lambda answer, wanted: answer == wanted,
    "!=": lambda answer, wanted: answer != wanted,
    "IN": lambda answer, wanted: answer in _split(wanted),
    "NOT IN": lambda answer, wanted: answer not in _split(wanted),
    "ISEMPTY": lambda answer, _: not answer,
    "ISNOTEMPTY": lambda answer, _: bool(answer),
    "STARTSWITH": lambda answer, wanted: answer.startswith(wanted),
    "ENDSWITH": lambda answer, wanted: answer.endswith(wanted),
    "LIKE": lambda answer, wanted: wanted in answer,
    "NOT LIKE": lambda answer, wanted: wanted not in answer,
}
_WORDS: dict[str, str] = {
    "=": "is",
    "!=": "is not",
    "IN": "is one of",
    "NOT IN": "is none of",
    "ISEMPTY": "is empty",
    "ISNOTEMPTY": "is answered",
    "STARTSWITH": "starts with",
    "ENDSWITH": "ends with",
    "LIKE": "contains",
    "NOT LIKE": "does not contain",
}


@dataclass(frozen=True, slots=True)
class Condition:
    """One condition of a policy.

    Attributes:
        field: The question's name.
        operator: The operator, such as ``=`` or ``IN``.
        value: What the answer is compared with.
        joins_with_or: True when this condition is ORed with the one before it.
        starts_group: True when this condition starts a new group (groups are ORed).
    """

    field: str
    operator: str
    value: str
    joins_with_or: bool = False
    starts_group: bool = False

    def holds(self, answers: Mapping[str, str]) -> bool:
        """Test the condition against the answers (stored values by name)."""
        return OPERATORS[self.operator](answers.get(self.field, ""), self.value)

    def describe(self) -> str:
        """The condition in words, such as "category is hardware"."""
        words = _WORDS.get(self.operator, self.operator)
        if self.operator in ("ISEMPTY", "ISNOTEMPTY"):
            return f"{self.field} {words}"
        return f"{self.field} {words} {self.value}"


@dataclass(frozen=True, slots=True)
class Effect:
    """What a policy does to one question when it applies.

    Attributes:
        name: The question's name.
        visible: Shown (True) or hidden (False); None leaves it as it is.
        mandatory: Required (True) or not (False); None leaves it as it is.
    """

    name: str
    visible: bool | None = None
    mandatory: bool | None = None

    def reversed(self) -> Effect:
        """The opposite effect, for a "reverse if false" policy whose conditions fail."""
        return Effect(
            self.name,
            None if self.visible is None else not self.visible,
            None if self.mandatory is None else not self.mandatory,
        )

    def describe(self) -> str:
        """The effect in words, such as "subcategory shown and required"."""
        parts = [
            word
            for flag, yes, no in (
                (self.visible, "shown", "hidden"),
                (self.mandatory, "required", "optional"),
            )
            if (word := (None if flag is None else yes if flag else no))
        ]
        return f"{self.name} {' and '.join(parts)}"


@dataclass(frozen=True, slots=True)
class Rule:
    """One UI policy.

    Attributes:
        title: The policy's short description.
        conditions: When it applies; none means always.
        effects: What it does, for questions on the form.
        reverse: True when the opposite applies while the conditions do not hold.
    """

    title: str
    conditions: tuple[Condition, ...]
    effects: tuple[Effect, ...]
    reverse: bool = True

    @property
    def understood(self) -> bool:
        """True when every operator it uses is one this module evaluates."""
        return all(condition.operator in OPERATORS for condition in self.conditions)

    def holds(self, answers: Mapping[str, str]) -> bool:
        """Evaluate the conditions as an encoded query: AND, OR, then groups."""
        if not self.conditions:
            return True
        groups: list[list[list[Condition]]] = []
        for condition in self.conditions:
            if not groups or condition.starts_group:
                groups.append([[condition]])
            elif condition.joins_with_or:
                groups[-1][-1].append(condition)
            else:
                groups[-1].append([condition])
        return any(
            all(any(c.holds(answers) for c in either) for either in group) for group in groups
        )

    def describe(self) -> str:
        """The policy in words, for the agent: when it applies and what it does."""
        when = "always"
        if self.conditions:
            words: list[str] = []
            for place, condition in enumerate(self.conditions):
                if place:
                    words.append(
                        "or, separately,"
                        if condition.starts_group
                        else "or"
                        if condition.joins_with_or
                        else "and"
                    )
                words.append(condition.describe())
            when = "when " + " ".join(words)
        does = "; ".join(effect.describe() for effect in self.effects)
        otherwise = "; otherwise the opposite" if self.reverse and self.conditions else ""
        known = "" if self.understood else " (not checked: an operator is not understood)"
        return f"{self.title}: {when}: {does}{otherwise}{known}"


@dataclass(frozen=True, slots=True)
class State:
    """Whether a question is on the form, and whether it must be answered.

    Attributes:
        visible: Shown on the form.
        mandatory: Must be answered (only while shown).
    """

    visible: bool
    mandatory: bool


def evaluate(
    rules: tuple[Rule, ...], base: Mapping[str, State], answers: Mapping[str, str]
) -> dict[str, State]:
    """Work out each question's state, applying the rules in order as the portal does.

    Args:
        rules: The form's policies, in the form's order.
        base: Each question's state before any policy.
        answers: The stored value of every answered question, by name.

    Returns:
        Each question's state.
    """
    states = dict(base)
    for rule in rules:
        if not rule.understood:
            continue
        if rule.holds(answers):
            effects = rule.effects
        elif rule.reverse:
            effects = tuple(effect.reversed() for effect in rule.effects)
        else:
            continue
        for effect in effects:
            if (current := states.get(effect.name)) is None:
                continue
            states[effect.name] = State(
                visible=current.visible if effect.visible is None else effect.visible,
                mandatory=current.mandatory if effect.mandatory is None else effect.mandatory,
            )
    return states


def _name(field: str) -> str:
    """Strip the ``variables.`` a catalog policy puts before a question's name."""
    return field.removeprefix(_PREFIX)


def _setting(raw: JsonValue) -> bool | None:
    """Read an action's ``"true"``, ``"false"`` or ``"ignore"``."""
    word = raw.strip().casefold() if isinstance(raw, str) else raw
    if word in (True, "true"):
        return True
    if word in (False, "false"):
        return False
    return None


def parse_rules(policies: JsonValue, names: frozenset[str]) -> tuple[Rule, ...]:
    """Read a form's UI policies from the portal's form model.

    Args:
        policies: The item's ``policy`` list.
        names: The questions on the form; effects on anything else are left out.

    Returns:
        The policies that affect at least one question, in order.
    """
    rules: list[Rule] = []
    for policy in objects(policies):
        effects = tuple(
            Effect(name, _setting(value(action, "visible")), _setting(value(action, "mandatory")))
            for action in objects(value(policy, "actions"))
            if (name := _name(text(action, "name") or "")) in names
        )
        effects = tuple(e for e in effects if e.visible is not None or e.mandatory is not None)
        if not effects:
            continue
        conditions = tuple(
            Condition(
                field=_name(text(condition, "field") or ""),
                operator=(text(condition, "oper") or text(condition, "operator") or "").strip(),
                value=text(condition, "value") or "",
                joins_with_or=_setting(value(condition, "or")) is True,
                starts_group=_setting(value(condition, "newquery")) is True,
            )
            for condition in objects(value(policy, "conditions"))
        )
        rules.append(
            Rule(
                title=text(policy, "short_description") or "A form rule",
                conditions=conditions,
                effects=effects,
                reverse=_setting(value(policy, "reverse")) is not False,
            )
        )
    return tuple(rules)
