"""Pull ESPN game summaries for every game in acc.db, cache the raw JSON,
and build team_games (with box score totals + estimated possessions) and
player_games. Safe to rerun: downloads are cached, loads use INSERT OR REPLACE.
"""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent.parent
DB_PATH = HERE / 'acc.db'
RAW_DIR = HERE / 'data' / 'raw' / 'summaries'
RAW_DIR.mkdir(parents=True, exist_ok=True)

SUMMARY_URL = ('https://site.api.espn.com/apis/site/v2/sports/basketball/'
               'mens-college-basketball/summary?event={game_id}')

TEAM_STAT_NAMES = {
    'fieldGoalsMade-fieldGoalsAttempted': ('fgm', 'fga'),
    'threePointFieldGoalsMade-threePointFieldGoalsAttempted': ('tpm', 'tpa'),
    'freeThrowsMade-freeThrowsAttempted': ('ftm', 'fta'),
    'offensiveRebounds': ('oreb',),
    'defensiveRebounds': ('dreb',),
    'totalRebounds': ('reb',),
    'assists': ('ast',),
    'steals': ('stl',),
    'blocks': ('blk',),
    'totalTurnovers': ('to',),
}

PLAYER_KEY_MAP = {
    'minutes': 'minutes',
    'points': 'points',
    'rebounds': 'reb',
    'assists': 'ast',
    'turnovers': 'to',
    'steals': 'stl',
    'blocks': 'blk',
    'offensiveRebounds': 'oreb',
    'defensiveRebounds': 'dreb',
    'fouls': 'fouls',
}


def fetch_summary(game_id):
    path = RAW_DIR / f'{game_id}.json'
    if path.exists():
        return json.loads(path.read_text())
    url = SUMMARY_URL.format(game_id=game_id)
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.load(resp)
    path.write_text(json.dumps(data))
    time.sleep(0.3)
    return data


def split_made_attempted(text):
    made, att = text.split('-')
    return int(made), int(att)


def parse_team_stats(entry):
    out = {}
    for s in entry['statistics']:
        mapping = TEAM_STAT_NAMES.get(s['name'])
        if not mapping:
            continue
        if len(mapping) == 2:
            made, att = split_made_attempted(s['displayValue'])
            out[mapping[0]], out[mapping[1]] = made, att
        else:
            out[mapping[0]] = int(s['displayValue'])
    return out


def possessions(stats):
    return stats['fga'] - stats['oreb'] + stats['to'] + 0.475 * stats['fta']


def parse_players(entry):
    keys = entry['statistics'][0]['keys']
    rows = []
    for ath in entry['statistics'][0]['athletes']:
        if ath.get('didNotPlay'):
            continue
        stats = dict(zip(keys, ath['stats']))
        row = {'player_id': ath['athlete']['id'], 'player_name': ath['athlete']['displayName']}
        for espn_key, col in PLAYER_KEY_MAP.items():
            v = stats.get(espn_key)
            if v is None:
                row[col] = None
            elif '-' in str(v):
                made, att = split_made_attempted(v)
                row[f'{col}m'], row[f'{col}a'] = made, att
            else:
                row[col] = int(v) if str(v).lstrip('-').isdigit() else v
        fgm, fga = split_made_attempted(stats.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'))
        tpm, tpa = split_made_attempted(stats.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted', '0-0'))
        ftm, fta = split_made_attempted(stats.get('freeThrowsMade-freeThrowsAttempted', '0-0'))
        row.update(fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm, fta=fta)
        row['minutes'] = int(stats.get('minutes') or 0)
        row['points'] = int(stats.get('points') or 0)
        rows.append(row)
    return rows


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    games = [dict(r) for r in conn.execute(
        "SELECT game_id, game_date_et, home_espn_id, away_espn_id, "
        "home_score, away_score, neutral_site FROM games").fetchall()]

    is_view = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = 'team_games'").fetchone()
    if is_view and is_view[0] == 'view':
        conn.execute("DROP VIEW team_games")

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS team_games (
        game_id TEXT NOT NULL, game_date_et TEXT NOT NULL,
        espn_id TEXT NOT NULL, opponent_id TEXT NOT NULL,
        site TEXT NOT NULL, points_for INTEGER, points_against INTEGER,
        win INTEGER, margin INTEGER,
        fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER,
        ftm INTEGER, fta INTEGER, oreb INTEGER, dreb INTEGER, reb INTEGER,
        ast INTEGER, stl INTEGER, blk INTEGER, "to" INTEGER,
        possessions_est REAL,
        PRIMARY KEY (game_id, espn_id)
    );
    CREATE TABLE IF NOT EXISTS player_games (
        game_id TEXT NOT NULL, game_date_et TEXT NOT NULL,
        espn_id TEXT NOT NULL, player_id TEXT NOT NULL, player_name TEXT NOT NULL,
        minutes INTEGER, points INTEGER,
        fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER, ftm INTEGER, fta INTEGER,
        oreb INTEGER, dreb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER,
        "to" INTEGER, fouls INTEGER,
        PRIMARY KEY (game_id, player_id)
    );
    """)

    skipped = []
    for g in games:
        try:
            data = fetch_summary(g['game_id'])
            bs = data['boxscore']
            if not bs.get('teams'):
                skipped.append((g['game_id'], 'no boxscore'))
                continue
            team_stats = {t['team']['id']: parse_team_stats(t) for t in bs['teams']}
            home_id, away_id = g['home_espn_id'], g['away_espn_id']
            if home_id not in team_stats or away_id not in team_stats:
                skipped.append((g['game_id'], 'team id mismatch'))
                continue
            poss = round((possessions(team_stats[home_id]) + possessions(team_stats[away_id])) / 2, 1)

            for espn_id, opp_id, pf, pa in (
                (home_id, away_id, g['home_score'], g['away_score']),
                (away_id, home_id, g['away_score'], g['home_score']),
            ):
                st = team_stats[espn_id]
                site = 'N' if g['neutral_site'] else ('H' if espn_id == home_id else 'A')
                conn.execute("""INSERT OR REPLACE INTO team_games
                    (game_id, game_date_et, espn_id, opponent_id, site, points_for, points_against,
                     win, margin, fgm, fga, tpm, tpa, ftm, fta, oreb, dreb, reb, ast, stl, blk, "to",
                     possessions_est)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (g['game_id'], g['game_date_et'], espn_id, opp_id, site, pf, pa,
                     1 if pf > pa else 0, pf - pa,
                     st['fgm'], st['fga'], st['tpm'], st['tpa'], st['ftm'], st['fta'],
                     st['oreb'], st['dreb'], st['reb'], st['ast'], st['stl'], st['blk'], st['to'],
                     poss))

            for entry in bs.get('players', []):
                espn_id = entry['team']['id']
                for row in parse_players(entry):
                    conn.execute("""INSERT OR REPLACE INTO player_games
                        (game_id, game_date_et, espn_id, player_id, player_name, minutes, points,
                         fgm, fga, tpm, tpa, ftm, fta, oreb, dreb, reb, ast, stl, blk, "to", fouls)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (g['game_id'], g['game_date_et'], espn_id, row['player_id'], row['player_name'],
                         row['minutes'], row['points'], row['fgm'], row['fga'], row['tpm'], row['tpa'],
                         row['ftm'], row['fta'], row.get('oreb', 0), row.get('dreb', 0),
                         (row.get('oreb') or 0) + (row.get('dreb') or 0),
                         row.get('ast', 0), row.get('stl', 0), row.get('blk', 0), row.get('to', 0),
                         row.get('fouls', 0)))
        except Exception as e:
            skipped.append((g['game_id'], repr(e)))

    conn.commit()
    tg = conn.execute("SELECT COUNT(*) FROM team_games").fetchone()[0]
    pg = conn.execute("SELECT COUNT(*) FROM player_games").fetchone()[0]
    print(f'team_games rows: {tg}')
    print(f'player_games rows: {pg}')
    print(f'skipped: {len(skipped)}')
    for s in skipped:
        print(' ', s)
    conn.close()


if __name__ == '__main__':
    main()
