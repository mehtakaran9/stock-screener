from backend.utils import (
    fmt_number, rsi_color, rsi_label,
    DANGER, SUCCESS, AMBER, TEXT_SECONDARY,
)


def test_fmt_number_trillions():
    assert fmt_number(2.5e12) == "2.50T"


def test_fmt_number_billions():
    assert fmt_number(3.1e9) == "3.10B"


def test_fmt_number_millions():
    assert fmt_number(52.3e6) == "52.30M"


def test_fmt_number_thousands():
    assert fmt_number(5_500) == "5.50K"


def test_fmt_number_small():
    assert fmt_number(999) == "999"


def test_rsi_color_overbought():
    assert rsi_color(75) == DANGER


def test_rsi_color_overbought_boundary():
    assert rsi_color(70.1) == DANGER


def test_rsi_color_bull():
    assert rsi_color(60) == SUCCESS


def test_rsi_color_bull_boundary():
    assert rsi_color(55) == SUCCESS


def test_rsi_color_neutral():
    assert rsi_color(45) == AMBER


def test_rsi_color_neutral_boundary():
    assert rsi_color(40) == AMBER


def test_rsi_color_weak():
    assert rsi_color(35) == TEXT_SECONDARY


def test_rsi_label_overbought():
    assert rsi_label(75) == "OB"


def test_rsi_label_bull():
    assert rsi_label(60) == "Bull"


def test_rsi_label_neutral():
    assert rsi_label(45) == "Neut"


def test_rsi_label_weak():
    assert rsi_label(35) == "Weak"
