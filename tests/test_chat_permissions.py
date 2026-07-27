from kageha.chat.repl import _apply_permissions, _permissions_status


def test_permissions_status_modes():
    assert "auto" in _permissions_status(True)
    assert "ask" in _permissions_status(False)


def test_permissions_toggle():
    flag, msg = _apply_permissions("", auto_approve=False)
    assert flag is False
    assert "ask" in msg

    flag, msg = _apply_permissions("auto", auto_approve=False)
    assert flag is True
    assert "auto" in msg

    flag, msg = _apply_permissions("off", auto_approve=True)
    assert flag is False

    flag, msg = _apply_permissions("on", auto_approve=False)
    assert flag is True

    flag, msg = _apply_permissions("nope", auto_approve=False)
    assert flag is False
    assert "usage" in msg.lower()
