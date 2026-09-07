"""Judging an infrared capture without a remote in hand.

The capture script asks for three presses of each button and decides whether they
agree. Deciding that is harder than it sounds: the log describes one press three
times, holding a button changes the payload, and raw timings never repeat
exactly. All of that is here, so the evening with the remote is not the first
time any of it runs.
"""

from __future__ import annotations

import pytest

from hydrosnooze.ircodes import (
    BUTTONS,
    Capture,
    Reading,
    best,
    cross_check,
    held_too_long,
    judge,
    parse_line,
    report,
    signature,
)

NEC = "[14:23:14][I][remote.nec:070]: Received NEC: address=0x10EF, command=0x54AB, command_repeats=1"
RAW = "[14:23:14][I][remote.raw:041]: Received Raw: 9024, -4512, 564, -564, 564, -1692"
PRONTO = "[14:23:14][I][remote.pronto:226]: Received Pronto: data=0000 006D 0022 0002"


def nec(command: str, repeats: int = 1, address: str = "0x10EF") -> Reading:
    return Reading("nec", f"address={address}, command={command}, command_repeats={repeats}")


# --- Reading the log -----------------------------------------------------------


def test_a_decoded_line_becomes_a_reading():
    reading = parse_line(NEC)
    assert reading is not None
    assert reading.protocol == "nec"
    assert reading.payload == "address=0x10EF, command=0x54AB, command_repeats=1"
    assert reading.is_named


def test_raw_and_pronto_are_not_named_protocols():
    assert parse_line(RAW).is_named is False
    assert parse_line(PRONTO).is_named is False


def test_the_colour_codes_esphome_adds_do_not_break_it():
    """The log is coloured, and the escape codes sit in the middle of the text."""
    coloured = "\033[0;36m[14:23:14][I][remote.nec:070]: Received NEC: address=0x1, command=0x2\033[0m"
    reading = parse_line(coloured)
    assert reading is not None
    assert reading.protocol == "nec"
    assert "\033" not in reading.payload


@pytest.mark.parametrize(
    "line",
    [
        "[14:23:00][C][remote_receiver:049]: Remote Receiver:",
        "INFO Starting log output from /dev/cu.usbmodem101",
        "",
        "Received",
    ],
)
def test_everything_else_in_the_log_is_ignored(line):
    assert parse_line(line) is None


# --- One press, described several ways -----------------------------------------


def test_a_named_protocol_beats_raw_timings():
    """dump: all describes one press three ways. Four bytes beat a hundred
    numbers, and are self-correcting where a mistyped timing is not."""
    readings = [parse_line(RAW), parse_line(NEC), parse_line(PRONTO)]
    chosen = best([r for r in readings if r])
    assert chosen is not None and chosen.protocol == "nec"


def test_raw_is_taken_when_nothing_decoded():
    readings = [parse_line(PRONTO), parse_line(RAW)]
    chosen = best([r for r in readings if r])
    assert chosen is not None and chosen.protocol == "raw"


def test_no_readings_at_all_is_no_choice():
    assert best([]) is None


# --- What counts as the same button --------------------------------------------


def test_holding_a_button_longer_is_still_the_same_button():
    """command_repeats counts how long it was held. Two presses of one button
    differ by it constantly, so it must not count towards identity."""
    assert signature(nec("0x54AB", repeats=1)) == signature(nec("0x54AB", repeats=4))


def test_a_different_command_is_a_different_button():
    assert signature(nec("0x54AB")) != signature(nec("0x54AC"))


def test_raw_timings_are_compared_loosely_because_they_jitter():
    """The same press twice never gives byte-identical timings. A remote and a
    receiver both wobble by tens of microseconds."""
    first = Reading("raw", "9024, -4512, 564, -564")
    second = Reading("raw", "9041, -4488, 551, -579")
    assert signature(first) == signature(second)


def test_genuinely_different_raw_codes_still_differ():
    first = Reading("raw", "9024, -4512, 564, -564")
    second = Reading("raw", "9024, -4512, 564, -1692")
    assert signature(first) != signature(second)


def test_a_different_number_of_pulses_is_a_different_code():
    assert signature(Reading("raw", "9024, -4512")) != signature(Reading("raw", "9024, -4512, 564"))


def test_holding_is_noticed_even_though_it_is_forgiven():
    assert held_too_long(nec("0x1", repeats=3)) is True
    assert held_too_long(nec("0x1", repeats=1)) is False


# --- The verdict on one button --------------------------------------------------


def test_three_agreeing_presses_pass():
    verdict = judge([nec("0x54AB"), nec("0x54AB", 2), nec("0x54AB")], 3)
    assert verdict.ok
    assert "consistent" in verdict.headline


def test_disagreeing_presses_fail_and_say_why():
    verdict = judge([nec("0x54AB"), nec("0x99FF"), nec("0x54AB")], 3)
    assert not verdict.ok
    assert "different codes" in verdict.headline
    assert "10cm" in verdict.detail


def test_too_few_presses_is_not_a_pass():
    assert not judge([nec("0x54AB")], 3).ok
    assert not judge([], 3).ok


def test_raw_only_passes_but_is_flagged():
    raw = Reading("raw", "9024, -4512, 564, -564")
    verdict = judge([raw, raw, raw], 3)
    assert verdict.ok
    assert "raw" in verdict.headline


def test_a_long_hold_passes_with_a_note():
    verdict = judge([nec("0x1"), nec("0x1", repeats=5), nec("0x1")], 3)
    assert verdict.ok
    assert "held" in verdict.detail


# --- Problems only visible with all eight side by side --------------------------


def captures(**codes: str) -> list[Capture]:
    out = []
    for name, label in BUTTONS:
        capture = Capture(name, label)
        if name in codes:
            capture.presses = [nec(codes[name])] * 3
        out.append(capture)
    return out


def test_a_clean_set_of_eight_has_nothing_to_say():
    codes = {name: f"0x{index:02X}" for index, (name, _) in enumerate(BUTTONS)}
    assert cross_check(captures(**codes)) == []


def test_two_buttons_with_the_same_code_are_caught():
    """What pressing the same button twice by mistake looks like, and it is
    invisible when you are looking at one capture at a time."""
    problems = cross_check(captures(power="0x54AB", mute="0x54AB"))
    assert any("identical code" in p for p in problems)
    assert any("power" in p and "mute" in p for p in problems)


def test_a_button_on_a_different_address_is_caught():
    set_ = captures(power="0x01")
    odd = next(c for c in set_ if c.name == "cool")
    odd.presses = [nec("0x02", address="0x77BB")] * 3
    problems = cross_check(set_)
    assert any("address" in p for p in problems)


def test_a_button_decoding_as_a_different_protocol_is_caught():
    set_ = captures(power="0x01", warm="0x02")
    odd = next(c for c in set_ if c.name == "warm")
    odd.presses = [Reading("raw", "9024, -4512")] * 3
    problems = cross_check(set_)
    assert any("different protocols" in p for p in problems)


def test_nothing_to_compare_yet_says_nothing():
    assert cross_check(captures()) == []
    assert cross_check(captures(power="0x01")) == []


# --- The thing that gets sent on ------------------------------------------------


def test_the_report_names_every_button_and_its_verdict():
    text = report(captures(power="0x54AB"), 3)
    for name, _ in BUTTONS:
        assert name in text
    assert "OK  power" in text
    assert "BAD mute" in text, "a button never pressed has to show as missing"
    assert "0x54AB" in text
    assert "Across all eight:" in text


def test_the_report_shows_every_press_not_just_the_conclusion():
    """So a capture can be second-guessed by eye afterwards."""
    text = report(captures(power="0x54AB"), 3)
    assert text.count("press 1:") == 1
    assert "press 3:" in text
