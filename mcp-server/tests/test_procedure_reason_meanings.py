"""RUN-10: say what each no_check_reason means.

The paragraph is fixed prompt text. The names in it (A, B) are the
wording of the instruction, not a corpus. No network.
"""

from grap_mcp.procedure_model import SYSTEM
from grap_mcp.procedure_template import FIXED_TEXT

# The RUN-09 sentence, unchanged. The new paragraph follows it.
_RUN09 = (
    "When a requirement names DBC Signals, at least one case verifying it must have a check. "
    "If no check can be written, leave checks empty and set no_check_reason to inside_ecu, "
    "signal_compare or not_on_bus. "
    "Never write a check you cannot ground in the case's uses."
)
_REASON_MEANINGS = (
    "signal_compare is only for an expected result that relates two signal values to each other, "
    "such as A equals B or A stays within 5 of B. "
    "A cause on one signal and its effect on another is not signal_compare: "
    "put the cause in when and the effect in expect. "
    "not_on_bus is for a cause or result that no DBC Signal carries, such as oncoming traffic. "
    "inside_ecu is for a result visible only inside the ECU. "
    "Every condition the requirement says must already hold "
    "(while ..., only when ..., when ... is ...) goes into given as a condition on its signal."
)


def test_a_both_prompts_say_what_each_no_check_reason_means():
    """Both prompts carry the RUN-10 paragraph word for word, after the RUN-09 sentence."""
    placed = _RUN09 + " " + _REASON_MEANINGS
    for prompt in (SYSTEM, FIXED_TEXT):
        assert placed in prompt
