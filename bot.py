#!/usr/bin/env python3
"""
theoddsEater multi-sport line-movement alert bot (MLB, NHL, NBA).

Posts moneyline shifts to your Telegram channel in the same style as the
NFL/CFB alerts:

    ⚾ MLB · Tampa Bay Rays @ New York Yankees
    🕐 Sat 07:30 PM ET
    📈 FanDuel · New York Yankees: -122 → -135 (+2.5 pts, shortened)  |  open +114

Commands
    python bot.py --account      show your OddsPapi plan and remaining quota
    python bot.py --discover     find sport / tournament / market IDs, write config.json
    python bot.py --dry-run      run one cycle and print alerts instead of posting
    python bot.py --once         run one cycle and post to Telegram
    python bot.py                run forever (poll every POLL_MINUTES)

Secrets come from environment variables or a .env file next to this script:
    ODDSPAPI_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-4))

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
API = "https://api.oddspapi.io/v4"

SPORT_EMOJI = {"MLB": "⚾", "NHL": "🏒", "NBA": "🏀", "NFL": "🏈", "CFB": "🏈"}

# How discovery recognises each league.
LEAGUES = {
    "MLB": {"sport": r"baseball", "tournament": r"^(mlb|major league baseball)$"},
    "NHL": {"sport": r"(ice.?hockey|^hockey$)", "tournament": r"^(nhl|national hockey league)$"},
    "NBA": {"sport": r"basketball", "tournament": r"^(nba|national basketball association)$"},
}
WANTED_BOOKS = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM"}


# ----------------------------------------------------------------- helpers
def load_env():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def implied(american):
    """American odds -> implied probability (0-1)."""
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def fmt_american(a):
    a = int(round(a))
    return f"+{a}" if a > 0 else str(a)


def parse_american(player):
    """Pull an American price out of an OddsPapi player entry."""
    raw = player.get("priceAmerican")
    if raw not in (None, ""):
        try:
            return int(float(str(raw).replace("+", "")))
        except ValueError:
            pass
    dec = player.get("price")
    if dec and float(dec) > 1:
        dec = float(dec)
        return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))
    return None


def fmt_time(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(ET).strftime("%a %I:%M %p ET")


# ------------------------------------------------------------------ client
class OddsPapi:
    def __init__(self, key, min_gap=2.1):
        import requests
        self.s = requests.Session()
        self.key = key
        self.min_gap = min_gap  # docs: fixtures cooldown is 2000 ms
        self.last = 0.0
        self.calls = 0

    def get(self, path, **params):
        wait = self.min_gap - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        params["apiKey"] = self.key
        r = self.s.get(f"{API}/{path}", params=params, timeout=30)
        self.last = time.time()
        self.calls += 1
        if r.status_code == 429:
            time.sleep(5)
            r = self.s.get(f"{API}/{path}", params=params, timeout=30)
            self.last = time.time()
            self.calls += 1
        if r.status_code == 404 and "FIXTURE_NOT_FOUND" in r.text:
            return []  # no games scheduled (e.g. offseason) is not an error
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code} on /{path} params={ {k: v for k, v in params.items() if k != 'apiKey'} }: {r.text[:400]}")
        return r.json()


# --------------------------------------------------------------- discovery
def discover(api):
    cfg = {"threshold_pts": 1.5, "poll_minutes": 15, "max_requests_per_day": 400,
           "participant1_is_home": True,
           "bookmakers": {}, "leagues": {}}

    books = api.get("bookmakers")
    for b in books:
        slug = (b.get("slug") or "").lower()
        if slug in WANTED_BOOKS:
            cfg["bookmakers"][slug] = WANTED_BOOKS[slug]
    if not cfg["bookmakers"]:
        for b in books:
            slug = (b.get("slug") or "").lower()
            for want, nice in WANTED_BOOKS.items():
                if want in slug:
                    cfg["bookmakers"][slug] = nice
    print("Bookmakers matched:", cfg["bookmakers"] or "NONE (edit config.json by hand)")

    sports = api.get("sports")
    for league, rule in LEAGUES.items():
        sp = next((s for s in sports
                   if re.search(rule["sport"], (s.get("slug") or s.get("sportName") or ""), re.I)), None)
        if not sp:
            print(f"[{league}] sport not found in /sports")
            continue
        sid = sp["sportId"]
        tours = api.get("tournaments", sportId=sid)
        picked = [t for t in tours
                  if re.search(rule["tournament"], (t.get("tournamentName") or "").strip(), re.I)]
        print(f"[{league}] sportId={sid} tournaments:",
              [(t["tournamentId"], t["tournamentName"], t.get("categoryName")) for t in picked] or "NONE")
        market = pick_moneyline(api.get("markets", sportId=sid))
        if market:
            print(f"[{league}] moneyline market -> {market['marketId']} "
                  f"({market.get('marketName')}) outcomes={market['outcome_ids']}")
        cfg["leagues"][league] = {
            "enabled": bool(picked and market),
            "sportId": sid,
            "tournamentIds": [t["tournamentId"] for t in picked],
            "marketId": market["marketId"] if market else None,
            "outcomeIds": market["outcome_ids"] if market else [],
        }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print(f"\nWrote {CONFIG_PATH.name}. Open it, check each league looks right, then run: python bot.py --dry-run")
    print(f"Discovery used {api.calls} API requests.")


def pick_moneyline(markets):
    """Choose the full-game, no-handicap, 2-way winner market from the catalog."""
    best, best_score = None, -1
    for m in markets:
        name = (m.get("marketName") or "").lower()
        mtype = (m.get("marketType") or "").lower()
        period = (m.get("period") or "").lower()
        try:
            handicap = float(m.get("handicap") or 0)
        except (TypeError, ValueError):
            handicap = 1
        outs = m.get("outcomes") or []
        if isinstance(outs, dict):
            outs = [{"outcomeId": k, **(v if isinstance(v, dict) else {})} for k, v in outs.items()]
        real = [o for o in outs if str(o.get("outcomeName", "")).strip().lower() not in ("x", "draw", "tie")]
        if handicap != 0 or len(real) != 2:
            continue
        score = 0
        if mtype in ("moneyline", "h2h", "winner", "1x2", "ml"):
            score += 3
        if re.search(r"moneyline|money line|winner|to win|head.?to.?head|1x2|full time result", name):
            score += 3
        if period in ("fulltime", "full-time", "game", "match", "fullgame"):
            score += 2
        if re.search(r"incl|overtime|extra", name):
            score += 1
        if re.search(r"half|quarter|period|inning|1st|2nd|3rd|first|set|map|odd|even|total|spread|handicap", name):
            score -= 5
        if score > best_score:
            ids = sorted(real, key=lambda o: int(o["outcomeId"]))
            best_score = score
            best = {"marketId": m["marketId"], "marketName": m.get("marketName"),
                    "outcome_ids": [str(ids[0]["outcomeId"]), str(ids[1]["outcomeId"])]}
    return best if best_score > 0 else None


# ------------------------------------------------------------ movement core
def extract_prices(fixture, league_cfg, books):
    """-> {book_slug: (price_team1, price_team2)} using the configured moneyline market."""
    out = {}
    mkt_id = str(league_cfg["marketId"])
    o1, o2 = [str(x) for x in league_cfg["outcomeIds"]]
    for slug in books:
        bo = (fixture.get("bookmakerOdds") or {}).get(slug)
        if not bo or bo.get("suspended") or bo.get("bookmakerIsActive") is False:
            continue
        mk = (bo.get("markets") or {}).get(mkt_id)
        if not mk or mk.get("marketActive") is False:
            continue
        prices = []
        for oid in (o1, o2):
            players = ((mk.get("outcomes") or {}).get(oid) or {}).get("players") or {}
            p = next((v for v in players.values() if v.get("active", True)), None)
            prices.append(parse_american(p) if p else None)
        if None not in prices:
            out[slug] = tuple(prices)
    return out


def find_moves(league, fixture_id, names, start_iso, book_prices, books, state, threshold, home_first=True):
    """Compare against saved state; return alert lines and update state in place."""
    lines = []
    for slug, prices in book_prices.items():
        for idx, price in enumerate(prices):
            key = f"{fixture_id}|{slug}|{idx}"
            rec = state.get(key)
            if rec is None:
                state[key] = {"open": price, "last": price}
                continue
            delta = (implied(price) - implied(rec["last"])) * 100.0
            if abs(delta) >= threshold:
                shorter = delta > 0  # higher implied prob = price shortened
                lines.append(
                    f"{'📈' if shorter else '📉'} {books[slug]} · {names[idx]}: "
                    f"{fmt_american(rec['last'])} → {fmt_american(price)} "
                    f"({delta:+.1f} pts, {'shortened' if shorter else 'lengthened'})"
                    f"  |  open {fmt_american(rec['open'])}")
                rec["last"] = price
    if not lines:
        return None
    home, away = names if home_first else names[::-1]
    return (f"{SPORT_EMOJI.get(league, '🎯')} {league} · {away} @ {home}\n"
            f"🕐 {fmt_time(start_iso)}\n" + "\n".join(lines))


def league_fixtures(api, league, lc, state, now, refresh_hours=3):
    """Pre-game fixtures for a league, cached in state so most cycles cost 1 request."""
    cache = state.setdefault("_fx", {}).get(league)
    if not cache or now.timestamp() - cache["at"] > refresh_hours * 3600:
        fixtures = {}
        frm = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        to = (now + timedelta(hours=47)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for tid in lc["tournamentIds"]:
            for f in api.get("fixtures", sportId=lc["sportId"], tournamentId=tid,
                             **{"from": frm, "to": to, "hasOdds": "true"}):
                fixtures[str(f["fixtureId"])] = {
                    "p1": f.get("participant1Name", "Team 1"),
                    "p2": f.get("participant2Name", "Team 2"),
                    "start": f["startTime"]}
        cache = {"at": now.timestamp(), "fixtures": fixtures}
        state["_fx"][league] = cache
    # only games that have not started yet
    return {fid: f for fid, f in cache["fixtures"].items()
            if datetime.fromisoformat(f["start"].replace("Z", "+00:00")) > now}


def run_cycle(api, cfg, state, send, now=None, verbose=False):
    now = now or datetime.now(timezone.utc)
    sent = 0
    books = cfg["bookmakers"]
    for league, lc in cfg["leagues"].items():
        if not lc.get("enabled"):
            continue
        fixtures = league_fixtures(api, league, lc, state, now)
        if not fixtures:
            continue
        # OddsPapi accepts exactly one bookmaker per call on this plan:
        # fetch each book separately and merge them per fixture.
        merged = {}
        for slug in books:
            for fx_ in api.get("odds-by-tournaments",
                               tournamentIds=",".join(str(t) for t in lc["tournamentIds"]),
                               bookmaker=slug):
                m = merged.setdefault(str(fx_.get("fixtureId")), fx_)
                if m is not fx_:
                    m.setdefault("bookmakerOdds", {}).update(fx_.get("bookmakerOdds") or {})
        odds = list(merged.values())
        for fx in odds:
            meta = fixtures.get(str(fx.get("fixtureId")))
            if not meta:  # already started, or outside the 47-hour window
                continue
            # OddsPapi participant1 is the home side; alerts read "Away @ Home".
            names = (meta["p1"], meta["p2"])
            if verbose:
                found = extract_prices(fx, lc, books)
                print(f"  {league} {meta['p2']} @ {meta['p1']} ({fmt_time(meta['start'])}): " +
                      (", ".join(f"{books[b]} {fmt_american(a)}/{fmt_american(c)}" for b, (a, c) in found.items())
                       or "no moneyline prices found"))
            msg = find_moves(league, str(fx["fixtureId"]), names, meta["start"],
                             extract_prices(fx, lc, books), books, state,
                             cfg["threshold_pts"], home_first=cfg.get("participant1_is_home", True))
            if msg:
                send(msg)
                sent += 1
    return sent


# -------------------------------------------------------------------- main
def telegram_sender(token, chat_id):
    import requests

    def send(text):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                          timeout=30)
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text}, timeout=30)
        elif not r.ok:
            print("Telegram error:", r.status_code, r.text[:200], file=sys.stderr)
        time.sleep(1.2)
    return send


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def prune_state(state, cap=20000):
    keys = [k for k in state if not k.startswith("_")]
    for k in keys[: max(0, len(keys) - cap)]:
        state.pop(k, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--account", action="store_true", help="print your OddsPapi plan / quota")
    args = ap.parse_args()
    load_env()

    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        sys.exit("Set ODDSPAPI_KEY in .env first (see .env.example).")
    api = OddsPapi(key)

    if args.account:
        return print(json.dumps(api.get("account"), indent=2))
    if args.discover:
        return discover(api)
    if not CONFIG_PATH.exists():
        sys.exit("No config.json yet. Run: python bot.py --discover")
    cfg = json.loads(CONFIG_PATH.read_text())

    if args.dry_run:
        send = lambda text: print(text + "\n" + "-" * 40)  # noqa: E731
    else:
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (token and chat):
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (or use --dry-run).")
        send = telegram_sender(token, chat)

    state = load_state()
    while True:
        today = datetime.now().strftime("%Y-%m-%d")
        meta = state.setdefault("_meta", {"day": today, "used": 0})
        if meta["day"] != today:
            meta.update(day=today, used=0)
        if meta["used"] >= cfg.get("max_requests_per_day", 200):
            print("Daily request budget reached; skipping this cycle.")
        else:
            before = api.calls
            try:
                n = run_cycle(api, cfg, state, send, verbose=args.dry_run)
                print(f"[{datetime.now():%H:%M}] cycle ok, {n} alert(s), {api.calls - before} API calls")
            except Exception as e:  # keep the bot alive through API hiccups
                print("cycle failed:", repr(e), file=sys.stderr)
            meta["used"] += api.calls - before
            prune_state(state)
            if not args.dry_run:
                STATE_PATH.write_text(json.dumps(state))
        if args.once or args.dry_run:
            break
        time.sleep(cfg.get("poll_minutes", 15) * 60)


if __name__ == "__main__":
    main()
