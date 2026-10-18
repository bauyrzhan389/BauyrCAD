"""File > Export to SolidWorks: runs the export in a worker thread (SolidWorks takes a few
seconds per part) with a progress dialog, then shows what was rebuilt."""

from __future__ import annotations

import html
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog


class _Worker(QThread):
    done = Signal(object, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except Exception as e:  # noqa: BLE001 - reported to the user
            self.done.emit(None, f"{e}\n\n{traceback.format_exc(limit=3)}")


def precheck(parent, issues) -> bool:
    """Strict export: show the known gaps (found without starting SolidWorks) and stop.
    True when there are none."""
    if not issues:
        return True
    shown = issues[:25] + ([f"... and {len(issues) - 25} more"] if len(issues) > 25 else [])
    QMessageBox.warning(parent, "Export to SolidWorks",
                        "This model can't be exported with its complete, editable SolidWorks "
                        "feature tree yet:\n\n" + "\n".join(f"- {i}" for i in shown)
                        + "\n\nNothing was saved and SolidWorks was not started. Use File > "
                        "Export (STEP) when only the geometry is needed.")
    return False


def _report_html(rep: dict) -> str:
    def part_lines(name, p):
        kind = "feature tree" if p["native"] else "solid body"
        feats = ", ".join(p.get("features") or []) or "-"
        out = f"<b>{html.escape(name)}</b>: {kind} ({html.escape(feats)})"
        for w in p.get("warnings") or []:
            out += f"<br>&nbsp;&nbsp;<span style='color:#a15c00'>{html.escape(w)}</span>"
        for step, feats in (p.get("steps") or {}).items():
            out += (f"<br>&nbsp;&nbsp;{html.escape(step)} became {len(feats)} SolidWorks features: "
                    f"{html.escape(', '.join(feats))}")
        if p.get("helpers"):
            out += ("<br>&nbsp;&nbsp;<span style='color:#606060'>Hidden helper geometry for "
                    f"SolidWorks: {html.escape(', '.join(p['helpers']))}</span>")
        for n in p.get("notes") or []:
            out += f"<br>&nbsp;&nbsp;<span style='color:#606060'>Note: {html.escape(n)}</span>"
        return out
    lines = [f"Saved <b>{html.escape(rep['path'])}</b>"]
    if "parts" in rep:
        lines += [part_lines(n, p) for n, p in rep["parts"].items()]
        mates = rep.get("mates", [])
        ok = sum(1 for m in mates if m["ok"])
        lines.append(f"<b>Mates:</b> {ok} of {len(mates)} recreated in SolidWorks")
        lines += [f"&nbsp;&nbsp;<span style='color:#b03030'>{html.escape(m['name'])}: "
                  f"{html.escape(m['note'])}</span>" for m in mates if not m["ok"]]
        for w in rep.get("warnings") or []:
            lines.append(f"<span style='color:#a15c00'>{html.escape(w)}</span>")
    else:
        lines.append(part_lines(Path(rep["path"]).name, rep))
    return "<br>".join(lines)


def run(parent, title: str, fn, on_done=None):
    """Run fn() (an export) in the background with a busy dialog; show the report."""
    dlg = QProgressDialog("Building the SolidWorks files...\n(SolidWorks runs in the background; "
                          "the first export also prepares the connection)", None, 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.show()
    worker = _Worker(fn)
    parent._sw_worker = worker                     # keep a reference while it runs

    def finished(rep, err):
        dlg.close()
        QApplication.restoreOverrideCursor()
        if err:
            QMessageBox.critical(parent, title, f"The SolidWorks export failed:\n\n{err}")
        else:
            box = QMessageBox(parent)
            box.setWindowTitle(title)
            box.setTextFormat(Qt.RichText)
            box.setText(_report_html(rep))
            box.exec()
        if on_done:
            on_done(rep, err)
    worker.done.connect(finished)
    QApplication.setOverrideCursor(Qt.WaitCursor)
    worker.start()
    return worker
