"""Tests for CheckReportDialog (check findings + apply-fix flow)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from slovnik.czech import Issue
from slovnik.ui.check_report import CheckReportDialog


def _refl_issue(fix: str = "ptát se") -> Issue:
    return Issue(kind="reflexive", severity="error", token="ptát si",
                 message="«ptát si» — помилка.", suggestion=fix)


def test_dialog_lists_issues(qtbot):
    dlg = CheckReportDialog(None, [_refl_issue(),
                                   Issue("spelling", "warn", "kocka", "опечатка")])
    qtbot.addWidget(dlg)
    assert dlg.list_widget.count() == 2
    assert "se/si" in dlg.list_widget.item(0).text()


def test_dialog_empty_state(qtbot):
    dlg = CheckReportDialog(None, [])
    qtbot.addWidget(dlg)
    assert dlg.list_widget.count() == 0
    assert "Проблем не знайдено" in dlg.hint.text()


def test_dialog_apply_updates_target_and_rechecks(qtbot):
    holder = {"text": "ptát si"}
    issues = [_refl_issue()]
    dlg = CheckReportDialog(
        None, issues,
        getter=lambda: holder["text"],
        setter=lambda t: holder.update(text=t),
        recheck=lambda t: [] if t == "ptát se" else issues,
    )
    qtbot.addWidget(dlg)
    dlg.list_widget.setCurrentRow(0)
    dlg._on_apply()
    assert holder["text"] == "ptát se"
    # recheck came back clean → empty list, happy hint
    assert dlg.list_widget.count() == 0
    assert "Проблем не знайдено" in dlg.hint.text()


def test_dialog_read_only_mode_hides_apply(qtbot):
    dlg = CheckReportDialog(None, [_refl_issue()])
    qtbot.addWidget(dlg)
    assert dlg.apply_btn.isHidden()
    # editable mode shows it
    dlg2 = CheckReportDialog(None, [_refl_issue()], getter=lambda: "ptát si",
                             setter=lambda t: None)
    qtbot.addWidget(dlg2)
    assert not dlg2.apply_btn.isHidden()


def test_dialog_apply_without_suggestion_is_noop(qtbot):
    holder = {"text": "kocka"}
    dlg = CheckReportDialog(
        None, [Issue("spelling", "warn", "kocka", "опечатка")],
        getter=lambda: holder["text"], setter=lambda t: holder.update(text=t),
    )
    qtbot.addWidget(dlg)
    dlg.list_widget.setCurrentRow(0)
    assert not dlg.apply_btn.isEnabled()  # no mechanical fix for spelling
    dlg._on_apply()
    assert holder["text"] == "kocka"
