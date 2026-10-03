#!/usr/bin/env python3
"""CloudLink — GTK4 front-end for the LAN engine (no internet, no server).

Needs: python3-gi, gir1.2-gtk-4.0 (and python3-cryptography for the core).
The engine runs on its own thread; every engine event is marshalled to the GTK main loop with GLib.idle_add.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from core.engine import Engine, EngineError  # noqa: E402

APP_ID = "app.cloudlink.Linux"


def human_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="CloudLink", default_width=760, default_height=560)
        self.engine = Engine(self._on_engine_event)
        self.engine.start()
        self.peers: list[dict] = []
        self.browse_peer: dict | None = None
        self.browse_path = "/"
        self._dialog: Gtk.Window | None = None
        self._transfer_rows: dict[str, tuple[Gtk.ListBoxRow, Gtk.Label, Gtk.ProgressBar]] = {}

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack)
        header.set_title_widget(switcher)

        self.stack.add_titled(self._build_devices(), "devices", "Devices")
        self.stack.add_titled(self._build_files(), "files", "Files")
        self.stack.add_titled(self._build_transfers(), "transfers", "Transfers")
        self.stack.add_titled(self._build_settings(), "settings", "Settings")
        self.set_child(self.stack)
        self.connect("close-request", self._on_close)

    # ------------------------------------------------------------------ engine → UI
    def _on_engine_event(self, kind: str, data: dict) -> None:
        GLib.idle_add(self._handle_event, kind, data)  # engine thread → GTK thread

    def _handle_event(self, kind: str, d: dict) -> bool:
        if kind == "peers":
            self.peers = d["peers"]
            self._refresh_peers()
        elif kind == "pairing":
            self._on_pairing(d)
        elif kind == "transfer":
            self._on_transfer(d)
        elif kind in ("toast", "get_error"):
            self._toast(d.get("text") or d.get("error", ""))
        elif kind == "peer_rejected":
            self._toast("That device no longer trusts this computer — pair again.")
        return False  # run once

    # ------------------------------------------------------------------ small helpers
    def _toast(self, text: str) -> None:
        self.toast_label.set_text(text)
        self.toast_revealer.set_reveal_child(True)
        GLib.timeout_add_seconds(5, lambda: (self.toast_revealer.set_reveal_child(False), False)[1])

    def _message(self, title: str, body: str, buttons: list[tuple[str, bool]] | None = None, on_response=None) -> None:
        if self._dialog:
            self._dialog.destroy()
        dlg = Gtk.Window(transient_for=self, modal=True, title=title, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        lbl = Gtk.Label(label=body, wrap=True, max_width_chars=48, xalign=0, use_markup=True)
        box.append(lbl)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        for text, value in (buttons or [("OK", True)]):
            b = Gtk.Button(label=text)
            if value:
                b.add_css_class("suggested-action")

            def clicked(_b, v=value):
                dlg.destroy()
                self._dialog = None
                if on_response:
                    on_response(v)

            b.connect("clicked", clicked)
            row.append(b)
        box.append(row)
        dlg.set_child(box)
        dlg.connect("close-request", lambda *_: (on_response(False) if on_response and self._dialog is dlg else None, False)[1])
        self._dialog = dlg
        dlg.present()

    # ------------------------------------------------------------------ devices tab
    def _build_devices(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.me_label = Gtk.Label(xalign=0)
        self.me_label.add_css_class("dim-label")
        page.append(self.me_label)
        self.peer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.peer_list.add_css_class("boxed-list")
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(self.peer_list)
        page.append(sc)

        page.append(Gtk.Label(label="Connect by IP (if your router blocks discovery)", xalign=0))
        row = Gtk.Box(spacing=8)
        self.ip_entry = Gtk.Entry(placeholder_text="192.168.1.20", hexpand=True)
        self.port_entry = Gtk.Entry(text="47616", width_chars=6)
        btn = Gtk.Button(label="Pair")
        btn.connect("clicked", self._on_pair_ip)
        for w in (self.ip_entry, self.port_entry, btn):
            row.append(w)
        page.append(row)

        self.toast_label = Gtk.Label(wrap=True)
        self.toast_revealer = Gtk.Revealer(child=self.toast_label)
        page.append(self.toast_revealer)
        return page

    def _refresh_peers(self) -> None:
        st = self.engine.state
        self.me_label.set_text(f"This computer: {st.device_name} · port {self.engine.port}")
        child = self.peer_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.peer_list.remove(child)
            child = nxt
        if not self.peers:
            self.peer_list.append(Gtk.Label(label="No devices found yet — open CloudLink on the other device.",
                                            margin_top=12, margin_bottom=12))
        for p in self.peers:
            row = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = Gtk.Label(label=p["name"] or p["id"], xalign=0)
            name.add_css_class("heading")
            info.append(name)
            sub = Gtk.Label(label=f'{"online" if p["online"] else "offline"} · {p["host"]}:{p["port"]}', xalign=0)
            sub.add_css_class("dim-label")
            info.append(sub)
            row.append(info)
            if p["paired"]:
                openb = Gtk.Button(label="Browse")
                openb.connect("clicked", lambda _b, peer=p: self._open_peer(peer))
                forget = Gtk.Button(label="Forget")
                forget.connect("clicked", lambda _b, peer=p: self._forget(peer))
                row.append(openb)
                row.append(forget)
            else:
                pair = Gtk.Button(label="Pair")
                pair.add_css_class("suggested-action")
                pair.connect("clicked", lambda _b, peer=p: self.engine.call(self.engine.start_pairing(peer["host"], peer["port"])))
                row.append(pair)
            self.peer_list.append(row)

    def _on_pair_ip(self, _b) -> None:
        host = self.ip_entry.get_text().strip()
        try:
            port = int(self.port_entry.get_text())
            assert 0 < port < 65536 and host
        except (ValueError, AssertionError):
            return self._toast("Enter a valid IP address and port.")
        self.engine.call(self.engine.start_pairing(host, port))

    def _forget(self, peer: dict) -> None:
        def go(ok: bool) -> None:
            if ok:
                self.engine.call(self.engine.unpair(peer["id"]))
                if self.browse_peer and self.browse_peer["id"] == peer["id"]:
                    self.browse_peer = None

        self._message("Forget device?", f'"{GLib.markup_escape_text(peer["name"])}" will lose access to your files.',
                      [("Cancel", False), ("Forget", True)], go)

    # ------------------------------------------------------------------ pairing dialogs
    def _on_pairing(self, d: dict) -> None:
        stage = d["stage"]
        answer = lambda ok: self.engine.call(self.engine.answer_pairing(ok))  # noqa: E731
        cancel = lambda _v: self.engine.call(self.engine.cancel_pairing())  # noqa: E731
        esc = GLib.markup_escape_text
        if stage == "connecting":
            self._message("Pairing", "Waiting for the other device to accept…", [("Cancel", False)], cancel)
        elif stage == "request":
            self._message("Pairing request",
                          f'"{esc(d["peer_name"])}" ({esc(d["host"])}) wants to pair. Only accept if you started it.',
                          [("Decline", False), ("Accept", True)], answer)
        elif stage == "verify":
            sas = d["sas"]
            self._message("Compare the code",
                          f'Pairing with "{esc(d["peer_name"])}". The SAME 6 digits must show on both devices:\n\n'
                          f'<span size="xx-large" font_family="monospace">{sas[:3]} {sas[3:]}</span>\n\n'
                          "If they differ, someone may be intercepting — choose “Different”.",
                          [("Different", False), ("Same", True)], answer)
        elif stage == "done":
            self._message("Paired", "The device is now trusted.")
        elif stage == "error":
            reasons = {"declined": "The pairing was declined or timed out.",
                       "sas_mismatch": "The confirmation did not match.",
                       "commit_mismatch": "The other device failed the key check. Try again.",
                       "busy": "The other device is already pairing."}
            self._message("Pairing failed", esc(reasons.get(d.get("reason", ""), str(d.get("reason", "")))))

    # ------------------------------------------------------------------ files tab
    def _build_files(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                       margin_start=8, margin_end=8)
        bar = Gtk.Box(spacing=6)
        self.up_btn = Gtk.Button(icon_name="go-up-symbolic")
        self.up_btn.connect("clicked", lambda _b: self._navigate(self.browse_path.rstrip("/").rpartition("/")[0] or "/"))
        self.path_label = Gtk.Label(label="Pick a paired device in the Devices tab", xalign=0, hexpand=True,
                                    ellipsize=Pango.EllipsizeMode.START)
        up = Gtk.Button(icon_name="document-send-symbolic", tooltip_text="Upload a file here")
        up.connect("clicked", self._on_upload)
        for w in (self.up_btn, self.path_label, up):
            bar.append(w)
        page.append(bar)
        self.file_list = Gtk.ListBox()
        self.file_list.connect("row-activated", self._on_file_activated)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(self.file_list)
        page.append(sc)
        self._entries: dict[Gtk.ListBoxRow, dict] = {}
        return page

    def _open_peer(self, peer: dict) -> None:
        self.browse_peer = peer
        self.stack.set_visible_child_name("files")
        self._navigate("/")

    def _navigate(self, path: str) -> None:
        if not self.browse_peer:
            return
        self.browse_path = path
        self.path_label.set_text(f'{self.browse_peer["name"]}:{path}')
        self.up_btn.set_sensitive(path != "/")
        self._set_files_message("Loading…")
        peer_id = self.browse_peer["id"]

        def work() -> None:
            try:
                entries = self.engine.call(self.engine.list_dir(peer_id, path)).result(40)
                GLib.idle_add(self._show_entries, peer_id, path, entries)
            except Exception as e:  # noqa: BLE001 - shown to the user
                GLib.idle_add(self._set_files_message, f"Couldn't load: {getattr(e, 'args', [e])[0] or type(e).__name__}")

        threading.Thread(target=work, name="cloudlink-list", daemon=True).start()

    def _set_files_message(self, text: str) -> bool:
        self._clear(self.file_list)
        self._entries = {}
        self.file_list.append(Gtk.Label(label=text, margin_top=16, margin_bottom=16))
        return False

    def _show_entries(self, peer_id: str, path: str, entries: list[dict]) -> bool:
        if not self.browse_peer or self.browse_peer["id"] != peer_id or self.browse_path != path:
            return False  # user navigated away meanwhile
        self._clear(self.file_list)
        self._entries = {}
        if not entries:
            self.file_list.append(Gtk.Label(label="Empty folder", margin_top=16, margin_bottom=16))
        for e in entries:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            box.append(Gtk.Image.new_from_icon_name("folder-symbolic" if e["d"] else "text-x-generic-symbolic"))
            box.append(Gtk.Label(label=e["n"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
            if not e["d"]:
                size = Gtk.Label(label=human_size(e["s"]))
                size.add_css_class("dim-label")
                box.append(size)
            row.set_child(box)
            self._entries[row] = e
            self.file_list.append(row)
        return False

    @staticmethod
    def _clear(lb: Gtk.ListBox) -> None:
        child = lb.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            lb.remove(child)
            child = nxt

    def _on_file_activated(self, _lb, row) -> None:
        e = self._entries.get(row)
        if not e or not self.browse_peer:
            return
        full = (self.browse_path.rstrip("/") + "/" + e["n"])
        if e["d"]:
            self._navigate(full)
        else:
            self._toast(f'Downloading {e["n"]}…')
            self.engine.call(self.engine.download(self.browse_peer["id"], full))
            self.stack.set_visible_child_name("transfers")

    def _on_upload(self, _b) -> None:
        if not self.browse_peer:
            return self._toast("Pick a device first.")
        peer_id, dest = self.browse_peer["id"], self.browse_path
        dlg = Gtk.FileChooserNative(title="Upload a file", transient_for=self, action=Gtk.FileChooserAction.OPEN)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                if f and f.get_path():
                    self.engine.call(self.engine.upload(peer_id, f.get_path(), dest))
                    self.stack.set_visible_child_name("transfers")

        dlg.connect("response", resp)
        self._chooser = dlg  # keep a reference alive
        dlg.show()

    # ------------------------------------------------------------------ transfers tab
    def _build_transfers(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                       margin_start=8, margin_end=8)
        self.transfer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(self.transfer_list)
        page.append(sc)
        clear = Gtk.Button(label="Clear finished", halign=Gtk.Align.END)
        clear.connect("clicked", self._clear_finished)
        page.append(clear)
        self._finished: set[str] = set()
        return page

    def _on_transfer(self, d: dict) -> None:
        tid = d["tid"]
        entry = self._transfer_rows.get(tid)
        if entry is None:
            row = Gtk.ListBoxRow(selectable=False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                          margin_start=8, margin_end=8)
            title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            bar = Gtk.ProgressBar()
            box.append(title)
            box.append(bar)
            row.set_child(box)
            self.transfer_list.prepend(row)
            entry = (row, title, bar)
            self._transfer_rows[tid] = entry
        row, title, bar = entry
        arrow = "⬇" if d["direction"] == "in" else "⬆"
        state = d["state"]
        size, done = d.get("size", 0), d.get("done", 0)
        if state == "active":
            title.set_text(f'{arrow} {d["name"]} — {human_size(done)} / {human_size(size)}')
            bar.set_fraction(min(1.0, done / size) if size else 0.0)
        else:
            self._finished.add(tid)
            bar.set_fraction(1.0 if state == "done" else bar.get_fraction())
            text = {"done": f'Done → {d.get("path", "")}' if d["direction"] == "in" else "Done",
                    "cancelled": "Cancelled"}.get(state, f'Failed: {d.get("error", "unknown")}')
            title.set_text(f'{arrow} {d["name"]} — {text}')

    def _clear_finished(self, _b) -> None:
        for tid in list(self._finished):
            row, _t, _b2 = self._transfer_rows.pop(tid)
            self.transfer_list.remove(row)
        self._finished.clear()

    # ------------------------------------------------------------------ settings tab
    def _build_settings(self) -> Gtk.Widget:
        st = self.engine.state
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=16, margin_bottom=16,
                       margin_start=16, margin_end=16)
        page.append(Gtk.Label(label="Device name", xalign=0))
        self.name_entry = Gtk.Entry(text=st.device_name)
        page.append(self.name_entry)
        page.append(Gtk.Label(label="Shared folder (what paired devices can browse)", xalign=0))
        self.root_label = Gtk.Label(label=str(st.get_share_root()), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        choose = Gtk.Button(label="Choose folder…")
        choose.connect("clicked", self._choose_root)
        row = Gtk.Box(spacing=8)
        self.root_label.set_hexpand(True)
        row.append(self.root_label)
        row.append(choose)
        page.append(row)
        up_row = Gtk.Box(spacing=8)
        up_row.append(Gtk.Label(label="Allow paired devices to upload into the shared folder", xalign=0, hexpand=True))
        self.upload_switch = Gtk.Switch(active=st.allow_uploads, valign=Gtk.Align.CENTER)
        up_row.append(self.upload_switch)
        page.append(up_row)
        save = Gtk.Button(label="Save", halign=Gtk.Align.START)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save_settings)
        page.append(save)
        note = Gtk.Label(label="Everything stays on your local network. Only devices you paired by comparing a code can connect.",
                         wrap=True, xalign=0)
        note.add_css_class("dim-label")
        page.append(note)
        return page

    def _choose_root(self, _b) -> None:
        dlg = Gtk.FileChooserNative(title="Shared folder", transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)

        def resp(d, r):
            if r == Gtk.ResponseType.ACCEPT and d.get_file():
                self.root_label.set_text(d.get_file().get_path())

        dlg.connect("response", resp)
        self._chooser = dlg
        dlg.show()

    def _save_settings(self, _b) -> None:
        st = self.engine.state
        st.device_name = self.name_entry.get_text().strip()[:48] or st.device_name
        st.share_root = self.root_label.get_text()
        st.allow_uploads = self.upload_switch.get_active()
        st.save()
        self._toast("Saved. Sharing changes apply to new connections; a new name shows after restart.")

    def _on_close(self, *_a) -> bool:
        self.engine.stop()
        return False


class App(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            try:
                win = Window(self)
            except EngineError as e:
                print(f"cloudlink: {e}", file=sys.stderr)
                self.quit()
                return
        win.present()


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
