#!/usr/bin/env python3
"""Headless CLI (works today without GTK): list / pair / ls / get / put.   python3 main_cli.py --help"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.engine import Engine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="cloudlink")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="show devices found on the network")
    sub.add_parser("serve", help="stay running so paired devices can browse this machine")
    p = sub.add_parser("pair"); p.add_argument("host"); p.add_argument("--port", type=int, default=47616)
    p = sub.add_parser("ls"); p.add_argument("device"); p.add_argument("path", nargs="?", default="/")
    p = sub.add_parser("get"); p.add_argument("device"); p.add_argument("path")
    p = sub.add_parser("put"); p.add_argument("device"); p.add_argument("file"); p.add_argument("dest", nargs="?", default="/")
    a = ap.parse_args()

    events: list = []
    cv = threading.Condition()

    def on_event(kind, data):
        with cv:
            events.append((kind, data)); cv.notify_all()

    def wait(kind, pred=lambda d: True, timeout=30):
        end = time.time() + timeout
        with cv:
            while True:
                for i, (k, d) in enumerate(events):
                    if k == kind and pred(d):
                        del events[i]; return d
                if time.time() > end:
                    raise TimeoutError(kind)
                cv.wait(0.2)

    eng = Engine(on_event); eng.start()

    def find(name):
        end = time.time() + 6
        while time.time() < end:
            snap = eng.call(_peers(eng)).result()
            for pr in snap:
                if name in (pr["id"], pr["name"]):
                    return pr
            time.sleep(0.5)
        sys.exit(f"device '{name}' not found")

    async def _peers(e):
        e._publish_peers()
        seen = e.discovery.peers
        return [dict(id=i, name=v["name"], host=v["host"], port=v["port"], paired=i in e.state.peers) for i, v in seen.items()]

    try:
        if a.cmd == "devices":
            time.sleep(3)
            print(f"this computer: {eng.state.device_name} at {', '.join(eng.local_ips()) or 'unknown IP'}:{eng.port}")
            for pr in eng.call(_peers(eng)).result():
                print(f'{pr["name"]:20} {pr["host"]}:{pr["port"]}  {"paired" if pr["paired"] else "not paired"}  id={pr["id"]}')
        elif a.cmd == "serve":
            print(f"serving {eng.state.get_share_root()} as '{eng.state.device_name}' on {', '.join(eng.local_ips()) or 'unknown IP'}:{eng.port} (Ctrl+C to stop)")
            while True:
                try:
                    ev = wait("pairing", lambda d: d["stage"] == "request", timeout=3600)
                    ok = input(f'Pair request from {ev["peer_name"]} ({ev["host"]}). Accept? [y/N] ').lower() == "y"
                    eng.call(eng.answer_pairing(ok)).result()
                    if ok:
                        v = wait("pairing", lambda d: d["stage"] == "verify")
                        print("Code:", v["sas"])
                        eng.call(eng.answer_pairing(input("Same code on the other device? [y/N] ").lower() == "y")).result()
                except TimeoutError:
                    pass
        elif a.cmd == "pair":
            eng.call(eng.start_pairing(a.host, a.port)).result()
            v = wait("pairing", lambda d: d["stage"] in ("verify", "error"), timeout=90)
            if v["stage"] == "error":
                sys.exit("pairing failed: " + v["reason"])
            print("Code:", v["sas"])
            eng.call(eng.answer_pairing(input("Same code on the other device? [y/N] ").lower() == "y")).result()
            r = wait("pairing", lambda d: d["stage"] in ("done", "error"), timeout=130)
            print("paired" if r["stage"] == "done" else "failed: " + r["reason"])
        elif a.cmd == "ls":
            for e in eng.call(eng.list_dir(find(a.device)["id"], a.path)).result(30):
                print(("d " if e["d"] else "- ") + f'{e["s"]:>12}  {e["n"]}')
        elif a.cmd == "get":
            eng.call(eng.download(find(a.device)["id"], a.path)).result(30)
            d = wait("transfer", lambda d: d["direction"] == "in" and d["state"] in ("done", "failed"), timeout=3600)
            print(d["state"], d.get("path", d.get("error", "")))
        elif a.cmd == "put":
            ok = eng.call(eng.upload(find(a.device)["id"], a.file, a.dest)).result(3600)
            print("done" if ok else "failed")
    finally:
        eng.stop()


if __name__ == "__main__":
    main()
