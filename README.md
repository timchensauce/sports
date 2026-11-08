# 🎯 Bet or Pass

A Streamlit app that compares odds across sportsbooks using [The Odds API](https://the-odds-api.com), removes the vig, and tells you whether each line is a **BET**, **LEAN**, **CHECK** or **PASS**, with the reasoning.

**Covers:** NBA, NFL, NCAA men's basketball (NCAAB), NCAA football (NCAAF) and MLB, compiled into one ranked list of picks. Markets: moneylines, spreads, totals, plus a parlay checker. Leagues are toggleable and out-of-season leagues are skipped.

## Run locally
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your key
streamlit run app.py
```
No key? The app runs on built-in demo data.

## Deploy on Streamlit Community Cloud
1. Push this folder to a GitHub repo.
2. At share.streamlit.io, click **New app** and pick the repo with `app.py` as the main file.
3. Under **Advanced settings > Secrets**, add `ODDS_API_KEY = "your_key"`.

Never commit your key. `.gitignore` already excludes `secrets.toml`.

## How it works
1. Per-book vig removal (power method by default, which corrects longshot bias; proportional optional).
2. Weighted consensus probability (sharp books such as Pinnacle weighted 3x by default; add the EU region to get Pinnacle).
3. EV = fair p x decimal odds - 1; edge = fair p - implied p.
4. **BET**: EV >= your minimum, edge > book disagreement, confidence not low. **LEAN**: positive EV, weaker evidence, half stake. **CHECK**: EV above your suspicious threshold (likely stale line). **PASS**: no edge.
5. Stake: fractional Kelly, capped at a max % of bankroll.

## API credits
Each refresh costs (leagues x regions x markets) credits, so 5 leagues x 1 region x 3 markets = 15. Turn off leagues you don't need. Odds are cached for 5 minutes to protect your quota.

## Limitations
- Consensus is an estimate, not truth; the app finds the best price, it does not predict outcomes.
- Spreads and totals only compare when books list the same line.
- No player props, alternate lines or live betting.
- Parlay math assumes independent legs; same-game legs are flagged.

## Disclaimer
For information only, not financial or betting advice. Bet legally and only what you can afford to lose. US help line: 1-800-GAMBLER.
