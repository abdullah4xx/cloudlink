#!/usr/bin/env python3
"""CloudLink — GTK4 front-end for the LAN engine (no internet, no server). Light/dark follows the system.

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
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from core.engine import Engine, EngineError  # noqa: E402

APP_ID = "app.cloudlink.Linux"


def human_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


# ---------------------------------------------------------------------------------------------- theme
PALETTES = {
    "dark": dict(bg="#121317", surface="#1b1d23", surface2="#252830", text="#f1f2f4", dim="#9b9fab",
                 border="#2b2e37", shadow="0.35", accent="#2f8cff", accent_fg="#ffffff"),
    "light": dict(bg="#f3f4f8", surface="#ffffff", surface2="#eceef4", text="#1a1c22", dim="#6a6e7a",
                  border="#e1e4ec", shadow="0.08", accent="#1f7aff", accent_fg="#ffffff"),
}

CSS = """
window.cl, window.cl .cl-page { background: {bg}; color: {text}; }
.cl-top { background: {surface}; border-bottom: 1px solid {border}; }
.cl-brand { font-weight: 800; font-size: 17px; }
.cl-badge { background: {surface2}; color: {dim}; border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 700; }
.cl-card { background: {surface}; border: 1px solid {border}; border-radius: 18px; padding: 16px;
           box-shadow: 0 6px 18px rgba(0,0,0,{shadow}); }
.cl-title { font-size: 24px; font-weight: 800; }
.cl-h { font-size: 14px; font-weight: 800; }
.cl-dim { color: {dim}; font-size: 12px; }
.cl-ip { font-family: monospace; font-size: 22px; font-weight: 800; }
.cl-dot-on { color: #2fbf71; } .cl-dot-off { color: {dim}; }
.cl-folder { border-radius: 16px; padding: 14px 16px; color: #ffffff; border: none; min-height: 64px;
             box-shadow: 0 8px 18px rgba(0,0,0,{shadow}); }
.cl-folder label { color: #ffffff; }
.cl-folder-0 { background: linear-gradient(135deg, #2f9bff, #1676f3); }
.cl-folder-1 { background: linear-gradient(135deg, #7b4dff, #5b2fe0); }
.cl-folder-2 { background: linear-gradient(135deg, #46b04f, #2f8f3a); }
.cl-file { background: {surface}; border: 1px solid {border}; border-radius: 16px; padding: 0;
           box-shadow: 0 4px 12px rgba(0,0,0,{shadow}); }
.cl-thumb { background: {surface2}; border-radius: 15px 15px 0 0; min-height: 84px; color: {dim}; }
.cl-fname { font-weight: 700; font-size: 13px; }
button.cl-primary { background: {accent}; color: {accent_fg}; border-radius: 12px; border: none; box-shadow: none;
                    font-weight: 700; padding: 6px 16px; }
button.cl-soft { background: {surface2}; color: {text}; border-radius: 12px; border: none; box-shadow: none; padding: 6px 14px; }
button.cl-flat { background: transparent; color: {text}; border: none; box-shadow: none; border-radius: 12px; }
button.cl-flat:hover, button.cl-soft:hover { background: {border}; }
entry, searchentry { background: {surface2}; color: {text}; border-radius: 12px; border: 1px solid {border}; box-shadow: none; }
progressbar trough { background: {surface2}; border-radius: 99px; min-height: 8px; border: none; }
progressbar progress { background: {accent}; border-radius: 99px; min-height: 8px; border: none; }
.cl-toast { background: {text}; color: {bg}; border-radius: 12px; padding: 8px 14px; }
list, row { background: transparent; }
"""


def system_is_dark(default_prefer_dark: bool) -> bool:
    """Light/dark from the desktop: GNOME color-scheme, else the GTK theme name, else the GTK setting."""
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src and src.lookup("org.gnome.desktop.interface", True):
            scheme = Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
            if scheme == "prefer-dark":
                return True
            if scheme == "prefer-light":
                return False
    except Exception:  # noqa: BLE001
        pass
    st = Gtk.Settings.get_default()
    theme = (st.get_property("gtk-theme-name") or "").lower() if st else ""
    if "dark" in theme:
        return True
    return default_prefer_dark


def _clear(widget) -> None:  # noqa: ANN001 - ListBox / FlowBox / Box
    child = widget.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        widget.remove(child)
        child = nxt


def _label(text: str = "", *classes: str, xalign: float = 0, **kw) -> Gtk.Label:  # noqa: ANN003
    lb = Gtk.Label(label=text, xalign=xalign, **kw)
    for c in classes:
        lb.add_css_class(c)
    return lb


def _button(text: str = "", kind: str = "soft", icon: str | None = None) -> Gtk.Button:
    b = Gtk.Button(label=text) if not icon else Gtk.Button(icon_name=icon)
    b.add_css_class(f"cl-{kind}")
    return b


def _card(*classes: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("cl-card")
    for c in classes:
        box.add_css_class(c)
    return box


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="CloudLink", default_width=1120, default_height=720)
        self.add_css_class("cl")
        self.set_icon_name("cloudlink")
        self.engine = Engine(self._on_engine_event)
        self.engine.start()
        self.peers: list[dict] = []
        self.browse_peer: dict | None = None
        self.browse_path = "/"
        self.entries_all: list[dict] = []
        self._dialog: Gtk.Window | None = None
        self._transfer_rows: dict[str, tuple[Gtk.ListBoxRow, Gtk.Label, Gtk.ProgressBar]] = {}
        self._finished: set[str] = set()
        self._ips: list[str] = []

        # theme: follow the system, live
        st = Gtk.Settings.get_default()
        self._default_dark = bool(st.get_property("gtk-application-prefer-dark-theme")) if st else False
        self._provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._apply_theme()
        if st:
            st.connect("notify::gtk-theme-name", lambda *_: self._apply_theme())
        try:
            src = Gio.SettingsSchemaSource.get_default()
            if src and src.lookup("org.gnome.desktop.interface", True):
                Gio.Settings.new("org.gnome.desktop.interface").connect("changed::color-scheme",
                                                                         lambda *_: self._apply_theme())
        except Exception:  # noqa: BLE001
            pass

        self._build_top()
        body = Gtk.Box(spacing=0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.stack.add_named(self._scroll(self._build_home()), "home")
        self.stack.add_named(self._scroll(self._build_files()), "files")
        self.stack.add_named(self._scroll(self._build_settings()), "settings")
        body.append(self.stack)
        body.append(self._build_sidebar())
        overlay = Gtk.Overlay(child=body)
        self.toast_label = Gtk.Label(wrap=True)
        self.toast_label.add_css_class("cl-toast")
        self.toast_revealer = Gtk.Revealer(child=self.toast_label, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                           margin_bottom=20, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        overlay.add_overlay(self.toast_revealer)
        self.set_child(overlay)
        self.connect("close-request", self._on_close)
        self._refresh_me()
        self._refresh_peers()

    # ------------------------------------------------------------------ theme
    def _apply_theme(self) -> None:
        dark = system_is_dark(self._default_dark)
        st = Gtk.Settings.get_default()
        if st:
            st.set_property("gtk-application-prefer-dark-theme", dark)
        css = CSS
        for k, v in PALETTES["dark" if dark else "light"].items():
            css = css.replace("{" + k + "}", v)
        self._provider.load_from_string(css) if hasattr(self._provider, "load_from_string") else \
            self._provider.load_from_data(css.encode())

    @staticmethod
    def _scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        sc.set_child(child)
        return sc

    # ------------------------------------------------------------------ top bar
    def _build_top(self) -> None:
        header = Gtk.HeaderBar(show_title_buttons=True)
        header.add_css_class("cl-top")
        brand = Gtk.Box(spacing=8)
        brand.append(Gtk.Image.new_from_icon_name("cloudlink"))
        brand.append(_label("CloudLink", "cl-brand"))
        brand.append(_label("LAN", "cl-badge"))
        header.pack_start(brand)
        self.search = Gtk.SearchEntry(placeholder_text="Search files and devices", width_chars=38)
        self.search.connect("search-changed", lambda *_: self._on_search())
        header.set_title_widget(self.search)
        self.settings_btn = _button(icon="emblem-system-symbolic", kind="flat")
        self.settings_btn.set_tooltip_text("Settings")
        self.settings_btn.connect("clicked", lambda _b: self.stack.set_visible_child_name(
            "home" if self.stack.get_visible_child_name() == "settings" else "settings"))
        home = _button(icon="go-home-symbolic", kind="flat")
        home.set_tooltip_text("Devices")
        home.connect("clicked", lambda _b: self.stack.set_visible_child_name("home"))
        header.pack_end(self.settings_btn)
        header.pack_end(home)
        self.set_titlebar(header)

    def _on_search(self) -> None:
        if self.stack.get_visible_child_name() == "files":
            self._render_entries()
        else:
            self._refresh_peers()

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

    def _toast(self, text: str) -> None:
        self.toast_label.set_text(text)
        self.toast_revealer.set_reveal_child(True)
        GLib.timeout_add_seconds(5, lambda: (self.toast_revealer.set_reveal_child(False), False)[1])

    def _message(self, title: str, body: str, buttons: list[tuple[str, bool]] | None = None, on_response=None) -> None:  # noqa: ANN001
        if self._dialog:
            self._dialog.destroy()
        dlg = Gtk.Window(transient_for=self, modal=True, title=title, resizable=False)
        dlg.add_css_class("cl")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=20, margin_bottom=20,
                      margin_start=22, margin_end=22)
        box.append(Gtk.Label(label=body, wrap=True, max_width_chars=48, xalign=0, use_markup=True))
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        for text, value in (buttons or [("OK", True)]):
            b = _button(text, "primary" if value else "soft")

            def clicked(_b, v=value):  # noqa: ANN001
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

    # ------------------------------------------------------------------ sidebar (this computer / paired / transfers)
    def _build_sidebar(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18, margin_bottom=18,
                      margin_start=6, margin_end=18, width_request=320)

        me = _card()
        me.append(_label("This computer", "cl-dim"))
        self.me_name = _label("", "cl-h")
        me.append(self.me_name)
        self.me_ip = _label("—", "cl-ip", selectable=True)
        me.append(self.me_ip)
        self.me_note = _label("", "cl-dim", wrap=True)
        me.append(self.me_note)
        copy = _button("Copy address", "soft")
        copy.set_halign(Gtk.Align.START)
        copy.connect("clicked", self._copy_address)
        me.append(copy)
        col.append(me)

        paired = _card()
        paired.append(_label("Paired devices", "cl-h"))
        self.paired_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paired.append(self.paired_box)
        col.append(paired)

        tr = _card()
        top = Gtk.Box()
        top.append(_label("Transfers", "cl-h", hexpand=True))
        clear = _button("Clear", "flat")
        clear.connect("clicked", self._clear_finished)
        top.append(clear)
        tr.append(top)
        self.transfer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.transfer_hint = _label("Nothing yet.", "cl-dim")
        tr.append(self.transfer_hint)
        tr.append(self.transfer_list)
        col.append(tr)
        return self._scroll(col)

    def _refresh_me(self) -> None:
        st = self.engine.state
        self._ips = self.engine.local_ips()
        self.me_name.set_text(st.device_name)
        ip = self._ips[0] if self._ips else "unknown"
        self.me_ip.set_text(f"{ip}:{self.engine.port}")
        mdns = bool(self.engine.discovery and self.engine.discovery.mdns)
        extra = f" Other addresses: {', '.join(self._ips[1:])}." if len(self._ips) > 1 else ""
        self.me_note.set_text(("Discovery: mDNS + broadcast. " if mdns else "Discovery: broadcast only "
                               "(install python3-zeroconf for mDNS). ") + "Type this address on the other device if it can't find you." + extra)

    def _copy_address(self, _b) -> None:  # noqa: ANN001
        self.get_display().get_clipboard().set(self.me_ip.get_text())
        self._toast("Address copied.")

    # ------------------------------------------------------------------ home: nearby devices
    def _build_home(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=22, margin_bottom=22,
                       margin_start=26, margin_end=14)
        page.add_css_class("cl-page")
        page.append(_label("Devices", "cl-title"))
        self.home_sub = _label("", "cl-dim")
        page.append(self.home_sub)
        self.device_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                       min_children_per_line=1, column_spacing=14, row_spacing=14,
                                       homogeneous=True, valign=Gtk.Align.START)
        page.append(self.device_flow)

        ipc = _card()
        ipc.append(_label("Connect by IP", "cl-h"))
        ipc.append(_label("Use this if your router blocks discovery. The other device shows its address on its Devices screen.",
                          "cl-dim", wrap=True))
        row = Gtk.Box(spacing=8)
        self.ip_entry = Gtk.Entry(placeholder_text="192.168.1.20", hexpand=True)
        self.port_entry = Gtk.Entry(text="47616", width_chars=6)
        btn = _button("Pair", "primary")
        btn.connect("clicked", self._on_pair_ip)
        for w in (self.ip_entry, self.port_entry, btn):
            row.append(w)
        ipc.append(row)
        page.append(ipc)
        return page

    def _refresh_peers(self) -> None:
        q = self.search.get_text().strip().lower() if hasattr(self, "search") else ""
        self._refresh_me()
        shown = [p for p in self.peers if not q or q in (p["name"] or p["id"]).lower()]
        online = sum(1 for p in self.peers if p["online"])
        self.home_sub.set_text(f"{online} online · {sum(1 for p in self.peers if p['paired'])} paired")
        _clear(self.device_flow)
        if not shown:
            card = _card()
            card.append(_label("No devices found yet", "cl-h"))
            card.append(_label("Open CloudLink on your phone (same Wi-Fi). If it doesn't appear, connect by IP below.",
                               "cl-dim", wrap=True))
            self.device_flow.append(card)
        for p in shown:
            card = _card()
            top = Gtk.Box(spacing=10)
            top.append(Gtk.Image.new_from_icon_name("computer-symbolic" if "linux" in (p["name"] or "").lower()
                                                    else "phone-symbolic", pixel_size=28))
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            info.append(_label(p["name"] or p["id"], "cl-h", ellipsize=Pango.EllipsizeMode.END))
            sub = Gtk.Box(spacing=6)
            sub.append(_label("●", "cl-dot-on" if p["online"] else "cl-dot-off"))
            sub.append(_label(f'{"online" if p["online"] else "offline"} · {p["host"]}:{p["port"]}', "cl-dim"))
            info.append(sub)
            top.append(info)
            card.append(top)
            acts = Gtk.Box(spacing=8)
            if p["paired"]:
                openb = _button("Browse files", "primary")
                openb.connect("clicked", lambda _b, peer=p: self._open_peer(peer))
                forget = _button("Forget", "soft")
                forget.connect("clicked", lambda _b, peer=p: self._forget(peer))
                acts.append(openb)
                acts.append(forget)
            else:
                pair = _button("Pair", "primary")
                pair.connect("clicked", lambda _b, peer=p: self.engine.call(self.engine.start_pairing(peer["host"], peer["port"])))
                acts.append(pair)
            card.append(acts)
            self.device_flow.append(card)

        _clear(self.paired_box)
        paired = [p for p in self.peers if p["paired"]]
        if not paired:
            self.paired_box.append(_label("None yet.", "cl-dim"))
        for p in paired:
            r = Gtk.Box(spacing=8)
            r.append(_label("●", "cl-dot-on" if p["online"] else "cl-dot-off"))
            r.append(_label(p["name"] or p["id"], hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            r.append(_label("online" if p["online"] else "offline", "cl-dim"))
            self.paired_box.append(r)

    def _on_pair_ip(self, _b) -> None:  # noqa: ANN001
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

    # ------------------------------------------------------------------ files page
    def _build_files(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=22, margin_bottom=22,
                       margin_start=26, margin_end=14)
        page.add_css_class("cl-page")
        bar = Gtk.Box(spacing=8)
        self.up_btn = _button(icon="go-up-symbolic", kind="soft")
        self.up_btn.set_tooltip_text("Up one folder")
        self.up_btn.connect("clicked", lambda _b: self._navigate(self.browse_path.rstrip("/").rpartition("/")[0] or "/"))
        self.path_label = _label("Pick a paired device on the Devices page", "cl-dim", hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.START)
        up = _button("Upload here", "primary")
        up.connect("clicked", self._on_upload)
        for w in (self.up_btn, self.path_label, up):
            bar.append(w)
        page.append(bar)
        self.files_title = _label("Files", "cl-title")
        self.files_sub = _label("", "cl-dim")
        page.append(self.files_title)
        page.append(self.files_sub)
        self.folder_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                       min_children_per_line=2, column_spacing=12, row_spacing=12,
                                       homogeneous=True, valign=Gtk.Align.START)
        self.file_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                                     min_children_per_line=2, column_spacing=12, row_spacing=12,
                                     homogeneous=True, valign=Gtk.Align.START)
        page.append(self.folder_flow)
        page.append(self.file_flow)
        self.files_msg = _label("", "cl-dim", xalign=0.5, margin_top=24)
        page.append(self.files_msg)
        return page

    def _open_peer(self, peer: dict) -> None:
        self.browse_peer = peer
        self.search.set_text("")
        self.stack.set_visible_child_name("files")
        self._navigate("/")

    def _navigate(self, path: str) -> None:
        if not self.browse_peer:
            return
        self.browse_path = path
        self.files_title.set_text(self.browse_peer["name"])
        self.path_label.set_text(path)
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
        _clear(self.folder_flow)
        _clear(self.file_flow)
        self.entries_all = []
        self.files_sub.set_text("")
        self.files_msg.set_text(text)
        return False

    def _show_entries(self, peer_id: str, path: str, entries: list[dict]) -> bool:
        if not self.browse_peer or self.browse_peer["id"] != peer_id or self.browse_path != path:
            return False  # user navigated away meanwhile
        self.entries_all = entries
        self._render_entries()
        return False

    def _render_entries(self) -> None:
        q = self.search.get_text().strip().lower()
        items = [e for e in self.entries_all if not q or q in e["n"].lower()]
        folders = [e for e in items if e["d"]]
        files = [e for e in items if not e["d"]]
        _clear(self.folder_flow)
        _clear(self.file_flow)
        self.files_sub.set_text(f"{len(folders)} folders, {len(files)} files")
        self.files_msg.set_text("" if items else ("No matches" if q else "This folder is empty"))
        base = self.browse_path.rstrip("/")
        for i, e in enumerate(folders):
            b = Gtk.Button()
            b.add_css_class("cl-folder")
            b.add_css_class(f"cl-folder-{i % 3}")
            box = Gtk.Box(spacing=10)
            box.append(Gtk.Image.new_from_icon_name("folder-symbolic", pixel_size=26))
            box.append(_label(e["n"], "cl-h", hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            b.set_child(box)
            b.connect("clicked", lambda _b, full=base + "/" + e["n"]: self._navigate(full))
            self.folder_flow.append(b)
        for e in files:
            b = Gtk.Button()
            b.add_css_class("cl-file")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            thumb = Gtk.Box(halign=Gtk.Align.FILL, hexpand=True)
            thumb.add_css_class("cl-thumb")
            thumb.append(Gtk.Image.new_from_icon_name("text-x-generic-symbolic", pixel_size=36, hexpand=True))
            col.append(thumb)
            meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=8, margin_bottom=10,
                           margin_start=12, margin_end=12)
            meta.append(_label(e["n"], "cl-fname", ellipsize=Pango.EllipsizeMode.MIDDLE))
            meta.append(_label(human_size(e["s"]), "cl-dim"))
            col.append(meta)
            b.set_child(col)
            b.connect("clicked", lambda _b, ent=e: self._download(ent))
            self.file_flow.append(b)

    def _download(self, e: dict) -> None:
        if not self.browse_peer:
            return
        full = self.browse_path.rstrip("/") + "/" + e["n"]
        self._toast(f'Downloading {e["n"]}…')
        self.engine.call(self.engine.download(self.browse_peer["id"], full))

    def _on_upload(self, _b) -> None:  # noqa: ANN001
        if not self.browse_peer:
            return self._toast("Pick a device first.")
        peer_id, dest = self.browse_peer["id"], self.browse_path
        dlg = Gtk.FileChooserNative(title="Upload a file", transient_for=self, action=Gtk.FileChooserAction.OPEN)

        def resp(d, r):  # noqa: ANN001
            if r == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                if f and f.get_path():
                    self.engine.call(self.engine.upload(peer_id, f.get_path(), dest))

        dlg.connect("response", resp)
        self._chooser = dlg  # keep a reference alive
        dlg.show()

    # ------------------------------------------------------------------ transfers (sidebar)
    def _on_transfer(self, d: dict) -> None:
        tid = d["tid"]
        entry = self._transfer_rows.get(tid)
        if entry is None:
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6)
            title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            bar = Gtk.ProgressBar()
            box.append(title)
            box.append(bar)
            row.set_child(box)
            self.transfer_list.prepend(row)
            entry = (row, title, bar)
            self._transfer_rows[tid] = entry
            self.transfer_hint.set_visible(False)
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

    def _clear_finished(self, _b) -> None:  # noqa: ANN001
        for tid in list(self._finished):
            row, _t, _b2 = self._transfer_rows.pop(tid)
            self.transfer_list.remove(row)
        self._finished.clear()
        self.transfer_hint.set_visible(not self._transfer_rows)

    # ------------------------------------------------------------------ settings page
    def _build_settings(self) -> Gtk.Widget:
        st = self.engine.state
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=22, margin_bottom=22,
                       margin_start=26, margin_end=14)
        page.add_css_class("cl-page")
        page.append(_label("Settings", "cl-title"))
        card = _card()
        card.append(_label("Device name", "cl-h"))
        self.name_entry = Gtk.Entry(text=st.device_name)
        card.append(self.name_entry)
        card.append(_label("Shared folder (what paired devices can browse)", "cl-h", margin_top=8))
        self.root_label = _label(str(st.get_share_root()), ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        choose = _button("Choose folder…", "soft")
        choose.connect("clicked", self._choose_root)
        row = Gtk.Box(spacing=8)
        row.append(self.root_label)
        row.append(choose)
        card.append(row)
        up_row = Gtk.Box(spacing=8, margin_top=8)
        up_row.append(_label("Allow paired devices to upload into the shared folder", hexpand=True))
        self.upload_switch = Gtk.Switch(active=st.allow_uploads, valign=Gtk.Align.CENTER)
        up_row.append(self.upload_switch)
        card.append(up_row)
        save = _button("Save", "primary")
        save.set_halign(Gtk.Align.START)
        save.set_margin_top(8)
        save.connect("clicked", self._save_settings)
        card.append(save)
        page.append(card)
        page.append(_label("Everything stays on your local network. Only devices you paired by comparing a code can connect.",
                           "cl-dim", wrap=True))
        return page

    def _choose_root(self, _b) -> None:  # noqa: ANN001
        dlg = Gtk.FileChooserNative(title="Shared folder", transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)

        def resp(d, r):  # noqa: ANN001
            if r == Gtk.ResponseType.ACCEPT and d.get_file():
                self.root_label.set_text(d.get_file().get_path())

        dlg.connect("response", resp)
        self._chooser = dlg
        dlg.show()

    def _save_settings(self, _b) -> None:  # noqa: ANN001
        st = self.engine.state
        st.device_name = self.name_entry.get_text().strip()[:48] or st.device_name
        st.share_root = self.root_label.get_text()
        st.allow_uploads = self.upload_switch.get_active()
        st.save()
        self._toast("Saved. Sharing changes apply to new connections; a new name shows after restart.")

    def _on_close(self, *_a) -> bool:  # noqa: ANN002
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
