"""Offline test: fake OddsPapi responses in the documented shape, no network."""
import copy
from datetime import datetime, timedelta, timezone
import bot

NOW = datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=7, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

CFG = {"threshold_pts": 1.5, "bookmakers": {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM"},
       "leagues": {"MLB": {"enabled": True, "sportId": 13, "tournamentIds": [109],
                           "marketId": 131, "outcomeIds": ["131", "132"]}}}

def odds(dk, fd):
    def book(p):
        return {"bookmakerIsActive": True, "suspended": False, "markets": {"131": {"marketActive": True, "outcomes": {
            "131": {"players": {"0": {"active": True, "price": 0, "priceAmerican": str(p[0])}}},
            "132": {"players": {"0": {"active": True, "price": 0, "priceAmerican": str(p[1])}}}}}}}
    return [{"fixtureId": "id1", "bookmakerOdds": {"draftkings": book(dk), "fanduel": book(fd)}}]

class FakeAPI:
    def __init__(self): self.calls = 0; self.next = None; self.paths = []
    def get(self, path, **kw):
        self.calls += 1; self.paths.append(path)
        if path == "fixtures":
            return [{"fixtureId": "id1", "statusId": 0, "startTime": START,
                     "participant1Name": "New York Yankees", "participant2Name": "Tampa Bay Rays"}]
        return copy.deepcopy(self.next)

def test_flow():
    api, state, out = FakeAPI(), {}, []
    # cycle 1: opening prices recorded, nothing sent
    api.next = odds((-122, 102), (-120, 100))
    assert bot.run_cycle(api, CFG, state, out.append, now=NOW) == 0 and out == []
    # cycle 2: tiny move (below 1.5 pts) -> silent
    api.next = odds((-124, 104), (-120, 100))
    assert bot.run_cycle(api, CFG, state, out.append, now=NOW + timedelta(minutes=15)) == 0
    # cycle 3: DK moves -122 -> -135, FanDuel unchanged -> one message, DK lines only
    api.next = odds((-135, 114), (-120, 100))
    assert bot.run_cycle(api, CFG, state, out.append, now=NOW + timedelta(minutes=30)) == 1
    msg = out[0]; print(msg)
    assert msg.splitlines()[0] == "⚾ MLB · Tampa Bay Rays @ New York Yankees"
    assert msg.splitlines()[1].startswith("🕐 Tue 07:30 PM ET")
    assert "📈 DraftKings · New York Yankees: -122 → -135 (+2.5 pts, shortened)  |  open -122" in msg
    assert "📉 DraftKings · Tampa Bay Rays: +102 → +114 (-2.8 pts, lengthened)  |  open +102" in msg
    assert "FanDuel" not in msg
    # cycle 4: no further change -> silent (baseline moved to last alerted price)
    assert bot.run_cycle(api, CFG, state, out.append, now=NOW + timedelta(minutes=45)) == 0
    # fixtures were fetched once (cached), odds 4 times
    assert api.paths.count("fixtures") == 1 and api.paths.count("odds-by-tournaments") == 4
    # after first pitch the game is ignored
    api.next = odds((-200, 170), (-200, 170))
    assert bot.run_cycle(api, CFG, state, out.append, now=NOW + timedelta(hours=8)) == 0

def test_math():
    assert abs(bot.implied(-122) - 0.5495) < 1e-3 and abs(bot.implied(114) - 0.4673) < 1e-3
    assert bot.parse_american({"price": 2.5}) == 150 and bot.parse_american({"price": 1.5}) == -200
    assert bot.parse_american({"priceAmerican": "+114"}) == 114

def test_market_pick():
    cat = [
        {"marketId": 1, "marketName": "Total Runs", "marketType": "totals", "period": "fulltime", "handicap": 8.5,
         "outcomes": [{"outcomeId": 1, "outcomeName": "Over"}, {"outcomeId": 2, "outcomeName": "Under"}]},
        {"marketId": 2, "marketName": "1st Inning Winner", "marketType": "moneyline", "period": "1st", "handicap": 0,
         "outcomes": [{"outcomeId": 21, "outcomeName": "1"}, {"outcomeId": 22, "outcomeName": "2"}]},
        {"marketId": 131, "marketName": "Moneyline (incl. extra innings)", "marketType": "moneyline", "period": "fulltime",
         "handicap": 0, "outcomes": [{"outcomeId": 132, "outcomeName": "2"}, {"outcomeId": 131, "outcomeName": "1"}]},
    ]
    m = bot.pick_moneyline(cat)
    assert m["marketId"] == 131 and m["outcome_ids"] == ["131", "132"]

if __name__ == "__main__":
    test_math(); test_market_pick(); test_flow(); print("\nALL TESTS PASSED")
