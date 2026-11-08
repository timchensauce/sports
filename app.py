"""Bet or Pass: multi-book +EV finder built on The Odds API (https://the-odds-api.com)."""
import datetime as dt
import math

import pandas as pd
import requests
import streamlit as st

API = "https://api.the-odds-api.com/v4"
REGIONS = {"us": "US", "us2": "US (more books)", "uk": "UK", "eu": "EU (includes Pinnacle)", "au": "Australia"}
MARKETS = {"h2h": "Moneyline / 3-way", "spreads": "Spreads", "totals": "Totals"}
ICON = {"BET": "✅", "LEAN": "🟡", "CHECK": "⚠️", "PASS": "⛔"}

st.set_page_config(page_title="Bet or Pass", page_icon="🎯", layout="wide")


# ---------- odds helpers ----------
def to_dec(a):
    return 1 + a / 100 if a > 0 else 1 + 100 / -a


def to_am(d):
    return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))


def demo_events():
    t = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=6)).isoformat()
    # title, ML home, ML away, spread home, spread away, over, under (American)
    rows = {
        "draftkings": ("DraftKings", -130, 110, -110, -110, -110, -110),
        "fanduel": ("FanDuel", -125, 122, -108, -112, -112, -108),
        "betmgm": ("BetMGM", -135, 105, -115, -105, -105, -115),
        "caesars": ("Caesars", -128, 108, -110, -110, -110, -110),
        "pinnacle": ("Pinnacle", -138, 120, -106, -110, -108, -104),
    }
    bks = []
    for k, (n, *o) in rows.items():
        d = [to_dec(x) for x in o]
        bks.append({"key": k, "title": n, "markets": [
            {"key": "h2h", "outcomes": [{"name": "Boston Celtics", "price": d[0]}, {"name": "New York Knicks", "price": d[1]}]},
            {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": d[2], "point": -4.5},
                                            {"name": "New York Knicks", "price": d[3], "point": 4.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": d[4], "point": 220.5},
                                          {"name": "Under", "price": d[5], "point": 220.5}]}]})
    return [{"id": "demo", "home_team": "Boston Celtics", "away_team": "New York Knicks",
             "commence_time": t, "bookmakers": bks}]


@st.cache_data(ttl=600, show_spinner=False)
def get_sports(key):
    r = requests.get(f"{API}/sports", params={"apiKey": key}, timeout=20)
    r.raise_for_status()
    return [s for s in r.json() if s.get("active") and not s.get("has_outrights")]


@st.cache_data(ttl=300, show_spinner="Fetching odds…")
def get_odds(key, sport, regions, markets):
    r = requests.get(f"{API}/sports/{sport}/odds", timeout=30, params={
        "apiKey": key, "regions": regions, "markets": markets,
        "oddsFormat": "decimal", "dateFormat": "iso"})
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining")


# ---------- model ----------
def groups(e):
    """Group outcomes into comparable markets: (market, line) -> {(book_key, book_title): {pick: price}}."""
    out = {}
    for bk in e.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"] not in MARKETS:
                continue
            for o in m["outcomes"]:
                if m["key"] == "spreads":
                    line = o["point"] if o["name"] == e["home_team"] else -o["point"]
                    label = f'{o["name"]} {o["point"]:+g}'
                elif m["key"] == "totals":
                    line, label = o["point"], f'{o["name"]} {o["point"]:g}'
                else:
                    line, label = None, o["name"]
                out.setdefault((m["key"], line), {}).setdefault((bk["key"], bk["title"]), {})[label] = o["price"]
    return out


def build(events, sharp, sharp_w, min_books):
    rows = []
    for e in events:
        for (mkt, _line), books in groups(e).items():
            n = max(len(p) for p in books.values())
            full = {b: p for b, p in books.items() if len(p) == n and n >= 2}
            if not full:
                continue
            ref = set(next(iter(full.values())))
            full = {b: p for b, p in full.items() if set(p) == ref}
            if len(full) < min_books:
                continue
            w = {b: (sharp_w if b[0] in sharp else 1.0) for b in full}
            sw = sum(w.values())
            nv = {}
            for b, p in full.items():  # proportional vig removal per book
                tot = sum(1 / x for x in p.values())
                nv[b] = {l: (1 / x) / tot for l, x in p.items()}
            cons = {l: sum(w[b] * nv[b][l] for b in full) / sw for l in ref}
            sd = sum(math.sqrt(sum(w[b] * (nv[b][l] - cons[l]) ** 2 for b in full) / sw) for l in ref) / len(ref)
            conf = "high" if len(full) >= 4 and sd < .015 else "medium" if len(full) >= 3 and sd < .03 else "low"
            used = any(b[0] in sharp for b in full)
            for (bk, bt), p in full.items():
                for l, d in p.items():
                    rows.append(dict(
                        game=f'{e["away_team"]} @ {e["home_team"]}', start=e["commence_time"], market=mkt,
                        pick=l, book=bt, book_key=bk, price=d, american=to_am(d), implied=1 / d, fair=cons[l],
                        edge=cons[l] - 1 / d, ev=cons[l] * d - 1, sd=sd, n_books=len(full), conf=conf, sharp_used=used))
    return pd.DataFrame(rows)


def judge(r, s):
    if r["ev"] < 0.005:
        return "PASS"
    if r["ev"] > s["max_ev"]:
        return "CHECK"
    if r["ev"] >= s["min_ev"] and r["edge"] > r["sd"] and r["conf"] != "low":
        return "BET"
    return "LEAN" if r["ev"] >= s["min_ev"] else "PASS"


def stake(r, s):
    if r["verdict"] not in ("BET", "LEAN"):
        return 0.0
    k = r["ev"] / (r["price"] - 1) * s["kelly"] * s["bank"]
    k = min(k, s["bank"] * s["max_pct"] / 100)
    return k * (1 if r["verdict"] == "BET" else 0.5)


def explain(r):
    pts = [
        f'**Fair price:** {r["fair"]:.1%} win chance, from the vig-free consensus of {r["n_books"]} books'
        + (" (sharp books weighted higher)." if r["sharp_used"] else "."),
        f'**The line:** {r["book"]} pays {r["american"]:+d}, which implies {r["implied"]:.1%}. '
        f'That is a {r["edge"] * 100:.1f}-point edge and {r["ev"]:+.1%} EV (${r["ev"] * 100:.2f} per $100).',
        f'**Confidence {r["conf"]}:** books disagree by about {r["sd"] * 100:.1f} points. '
        + ("The edge is bigger than that disagreement." if r["edge"] > r["sd"]
           else "The edge is smaller than normal disagreement, so it may be noise."),
    ]
    if r["verdict"] == "CHECK":
        pts.append("**Warning:** EV this high usually means a stale line, a typo, or news already priced in. "
                   "Confirm the price in the book's app and check injuries before betting.")
    if r["verdict"] == "LEAN":
        pts.append("**Why only a lean:** positive EV, but low confidence or the edge is within normal book noise. Half stake.")
    if r["verdict"] == "BET":
        pts.append(f'**Sizing:** ${r["stake"]:,.0f} (fractional Kelly, capped by your max stake).')
    return "\n\n".join(pts)


# ---------- sidebar ----------
try:
    secret_key = st.secrets.get("ODDS_API_KEY", "")
except Exception:
    secret_key = ""

with st.sidebar:
    st.header("Setup")
    key = st.text_input("Odds API key", value=secret_key, type="password",
                        help="Free key at the-odds-api.com. Leave blank to use demo data.")
    regions = st.multiselect("Regions", list(REGIONS), default=["us"], format_func=REGIONS.get,
                             help="Add EU to include Pinnacle, the sharpest reference price.")
    markets = st.multiselect("Markets", list(MARKETS), default=list(MARKETS), format_func=MARKETS.get)
    st.caption(f"API cost: {max(1, len(regions)) * max(1, len(markets))} credit(s) per refresh.")
    st.subheader("Your rules")
    bank = st.number_input("Bankroll ($)", 10.0, value=1000.0, step=100.0)
    min_ev = st.number_input("Minimum EV to bet (%)", 0.0, 20.0, 2.0, 0.5) / 100
    kelly = st.number_input("Kelly fraction", 0.05, 1.0, 0.25, 0.05)
    max_pct = st.number_input("Max stake (% of bankroll)", 0.5, 25.0, 3.0, 0.5)
    max_ev = st.number_input("Flag EV above (%) as suspicious", 5.0, 100.0, 15.0, 1.0) / 100
    min_books = st.number_input("Min books per market", 2, 10, 3)
    sharp = st.multiselect("Sharp book keys", ["pinnacle", "matchbook", "betfair_ex_eu", "betfair_ex_uk", "lowvig"],
                           default=["pinnacle"])
    sharp_w = st.slider("Sharp book weight", 1.0, 6.0, 3.0, 0.5)
    hide_started = st.checkbox("Hide games already started", value=True)

S = dict(bank=bank, min_ev=min_ev, kelly=kelly, max_pct=max_pct, max_ev=max_ev)

# ---------- data ----------
st.title("🎯 Bet or Pass")
st.caption("Multi-book +EV finder for moneylines (incl. 3-way), spreads, totals and parlays.")

remaining = None
if not key:
    st.info("Demo mode: showing sample data. Add your Odds API key in the sidebar for live odds.")
    events = demo_events()
else:
    try:
        sports = get_sports(key)
        labels = {s["key"]: f'{s["group"]}: {s["title"]}' for s in sports}
        keys = list(labels)
        sport = st.selectbox("Sport", keys, index=keys.index("basketball_nba") if "basketball_nba" in keys else 0,
                             format_func=labels.get)
        events, remaining = get_odds(key, sport, ",".join(regions or ["us"]), ",".join(markets or ["h2h"]))
    except requests.HTTPError as ex:
        code = ex.response.status_code
        st.error("Invalid API key." if code == 401 else "Out of API credits." if code == 429
                 else f"Odds API error {code}: {ex.response.text[:200]}")
        st.stop()
    except requests.RequestException as ex:
        st.error(f"Network error: {ex}")
        st.stop()

if hide_started:
    now = dt.datetime.now(dt.timezone.utc)
    events = [e for e in events if dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > now]

df = build(events, set(sharp), sharp_w, int(min_books))
if df.empty:
    st.warning("No markets with enough books to compare. Add regions, lower “Min books per market”, or pick another sport.")
    st.stop()

df["verdict"] = df.apply(lambda r: judge(r, S), axis=1)
df["stake"] = df.apply(lambda r: stake(r, S), axis=1)

my_books = st.multiselect("Books I can bet at", sorted(df["book"].unique()), default=sorted(df["book"].unique()),
                          help="Fair prices use every book. Picks are limited to the books you choose.")
mine = df[df["book"].isin(my_books)]
picks = (mine[mine["verdict"] != "PASS"].sort_values("ev", ascending=False)
         .drop_duplicates(["game", "market", "pick"]))

c = st.columns(4)
c[0].metric("Games", df["game"].nunique())
c[1].metric("Lines compared", len(df))
c[2].metric("BET picks", int((picks["verdict"] == "BET").sum()))
c[3].metric("API credits left", remaining or "demo")

t1, t2, t3, t4 = st.tabs(["Picks", "All lines", "Parlay checker", "How it works"])

with t1:
    if picks.empty:
        st.info("Nothing clears your bar right now. Passing is a valid answer.")
    for r in picks.head(25).to_dict("records"):
        with st.container(border=True):
            a, b = st.columns([1, 4])
            a.markdown(f'### {ICON[r["verdict"]]} {r["verdict"]}')
            a.caption(f'Stake ${r["stake"]:,.0f}' if r["stake"] else "No stake")
            b.markdown(f'**{r["pick"]}** at **{r["book"]} ({r["american"]:+d})** · {MARKETS[r["market"]]}')
            b.caption(f'{r["game"]} · {pd.to_datetime(r["start"]).strftime("%a %b %d %H:%M UTC")}')
            b.markdown(explain(r))

with t2:
    f = st.selectbox("Market", ["all"] + list(MARKETS), format_func=lambda x: "All markets" if x == "all" else MARKETS[x])
    v = (df if f == "all" else df[df["market"] == f]).sort_values("ev", ascending=False).copy()
    for col in ("implied", "fair", "edge", "ev"):
        v[col] = v[col] * 100
    st.dataframe(v[["game", "market", "pick", "book", "american", "implied", "fair", "edge", "ev", "conf", "verdict", "stake"]],
                 width="stretch", hide_index=True, column_config={
                     "implied": st.column_config.NumberColumn("Implied %", format="%.1f"),
                     "fair": st.column_config.NumberColumn("Fair %", format="%.1f"),
                     "edge": st.column_config.NumberColumn("Edge pts", format="%.1f"),
                     "ev": st.column_config.NumberColumn("EV %", format="%.1f"),
                     "stake": st.column_config.NumberColumn("Stake $", format="%.0f")})

with t3:
    st.caption("Parlays multiply each book's margin, so most are negative EV. Check the numbers before you build one.")
    cols = {"Game": "str", "Leg": "str", "American odds": "float", "Fair win %": "float"}
    if "legs" not in st.session_state:
        st.session_state.legs = pd.DataFrame({k: pd.Series(dtype=t) for k, t in cols.items()})
    if st.button("Fill from top BET picks (one per game)"):
        top = picks[picks["verdict"] == "BET"].drop_duplicates("game").head(4)
        st.session_state.legs = pd.DataFrame({"Game": top["game"], "Leg": top["pick"],
                                              "American odds": top["american"].astype(float),
                                              "Fair win %": (top["fair"] * 100).round(1)})
        st.session_state.pop("legs_editor", None)
        st.rerun()
    legs = st.data_editor(st.session_state.legs, num_rows="dynamic", width="stretch",
                          hide_index=True, key="legs_editor")
    L = legs.dropna(subset=["American odds", "Fair win %"])
    L = L[(L["American odds"].abs() >= 100) & L["Fair win %"].between(0.1, 99.9)]
    if len(L) < 2:
        st.info("Add at least two legs with odds and a fair win % to evaluate a parlay.")
    else:
        naive = L["American odds"].map(to_dec).prod()
        fair = (L["Fair win %"] / 100).prod()
        offered = st.number_input("Sportsbook parlay price (American). 0 = multiply the legs", value=0, step=10)
        d = to_dec(offered) if abs(offered) >= 100 else naive
        ev = fair * d - 1
        m = st.columns(4)
        m[0].metric("Parlay odds", f"{to_am(d):+d}")
        m[1].metric("Fair win chance", f"{fair:.1%}")
        m[2].metric("Fair odds", f"{to_am(1 / fair):+d}")
        m[3].metric("EV", f"{ev:+.1%}")
        games = L["Game"].fillna("").str.strip()
        if games[games != ""].duplicated().any():
            st.warning("Two legs share a game. They are correlated, so multiplying probabilities is unreliable.")
        sz = min(max(ev, 0) / (d - 1) * kelly * bank, bank * max_pct / 100)
        if ev >= min_ev:
            st.success(f"✅ BET: positive EV after margin. Suggested stake ${sz:,.0f}.")
        elif ev > 0:
            st.warning("🟡 Marginal. EV is positive but under your minimum.")
        else:
            st.error("⛔ PASS: the parlay is negative EV. Betting the best single legs separately is usually better.")

with t4:
    st.markdown("""
**1. Remove the vig.** Each book's odds are converted to implied probabilities and normalized so each market sums to 100%.

**2. Build the fair price.** Vig-free probabilities are averaged across books. Sharp books (default Pinnacle) get extra weight.

**3. Compare every line.** EV = fair probability × decimal odds − 1. Edge = fair probability − implied probability.

**4. Judge.** BET needs EV above your minimum, an edge bigger than book disagreement, and medium or high confidence.
LEAN gets half stake. CHECK means EV above your suspicious threshold, so verify the line. PASS means no edge.

**5. Size.** Fractional Kelly on the fair probability, capped at your max stake.

**Limits.** The consensus is an estimate, not truth. Spreads and totals are only compared when books offer the same line.
Player props, alternate lines and live markets are not covered. This is a decision aid, not financial advice.
If gambling stops being fun, call 1-800-GAMBLER.
""")
