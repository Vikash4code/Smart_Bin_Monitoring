import time
import random
import requests
import sys

API_BASE = 'http://127.0.0.1:5000/update_level'
CONFIG_URL = 'http://127.0.0.1:5000/config'

# Per-bin update intervals (seconds) — change for faster / slower demo
bins = {
    'yellow': {'interval': 6,  'next_update': time.time()},
    'green':  {'interval': 7,  'next_update': time.time()},
    'blue':   {'interval': 8,  'next_update': time.time()}
}

CONFIG_POLL_INTERVAL = 4.0   # seconds between /config requests
_last_config_check = 0.0
_paused = False

# State
last_levels = {name: None for name in bins.keys()}
full_counts = {name: 0 for name in bins.keys()}  # how many consecutive updates at >=90

# Tweakable probabilities / params
P_BIG_JUMP = 0.12      # chance of a big jump (fast fill)
P_SMALL_DECREASE = 0.12   # chance of a small decrease (compaction/leak)
P_EMPTY_WHEN_FULL = 0.25  # base chance to empty when full on each cycle
FULL_COUNT_BEFORE_EMPTY = 2  # if full_count >= this, make empty more likely
OCCASIONAL_EMPTY_PROB = 0.02  # low chance to empty at any time

def debug(msg):
    # Print to stderr so server console isn't mixed; optional
    print(msg, file=sys.stderr)

def generate_next_level(name):
    """
    Returns next level for bin `name` (int 0..100), updates last_levels and full_counts.
    Behavior:
      - seed first value 10..35
      - most updates: small positive change (1..8)
      - P_BIG_JUMP: jump (10..30)
      - P_SMALL_DECREASE: small drop (-8..-2)
      - If level >= 90 => increment full_counts and consider emptying
      - Small chance OCCASIONAL_EMPTY_PROB to empty to simulate manual emptying
    """
    prev = last_levels.get(name)
    if prev is None:
        new = random.randint(10, 35)
        last_levels[name] = new
        full_counts[name] = 1 if new >= 90 else 0
        return new

    # If already very high, increase chance of emptying
    will_empty = False
    if prev >= 90:
        full_counts[name] += 1
        # base chance rises with how long it has been full
        empty_chance = P_EMPTY_WHEN_FULL + 0.15 * max(0, full_counts[name] - FULL_COUNT_BEFORE_EMPTY)
        if random.random() < empty_chance:
            will_empty = True
    else:
        full_counts[name] = 0
        # tiny chance to empty spontaneously
        if random.random() < OCCASIONAL_EMPTY_PROB:
            will_empty = True

    if will_empty:
        # empty to a small random level after collection
        new = random.randint(0, 18)
        last_levels[name] = new
        full_counts[name] = 0
        return new

    # otherwise decide event
    r = random.random()
    if r < P_BIG_JUMP:
        # big jump
        delta = random.randint(10, 30)
    elif r < P_BIG_JUMP + P_SMALL_DECREASE:
        # small decrease (settling/compaction)
        delta = -random.randint(2, 8)
    else:
        # gentle growth
        delta = random.randint(1, 8)

    new = prev + delta
    new = max(0, min(100, int(new)))

    # update last_levels and full count
    last_levels[name] = new
    if new >= 90:
        full_counts[name] += 1
    else:
        full_counts[name] = 0

    return new

def post_level(bin_color, level):
    payload = {'level': level}
    try:
        r = requests.post(f"{API_BASE}/{bin_color}", json=payload, timeout=6)
        return True, r.status_code, r.text
    except Exception as e:
        return False, None, str(e)

def fetch_config():
    global _paused
    try:
        r = requests.get(CONFIG_URL, timeout=3)
        j = r.json()
        _paused = bool(j.get('simulator_paused', False))
    except Exception:
        # keep previous paused state on error
        pass

def run_forever():
    global _last_config_check, _paused
    print("Simulator started — stochastic model. Polling /config every", CONFIG_POLL_INTERVAL, "s")
    print("Intervals:", {k: v['interval'] for k, v in bins.items()}, file=sys.stderr)

    while True:
        now = time.time()
        if now - _last_config_check >= CONFIG_POLL_INTERVAL:
            fetch_config()
            _last_config_check = now

        if _paused:
            # while paused, sleep a bit and continue to periodically poll config
            time.sleep(CONFIG_POLL_INTERVAL)
            continue

        for name, info in bins.items():
            if now >= info['next_update']:
                lvl = generate_next_level(name)
                ok, code, text = post_level(name, lvl)
                if ok:
                    print(f"[{name.upper()}] Level {lvl}% -> {code}")
                else:
                    print(f"[{name.upper()}] POST failed: {text}", file=sys.stderr)
                info['next_update'] = now + info['interval']
        time.sleep(1)

if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        print("\nSimulator stopped by user.")
