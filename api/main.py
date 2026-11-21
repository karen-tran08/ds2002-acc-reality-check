"""ACC Reality Check API: reads only acc.db, never calls Torvik or ESPN."""
import os
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

_HERE = Path(__file__).parent
DB_PATH = Path(os.environ.get('ACC_DB') or next(
    (p for p in [_HERE / 'acc.db', _HERE.parent / 'acc.db'] if p.exists()), _HERE / 'acc.db'))

app = FastAPI(title='ACC Reality Check',
              description="ACC men's basketball: records vs. ratings. Data from Bart Torvik and ESPN.")

LEADER_STATS = {'points': 'points', 'rebounds': 'rebounds', 'assists': 'assists',
                'steals': 'steals', 'blocks': 'blocks'}


def query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def latest_snapshot():
    return query('SELECT MAX(snapshot_date) AS d FROM ratings')[0]['d']


TEAM_SUMMARY_SQL = """
WITH rec AS (
    SELECT espn_id, COUNT(*) AS games, SUM(win) AS wins, ROUND(AVG(margin), 1) AS avg_margin
    FROM team_games GROUP BY espn_id
),
opp AS (
    SELECT tg.espn_id, ROUND(AVG(ro.barthag), 3) AS avg_opp_barthag
    FROM team_games tg
    JOIN teams o ON o.espn_id = tg.opponent_id
    LEFT JOIN ratings ro ON ro.torvik_name = o.torvik_name AND ro.snapshot_date = ?
    GROUP BY tg.espn_id
)
SELECT t.espn_id, t.espn_name AS team, rec.games, rec.wins, rec.games - rec.wins AS losses,
       ROUND(1.0 * rec.wins / rec.games, 3) AS win_pct, rec.avg_margin,
       ROUND(r.adjoe - r.adjde, 1) AS eff_margin, r.barthag, opp.avg_opp_barthag
FROM teams t
JOIN rec USING (espn_id)
JOIN opp USING (espn_id)
JOIN ratings r ON r.torvik_name = t.torvik_name AND r.snapshot_date = ?
WHERE t.is_acc = 1
"""


def add_labels(rows):
    """Our team's own short label for each team."""
    margins = sorted((r['eff_margin'] for r in rows), reverse=True)
    for r in rows:
        margin_rank = margins.index(r['eff_margin']) + 1
        if r['losses'] == 0 and r['avg_opp_barthag'] < 0.35:
            r['label'] = 'unbeaten, weak schedule'
        elif r['losses'] == 0:
            r['label'] = 'unbeaten, tested'
        elif margin_rank <= 5 and r['win_pct'] < 0.8:
            r['label'] = 'better than its record'
        elif r['avg_opp_barthag'] >= 0.5:
            r['label'] = 'tough schedule'
        else:
            r['label'] = 'as advertised'
        r['margin_rank'] = margin_rank
    return rows


def find_team(name):
    """Match any spelling we know (ESPN location, ESPN short name, Torvik name), ignoring case."""
    rows = query("""SELECT DISTINCT t.espn_id, t.espn_name, t.torvik_name, t.is_acc
                    FROM team_crosswalk c JOIN teams t ON t.espn_id = c.espn_id
                    WHERE LOWER(c.source_name) = LOWER(?)""", (name.strip(),))
    if not rows:
        raise HTTPException(status_code=404, detail=f'Unknown team: {name}')
    return rows[0]


@app.get('/')
def home():
    return {'routes': ['/teams', '/teams/{team}', '/games?date=YYYY-MM-DD',
                       '/matchup?a=...&b=...', '/leaders?stat=points'],
            'ratings_snapshot': latest_snapshot(), 'docs': '/docs'}


@app.get('/teams')
def teams():
    """All 18 ACC teams with record, efficiency margin, average opponent Barthag and a label."""
    snap = latest_snapshot()
    rows = add_labels(query(TEAM_SUMMARY_SQL, (snap, snap)))
    rows.sort(key=lambda r: r['eff_margin'], reverse=True)
    return {'ratings_snapshot': snap, 'count': len(rows), 'teams': rows}


@app.get('/teams/{team}')
def team_detail(team: str):
    """One ACC team's summary and its last five games. Unknown team -> 404."""
    t = find_team(team)
    snap = latest_snapshot()
    rows = add_labels(query(TEAM_SUMMARY_SQL, (snap, snap)))
    summary = next((r for r in rows if r['espn_id'] == t['espn_id']), None)
    if summary is None:
        raise HTTPException(status_code=404, detail=f'{t["espn_name"]} is not an ACC team')
    last5 = query("""SELECT tg.game_date_et AS date, o.espn_name AS opponent, tg.site,
                            tg.points_for, tg.points_against, tg.margin
                     FROM team_games tg JOIN teams o ON o.espn_id = tg.opponent_id
                     WHERE tg.espn_id = ?
                     ORDER BY tg.game_date_et DESC, tg.game_id DESC LIMIT 5""", (t['espn_id'],))
    return {'ratings_snapshot': snap, 'summary': summary, 'last_five_games': last5}


@app.get('/games')
def games(date: str = Query(..., description='Eastern date, YYYY-MM-DD')):
    """Every game on that Eastern date with both teams and scores."""
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise HTTPException(status_code=400, detail='date must look like YYYY-MM-DD')
    rows = query("""SELECT g.game_id, g.game_date_et AS date, h.espn_name AS home, g.home_score,
                           a.espn_name AS away, g.away_score, g.neutral_site
                    FROM games g
                    JOIN teams h ON h.espn_id = g.home_id
                    JOIN teams a ON a.espn_id = g.away_id
                    WHERE g.game_date_et = ?
                    ORDER BY g.start_utc""", (date,))
    return {'date': date, 'count': len(rows), 'games': rows}


@app.get('/matchup')
def matchup(a: str, b: str):
    """Both teams' current ratings, the favorite, and the efficiency-margin gap."""
    snap = latest_snapshot()
    sides = []
    for name in (a, b):
        t = find_team(name)
        r = query("""SELECT torvik_name, adjoe, adjde, barthag, ROUND(adjoe - adjde, 1) AS eff_margin
                     FROM ratings WHERE torvik_name = ? AND snapshot_date = ?""",
                  (t['torvik_name'], snap))
        if not r:
            raise HTTPException(status_code=404, detail=f'No rating for {t["espn_name"]}')
        sides.append({'team': t['espn_name'], **r[0]})
    fav = max(sides, key=lambda s: s['barthag'])
    return {'ratings_snapshot': snap, 'a': sides[0], 'b': sides[1],
            'favorite': fav['team'],
            'eff_margin_gap': round(abs(sides[0]['eff_margin'] - sides[1]['eff_margin']), 1)}


@app.get('/leaders')
def leaders(stat: str = 'points', limit: int = Query(10, ge=1, le=50)):
    """ACC players ranked by a per-game stat. Unknown stat -> 400."""
    if stat not in LEADER_STATS:
        raise HTTPException(status_code=400,
                            detail=f'stat must be one of: {", ".join(LEADER_STATS)}')
    col = LEADER_STATS[stat]   # column name comes from our own whitelist, never from the request
    rows = query(f"""SELECT pg.player_name AS player, t.espn_name AS team, COUNT(*) AS games,
                            ROUND(1.0 * SUM(pg.{col}) / COUNT(*), 1) AS per_game
                     FROM player_games pg JOIN teams t ON t.espn_id = pg.espn_id
                     WHERE t.is_acc = 1
                     GROUP BY pg.player_id
                     ORDER BY per_game DESC LIMIT ?""", (limit,))
    return {'stat': stat, 'leaders': rows}
