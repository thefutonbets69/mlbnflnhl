# theoddsEater line-movement alerts: MLB, NHL, NBA

Posts moneyline shifts to your Telegram channel in the same layout as your NFL/CFB alerts:

    ⚾ MLB · Tampa Bay Rays @ New York Yankees
    🕐 Tue 07:30 PM ET
    📈 DraftKings · New York Yankees: -122 → -135 (+2.5 pts, shortened)  |  open -122

It records the first price it sees for each team at DraftKings, FanDuel and BetMGM as the
open, then posts whenever the implied probability moves 1.5 points or more since the last
alert. Games that have already started are ignored.

Status: the alert logic is tested offline (run `python test_bot.py`). It has NOT been run
against the live OddsPapi feed yet, so do the dry run below before trusting it.

## Option A: run it free on GitHub (no computer left on)

1. Create a new repository on GitHub and upload every file in this folder, including the
   `.github` folder. A public repo gets unlimited free Actions minutes; a private repo has
   a monthly cap that a 15-minute schedule can exceed.
2. In the repo: Settings > Secrets and variables > Actions > New repository secret. Add:
   - `ODDSPAPI_KEY`: your OddsPapi key
   - `TELEGRAM_BOT_TOKEN`: the token of your alert bot (the one already admin in the channel)
   - `TELEGRAM_CHAT_ID`: `@thefutonbets`
3. Open the Actions tab, pick "line-movement-alerts", press "Run workflow".
   The first run finds the league and market IDs and saves them as `config.json`.
   Open the run log and check the three leagues look right (see "Check the config").
4. After that it runs by itself every 15 minutes. The first cycle only records opening
   prices, so alerts start from the second cycle.

GitHub pauses scheduled workflows in a repo with no activity for 60 days; it emails you
and one click turns it back on.

## Option B: run it on your computer

    pip install requests
    copy .env.example .env        (then put your key and token in .env)
    python bot.py --account       shows your OddsPapi plan and quota
    python bot.py --discover      writes config.json
    python bot.py --dry-run       prints the games and prices it found, posts nothing
    python bot.py                 runs forever, checking every 15 minutes

## Check the config

`config.json` after discovery should show, for MLB, NHL and NBA: `"enabled": true`, one
tournament ID, a market ID and two outcome IDs. Then `python bot.py --dry-run` lists each
game with a price pair per book. Compare one game with the sportsbook app:

- Prices wrong or missing: the market ID is not the moneyline. Look at the discovery
  printout and put the right `marketId` / `outcomeIds` in `config.json`.
- Teams swapped (favorite showing the underdog price): swap the two `outcomeIds`.
- "Away @ Home" reads backwards: set `"participant1_is_home": false`.

## Settings (config.json)

- `threshold_pts`: size of move that triggers an alert (1.5 matches your NFL/CFB bot).
- `poll_minutes`: how often the local version checks. On GitHub change the cron line.
- `max_requests_per_day`: safety cap so the bot can't burn your OddsPapi quota.
  Each check costs about 1 request per league, so 3 leagues every 15 minutes is roughly
  310 requests a day. If your NFL/CFB bot uses the same key, they share the quota.
- Set `"enabled": false` on a league to turn it off.
