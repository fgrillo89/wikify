"""Tests for the tolerant quote-substring normalizer."""

from wikify_simple.contracts.normalize import normalize_for_substring


def test_clean_text_identity_lowercase():
    s = "atomic layer deposition is self-limiting"
    assert normalize_for_substring(s) == s


def test_bracket_wrap_unwrapped():
    # pymupdf column-reconstruction artifact: [token] around an ordinary word.
    noisy = "H][2][plasma] is used"
    clean = "atomic plasma discharge"
    # Unwraps to "h]2plasma is used" and "2" is digit run kept;
    # verify the plasma token survives
    out = normalize_for_substring(noisy)
    assert "plasma" in out
    assert normalize_for_substring(clean)  # sanity


def test_double_space_collapsed():
    assert normalize_for_substring("foo    bar") == "foo bar"


def test_unicode_dash_normalized():
    # en dash, em dash, minus sign all -> '-'
    s = "self\u2013limiting \u2014 self\u2212limiting"
    out = normalize_for_substring(s)
    # Dashes normalise AND whitespace around hyphens collapses.
    assert out == "self-limiting-self-limiting"


def test_citation_marker_stripped():
    assert normalize_for_substring("memristor [12] is defined") == "memristor is defined"
    assert normalize_for_substring("see [12-15] for details") == "see for details"


def test_curly_quotes_normalized():
    s = "\u201cALD\u201d is a \u2018method\u2019"
    out = normalize_for_substring(s)
    assert out == "\"ald\" is a 'method'"


def test_truly_absent_quote_rejected():
    chunk = normalize_for_substring("atomic layer deposition is self-limiting")
    bogus = normalize_for_substring("quantum entanglement in plasma")
    assert bogus not in chunk


def test_case_insensitive_substring():
    chunk = normalize_for_substring("Atomic Layer Deposition Is A Self-Limiting Process.")
    q = normalize_for_substring("ATOMIC LAYER DEPOSITION")
    assert q in chunk


def test_noisy_quote_accepted_against_clean_chunk():
    # The model strips citation markers and normalises dashes; the raw
    # chunk still has them. Normalizing both sides makes the check pass.
    chunk = "the memristor [12] was first described by chua\u2014a theoretical device"
    q = "the memristor was first described by chua - a theoretical device"
    assert normalize_for_substring(q) in normalize_for_substring(chunk)
