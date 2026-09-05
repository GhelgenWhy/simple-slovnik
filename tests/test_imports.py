"""Smoke tests verifying package structure exists and is importable."""


def test_package_imports():
    import slovnik  # noqa: F401


def test_main_module_runs_help(capsys):
    from slovnik.__main__ import main
    rc = main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Slovník MVP" in out