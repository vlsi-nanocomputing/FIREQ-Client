"""End-to-end test of the PyQt6 GUI with synthetic mouse/keyboard events (offscreen).

Run: ``QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_qt``.
Set ``FIREQ_GUI_SHOTS=<dir>`` to save screenshots. Skipped when PyQt6 is not installed.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class GuiTest(unittest.TestCase):
    def test_editing_and_run(self) -> None:  # noqa: PLR0915
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import yaml
        from PyQt6.QtCore import QEvent, QMimeData, QPointF, QSettings, Qt
        from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QKeyEvent, QMouseEvent

        app = QApplication.instance() or QApplication(["fireq"])
        QSettings("vlsi-nanocomputing", "FIREQ-GUI").clear()
        from FIREQ_GUI.core.model import default_experiment
        from FIREQ_GUI.core.yaml_export import Exporter
        from FIREQ_GUI.gui.main_window import MainWindow
        from FIREQ_GUI.gui.timeline import MIME

        shots_dir = os.environ.get("FIREQ_GUI_SHOTS")

        def pump(n=10):
            for _ in range(n):
                app.processEvents()
                time.sleep(0.01)

        def _shot(w, name):
            if shots_dir:
                pump()
                w.grab().save(os.path.join(shots_dir, f"{name}.png"))

        def check(cond, msg):
            self.assertTrue(cond, msg)

        tmp = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            w = MainWindow()
            w.resize(1680, 980)
            w.show()
            pump(30)
            w.timeline.view.fit()
            pump()
            view, exp = w.timeline.view, w.exp
            vp = view.viewport()
            tick = exp.tick

            def send_mouse(kind, pos, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton):
                g = vp.mapToGlobal(pos.toPoint())
                ev = QMouseEvent(kind, pos, QPointF(g), button, buttons, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(vp, ev)
                pump(2)

            def drag(p0, p1, steps=6):
                send_mouse(QEvent.Type.MouseButtonPress, p0)
                for k in range(1, steps + 1):
                    p = p0 + (p1 - p0) * (k / steps)
                    send_mouse(QEvent.Type.MouseMove, p, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
                send_mouse(QEvent.Type.MouseButtonRelease, p1, Qt.MouseButton.NoButton)
                pump(15)

            def vpos(scene_pt):
                return QPointF(view.mapFromScene(scene_pt))

            # 1. move a drive pulse
            b = exp.drive_tracks[0].blocks[0]
            it = view.items[b.id]
            s0 = b.start
            drag(vpos(it.rect().center()), vpos(it.rect().center()) + QPointF(120, 0))
            check(b.start > s0, f"start {s0:.3f} -> {b.start:.3f} ns")
            check(abs(b.start / tick - round(b.start / tick)) < 1e-6, f"start on tick grid ({b.start / tick:.3f} ticks)")
            check(w.inspector.p_pulse.obj is b, "inspector shows the dragged pulse")

            # 2. resize it from the right edge
            it = view.items[b.id]
            d0 = b.duration
            edge = QPointF(it.rect().right() - 1, it.rect().center().y())
            drag(vpos(edge), vpos(edge) + QPointF(60, 0))
            check(b.duration > d0, f"duration {d0:.3f} -> {b.duration:.3f} ns ({b.duration / tick:.2f} ticks)")

            # 3. move a readout tone: its acquisition window follows (same ToF)
            t = exp.readout_tracks[0].tones[0]
            win = exp.windows_of(t.id)[0][1]
            tof0 = win.tof
            ex = Exporter(exp)
            ws0 = exp.ev(ex.window_start(win))
            it = view.items[t.id]
            drag(vpos(it.rect().center()), vpos(it.rect().center()) + QPointF(80, 0))
            ws1 = exp.ev(Exporter(exp).window_start(win))
            check(win.tof == tof0 and ws1 > ws0, f"window start {ws0:.2f} -> {ws1:.2f}, ToF kept {win.tof}")

            # 4. drag the acquisition window: changes the ToF
            it = view.items[win.id]
            drag(vpos(it.rect().center()), vpos(it.rect().center()) + QPointF(40, 0))
            check(win.tof > tof0, f"ToF {tof0} -> {win.tof:.3f} ns")
            sysc = yaml.safe_load(w.yaml_view.toPlainText())["sys_config"]
            check(abs(sysc[f"/axisAcquisitionIP_{win.acquisition}"]["$tof"] - win.tof) < 1e-6, "YAML $tof updated")
            _shot(w, "r1_after_drags")

            # 5. drop a DRAG pulse from the palette onto DAC 228_1
            row = next(r for r in view.rows if r.track is exp.drive_tracks[1])
            target = vpos(QPointF(view.x_of(1500), row.y + row.h / 2))
            md = QMimeData()
            md.setData(MIME, b"pulse:drag")
            n0 = len(exp.drive_tracks[1].blocks)
            for cls, typ in ((QDragEnterEvent, None), (QDragMoveEvent, None)):
                e = cls(target.toPoint(), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(vp, e)
            e = QDropEvent(target, Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(vp, e)
            pump(15)
            nb = exp.drive_tracks[1].blocks
            check(len(nb) == n0 + 1 and nb[-1].shape == "drag", f"new block {nb[-1].name} shape {nb[-1].shape} at {nb[-1].start:.2f} ns")

            # 6. edit carrier and gain in the inspector
            pg_ = w.inspector.p_pulse
            pg_.widgets["carrier"].setText("%q1_freq"); pg_.widgets["carrier"].editingFinished.emit()
            pump()
            pg_.widgets["gain"].setText("0.35"); pg_.widgets["gain"].editingFinished.emit()
            pump(15)
            check(nb[-1].carrier == "%q1_freq" and nb[-1].gain == 0.35, "carrier/gain set from the inspector")
            check("env_" + nb[-1].name in w.yaml_view.toPlainText(), "DRAG envelope appears in the YAML")
            _shot(w, "r2_drop_and_inspector")

            # 7. double-click on the readout track adds a tone; copy it to acquisition
            row = next(r for r in view.rows if r.kind == "readout")
            p = vpos(QPointF(view.x_of(2600), row.y + row.h - 8))
            send_mouse(QEvent.Type.MouseButtonPress, p)
            send_mouse(QEvent.Type.MouseButtonRelease, p, Qt.MouseButton.NoButton)
            send_mouse(QEvent.Type.MouseButtonDblClick, p)
            send_mouse(QEvent.Type.MouseButtonRelease, p, Qt.MouseButton.NoButton)
            pump(15)
            tones = exp.readout_tracks[0].tones
            check(len(tones) == 3, f"tones on the readout track: {[x.name for x in tones]}")
            w.timeline.copy_to_acquisition(tones[-1].id)
            pump(15)
            errs = [str(i) for i in w.issues if i.level == "error"]
            check(any("generators" in e for e in errs), "3 tones on a 2-generator board is flagged: " + (errs[0] if errs else "-"))
            _shot(w, "r3_three_tones")

            # 8. delete key + undo
            view.select(tones[-1].id)
            view.setFocus()
            QApplication.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
            pump(15)
            check(len(exp.readout_tracks[0].tones) == 2 and len(exp.acq_tracks[0].windows) == 2, "tone and its window deleted")
            w.do_undo()
            pump(10)
            check(len(w.exp.readout_tracks[0].tones) == 3, "undo restores it")
            w.do_redo()
            pump(10)
            exp = w.exp
            view = w.timeline.view

            # 9. import the spin echo example
            w._import(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "yaml_experiment_configurations_examples", "spin_echo.yaml"))
            pump(20)
            check([t.title for t in w.exp.tracks()] == ["DAC 228_0", "DAC 228_0", "ADC 224_0"], f"tracks {[t.title for t in w.exp.tracks()]}")
            w.timeline.slider.setValue(60)
            pump(15)
            _shot(w, "r4_spin_echo")

            # 10. run against a simulated server that announces its hardware
            from FIREQ_GUI.tools.mock_server import MockServer

            hw = {"key": "lab_zcu216", "base": "ZCU216", "title": "Lab ZCU216 (4 gen)", "generators": 4, "acquisitions": 4}
            srv = MockServer(port=0, delay=0.003, hardware=hw)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            w.load_experiment(default_experiment("ZCU216"), reset_history=True)
            w.exp.variables[0].num = 41
            w.exp.shots = 500
            w.refresh_derived()
            w.timeline.view.fit()
            os.chdir(tmp)
            w.browser.set_root(os.path.join(tmp, "experiment_output"))
            w.browser.export_root = __import__("pathlib").Path(tmp) / "exported"
            w.host.setText("127.0.0.1")
            w.port.setValue(srv.port)
            w.toggle_connection()
            t0 = time.time()
            while (not w.bridge.connected or w.exp.board != "lab_zcu216") and time.time() - t0 < 10:
                pump(5)
            check(w.bridge.connected, "connected")
            b = w.exp.board_profile
            check(w.exp.board == "lab_zcu216" and b.generators == 4 and len(b.dacs) == 16, f"hardware from the handshake: {b.title} {b.generators} gen")
            check(w.exp_panel.board.currentData() == "lab_zcu216", "board list shows the server hardware")
            done = []
            w.bridge.worker.finished.connect(lambda d, ok, m: done.append((d, ok, m)))
            w.run_experiment()
            t0 = time.time()
            while not done and time.time() - t0 < 60:
                pump(5)
            check(done and done[0][1], f"run finished: {done}")
            pump(40)
            sel = w.browser.selected_experiments()
            check(len(sel) == 1 and sel[0].path.name == os.path.basename(done[0][0]), f"finished run selected in the browser: {sel}")
            _shot(w, "r5_after_run")

            # 11. plot it from the browser (existing FIREQ_PLOTTER functions, separate process)
            from FIREQ_GUI.core.plot_actions import ACTIONS

            os.environ["MPLBACKEND"] = "Agg"  # the plot process must not open windows in the test
            w.browser._save_figures = True
            proc = w.browser.plot(ACTIONS[0], sel)
            t0 = time.time()
            while proc.state() != proc.ProcessState.NotRunning and time.time() - t0 < 60:
                pump(5)
            pump(5)
            exported = __import__("FIREQ_GUI.core.plot_actions", fromlist=["x"]).export_target(sel[0], w.browser.root, w.browser.export_root)
            check(proc.exitCode() == 0, "plot process exit code 0")
            check((exported / "data.pkl").is_file() and (exported / "a_figure.png").is_file(), f"exported and plotted in {exported}")

            # 12. load a hardware description from a file
            hw_file = os.path.join(tmp, "hw.yaml")
            with open(hw_file, "w") as f:
                f.write("key: rfsoc_custom\nbase: RFSoC4x2\ntitle: RFSoC 4x2 custom\ngenerators: 3\nacquisitions: 3\n")
            from FIREQ_GUI.core.boards import load_hardware_file

            key, desc = load_hardware_file(hw_file)
            w.apply_hardware(key, desc, "file")
            pump(10)
            check(w.exp.board_profile.generators == 3 and w.exp.board == "rfsoc_custom", "hardware from file applied")
            check(all(w.exp.board_profile.dac(t.dac) for t in w.exp.drive_tracks), "tracks re-mapped on the new channels")
            w.saved_json = w.exp.to_json()
            w.close()
            pump(5)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
