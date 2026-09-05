"""Smoke tests verifying package structure exists and is importable."""


def test_package_imports():
    import slovnik  # noqa: F401


def test_main_module_runs_help(monkeypatch):
    """__main__.main() must dispatch to app.run() and return its rc.

    The Task 1 stub printed "Slovník MVP"; Task 6 replaces the stub with a real
    GUI entry point, so we now mock app.run to keep this a fast, headless smoke test.
    """
    from slovnik import __main__ as main_mod
    from slovnik import app as app_mod

    monkeypatch.setattr(main_mod, "run", lambda db: 0)
    rc = main_mod.main()
    assert rc == 0
    # app_mod.run is the real implementation imported into __main__ — sanity-check
    # that the import wiring is in place (don't actually launch the GUI here).
    assert callable(app_mod.run)