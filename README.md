# ACC Reality Check

**DS2002 · Fall 2026 capstone**: Which ACC men's basketball teams are as good as their records say, and which have built their records on easy opponents?

We combine Bart Torvik's team ratings with ESPN game results and box scores, store everything in a SQLite database (`acc.db`), answer six questions with SQL, and publish the results as a FastAPI web API.

## Team: The Cleaners

| Member | Role |
|---|---|
| Karen Tran | Ratings CSV and name crosswalk; SQLite schema and queries |
| Zach Bernas | ESPN pulls and box scores; API and PythonAnywhere |

## Links

- **Final notebook:** [`ACC Reality Check.ipynb`](ACC%20Reality%20Check.ipynb) <!-- TODO: match the final notebook's file name -->
- **Live API:** https://USERNAME.pythonanywhere.com (interactive docs at [`/docs`](https://USERNAME.pythonanywhere.com/docs)) <!-- TODO -->
- **Presentation video:** TODO: add the link (test it in a private browser window)
- **Reflection:** Section 7 at the end of the final notebook

## Data sources

| Source | URL | Pulls used for the final results |
|---|---|---|
| Bart Torvik team ratings (CSV) | https://barttorvik.com/2027_team_results.csv | Snapshots dated 2026-11-20, … <!-- TODO: list every snapshot date --> |
| ESPN scoreboard (JSON) | https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=2&dates=YYYYMMDD&limit=200 | One file per date, 2026-11-01 through TODO |
| ESPN game summary (JSON) | https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event=GAME_ID | One file per finished game |

Torvik returns `403 Forbidden` to scripted requests from Colab, so each ratings snapshot is downloaded in a browser and committed to `data/raw/torvik/torvik_YYYY-MM-DD.csv`, using the date it was downloaded. The notebook restores these files from this repository when it runs.

## Repository layout

```
.
├── ACC Reality Check.ipynb      final notebook: extract, transform, load, questions, decision log, API calls, reflection
├── acc.db                       the SQLite database the answers and the API come from
├── api/
│   ├── main.py                  FastAPI application (reads only acc.db)
│   └── requirements.txt
├── data/raw/torvik/             committed Torvik snapshots, one CSV per download date
├── notebooks/08-capstone-acc/   studio and check-in notebooks
└── README.md
```

`data/raw/scoreboard/` and `data/raw/summaries/` are not committed (see `.gitignore`). The notebook downloads them again from ESPN and skips any file it already has.

## How to run the notebook

1. Open the final notebook in Google Colab (File → Open notebook → GitHub → `karen-tran08/ds2002-acc-reality-check`).
2. Run **Runtime → Run all**. The notebook:
   - restores the committed Torvik snapshots from this repository,
   - downloads every ESPN scoreboard date from Nov 1 to today and a summary for every finished game, re-downloading only days that still had unfinished games,
   - builds the crosswalk and the reshaped tables, loads `acc.db`, and proves that a second load leaves every row count unchanged,
   - runs the six questions, draws the charts, and calls the live API for Chart 4.
3. To add a new ratings snapshot: download the Torvik CSV in a browser, commit it as `data/raw/torvik/torvik_YYYY-MM-DD.csv`, add the date to `SNAPSHOT_DATES` in the notebook, and run all again.

The pipeline uses Python 3 with `requests`, `pandas`, `numpy` and `matplotlib`, all preinstalled in Colab.

## The database (`acc.db`)

| Table | One row per | Primary key | Contents |
|---|---|---|---|
| `teams` | team | `espn_id` | Every team in our games: ESPN id, ESPN name, Torvik name (empty for non-Division I teams), how it was matched, ACC flag |
| `team_crosswalk` | spelling | `source`, `source_name` | Which ESPN or Torvik spelling belongs to which ESPN team id |
| `ratings` | team per snapshot date | `snapshot_date`, `torvik_name` | Torvik values for every Division I team on the day we downloaded them |
| `games` | game | `game_id` | Eastern game date, UTC start time, home and away teams, both scores, neutral site, conference game |
| `team_games` | team per game | `game_id`, `espn_id` | Points for and against, margin, win, site, box score totals, estimated possessions |
| `player_games` | player per game | `game_id`, `player_id` | Minutes, points, shooting, rebounds, assists, steals, blocks, turnovers, fouls |

Every table is loaded with `INSERT OR REPLACE`, so rerunning the pipeline replaces rows instead of duplicating them.

## The API

Live at **https://USERNAME.pythonanywhere.com** <!-- TODO -->. It reads only `acc.db` and never contacts Torvik or ESPN. Every query uses `?` placeholders for values that come from a request.

| Route | Returns |
|---|---|
| `/teams` | All 18 ACC teams with record, efficiency margin, average opponent Barthag, and our label |
| `/teams/{team}` | One team's summary and its last five games with opponent and margin. An unknown team returns **404** |
| `/games?date=YYYY-MM-DD` | Every game on that Eastern date with both teams and scores. A malformed date returns **400** |
| `/matchup?a=...&b=...` | Both teams' current ratings, which one is the favorite, and the gap between their efficiency margins |
| `/leaders?stat=...` | ACC players ranked by `points`, `rebounds`, `assists`, `steals` or `blocks` per game. Any other value returns **400** |

**Our labels** (`/teams`):

- **unbeaten, weak schedule:** no losses and average opponent Barthag below 0.35
- **unbeaten, tested:** no losses against a stronger schedule
- **better than its record:** top-5 efficiency margin but a winning percentage below .800
- **tough schedule:** average opponent Barthag of 0.50 or higher
- **as advertised:** everyone else

**Updating the live site:** after pushing a new `acc.db`, open a Bash console on PythonAnywhere and run:

```bash
cd ds2002-acc-reality-check && git pull
pa website reload --domain USERNAME.pythonanywhere.com
```

## Reflection

Our answers to the five reflection questions (the crosswalk, a pipeline decision, trust, our numbers vs. Torvik's, and teamwork) are in **Section 7 of the final notebook**.
