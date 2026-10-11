"""Run the kit's practice runner unchanged, except that a transient platform failure does not end the run.

    python builder/cases_runner.py <the kit runner's own arguments>

The kit polls a simulation with plain requests, so one gateway error (502, 503, 504) while waiting loses a
simulation that is still running on the platform. Here a read is repeated; a request that creates something is
repeated only when the platform says it never processed it.
"""
import pathlib
import sys
import time
import urllib.error

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT / "tools"))

import freya

TRIES, WAIT_S = 20, 15
_api = freya.api


def api(method, path, body=None, **params):
    for attempt in range(1, TRIES + 1):
        try:
            out = _api(method, path, body, **params)
            if method == "POST" and path == "/api/v2/simulations" and isinstance(out, dict):
                # on record at once: if this process is lost, the simulation can still be found and scored
                print(f"[builder] simulation created: {(out.get('simulation') or out).get('id')}", flush=True)
            return out
        except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
            text = str(e)
            rejected = "temporarily unavailable" in text
            gateway = any(mark in text for mark in ("-> 502", "-> 503", "-> 504")) or not isinstance(e, RuntimeError)
            if attempt == TRIES or not (rejected or (gateway and method == "GET")):
                raise
            print(f"[builder] transient platform failure on {method} {path.split('?')[0]}, attempt {attempt}: {text[:120]}", flush=True)
            time.sleep(WAIT_S)


freya.api = api

import run_cases

_http = run_cases.http


def http(path, body=None):
    # The practice bank also timed out and answered with error pages on the event day. Opening a session or
    # reading a ledger changes nothing a second request could harm.
    for attempt in range(1, 7):
        try:
            return _http(path, body)
        except Exception as e:
            if attempt == 6:
                raise
            print(f"[builder] the practice bank did not answer on {path.split('/')[3] if path.count('/') > 2 else path}, attempt {attempt}: {type(e).__name__}", flush=True)
            time.sleep(10)


run_cases.http = http

if __name__ == "__main__":
    run_cases.main()
