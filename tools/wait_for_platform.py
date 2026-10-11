"""Development aid: wait until the Freya API answers again, then exit.

    python tools/wait_for_platform.py [max_minutes]
"""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
import freya_platform as fp

fp.load_env()
limit = float(sys.argv[1]) if len(sys.argv) > 1 else 25
started = time.time()
while True:
    t = time.time()
    try:
        fp.health()
        print(f"platform answers after {int(time.time() - started)} s of waiting (request took {time.time() - t:.1f} s)")
        sys.exit(0)
    except Exception as e:
        if time.time() - started > limit * 60:
            print(f"platform still not answering after {limit} minutes: {str(e)[:120]}")
            sys.exit(1)
        time.sleep(20)
