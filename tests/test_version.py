from findajob.version import is_newer, version_tuple


def test_version_tuple_parses_and_strips_v():
    assert version_tuple("0.33.0") == (0, 33, 0)
    assert version_tuple("v0.33.0") == (0, 33, 0)


def test_version_tuple_none_on_garbage():
    assert version_tuple("unknown") is None
    assert version_tuple("0.33.0-rc1") is None
    assert version_tuple("") is None


def test_is_newer_true_only_when_strictly_greater():
    assert is_newer("0.34.0", "0.33.0") is True
    assert is_newer("0.33.1", "0.33.0") is True
    assert is_newer("0.33.0", "0.33.0") is False
    assert is_newer("0.32.0", "0.33.0") is False


def test_is_newer_failclosed_on_unparseable():
    assert is_newer("unknown", "0.33.0") is False
    assert is_newer("0.34.0", "unknown") is False


def test_is_newer_handles_differing_segment_counts():
    # padding: (0,33) vs (0,33,0) must be equal, not "newer"
    assert is_newer("0.33", "0.33.0") is False
    assert is_newer("0.33.0", "0.33") is False
