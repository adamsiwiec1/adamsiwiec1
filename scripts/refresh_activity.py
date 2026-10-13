#!/usr/bin/env python3
"""Refresh live activity stats for the profile README."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
OUT_STATS = ROOT / "assets" / "activity-stats.svg"
OUT_GRAPH = ROOT / "assets" / "contribution-graph-90d.svg"
OUT_JSON = ROOT / "assets" / "activity-stats.json"
LOGIN = "adamsiwiec1"
USER_ID = "MDQ6VXNlcjczMDg1OTI3"
WINDOWS = (90, 60, 30, 7)
START_MARK = "<!-- activity:start -->"
END_MARK = "<!-- activity:end -->"

# GitHub dark contribution levels (same ramp as the profile calendar).
GRAPH_LEVELS = ("#161b22", "#0e4429", "#006d32", "#26a641", "#39d353")
GRAPH_BG = "#0d1117"
GRAPH_BORDER = "#30363d"
GRAPH_MUTED = "#8b949e"
GRAPH_TEXT = "#e6edf3"
FONT = "ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"


def gh_graphql(query: str, variables: dict) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            cmd += ["-F", f"{key}=null"]
        else:
            cmd += ["-F", f"{key}={value}"]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.output) from exc
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nicer(n: int) -> str:
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if n >= 1_000:
        text = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    return str(n)


def contribution_calendar(now: dt.datetime, days: int = 90) -> list[dict]:
    """Return contribution days for the last `days` days (GitHub calendar)."""
    start = now - dt.timedelta(days=days - 1)
    query = """
    query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {
        contributionsCollection(from:$from, to:$to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
                color
                weekday
              }
            }
          }
        }
      }
    }
    """
    calendar = gh_graphql(
        query,
        {
            "login": LOGIN,
            "from": iso(start.replace(hour=0, minute=0, second=0, microsecond=0)),
            "to": iso(now),
        },
    )["user"]["contributionsCollection"]["contributionCalendar"]
    days_out: list[dict] = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days_out.append(day)
    cutoff = (now - dt.timedelta(days=days - 1)).date()
    return [
        day
        for day in days_out
        if cutoff <= dt.date.fromisoformat(day["date"]) <= now.date()
    ]


def consistency_for_windows(calendar_days: list[dict], now: dt.datetime) -> dict[int, tuple[int, int]]:
    """Days with at least one contribution in each window → (active, window)."""
    today = now.date()
    out: dict[int, tuple[int, int]] = {}
    for window in WINDOWS:
        cutoff = today - dt.timedelta(days=window - 1)
        active = sum(
            1
            for day in calendar_days
            if cutoff <= dt.date.fromisoformat(day["date"]) <= today
            and day["contributionCount"] > 0
        )
        out[window] = (active, window)
    return out


def level_for_count(count: int, max_count: int) -> int:
    if count <= 0:
        return 0
    if max_count <= 1:
        return 4
    q = count / max_count
    if q <= 0.25:
        return 1
    if q <= 0.5:
        return 2
    if q <= 0.75:
        return 3
    return 4


def render_contribution_graph(days: list[dict], as_of: dt.datetime) -> str:
    """Compact GitHub-style contribution calendar for ~90 days."""
    if not days:
        days = []
    by_date = {d["date"]: d for d in days}
    start = dt.date.fromisoformat(days[0]["date"]) if days else as_of.date()
    end = dt.date.fromisoformat(days[-1]["date"]) if days else as_of.date()
    start_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    weeks: list[list[dict | None]] = []
    cursor = start_sunday
    while cursor <= end:
        week: list[dict | None] = []
        for _ in range(7):
            key = cursor.isoformat()
            if key in by_date:
                week.append(by_date[key])
            elif cursor < start or cursor > end:
                week.append(None)
            else:
                week.append(
                    {
                        "date": key,
                        "contributionCount": 0,
                        "weekday": (cursor.weekday() + 1) % 7,
                    }
                )
            cursor += dt.timedelta(days=1)
        weeks.append(week)

    max_count = max((d["contributionCount"] for d in days), default=0)
    cell = 7
    gap = 2
    left = 22
    top = 28
    width = left + len(weeks) * (cell + gap) + 10
    height = top + 7 * (cell + gap) + 16

    month_labels: list[tuple[int, str]] = []
    last_month = None
    for i, week in enumerate(weeks):
        for day in week:
            if not day:
                continue
            d = dt.date.fromisoformat(day["date"])
            if d.month != last_month and d.day <= 7:
                month_labels.append((i, d.strftime("%b")))
                last_month = d.month
                break

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Contribution graph, last 90 days">',
        "<title>Contribution graph (last 90 days)</title>",
        f'<rect width="{width}" height="{height}" rx="8" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
        f'<text x="10" y="14" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="9" font-weight="700">Contributions</text>',
        f'<text x="{width - 10}" y="14" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="8">90d</text>',
    ]
    for i, label in month_labels:
        x = left + i * (cell + gap)
        parts.append(
            f'<text x="{x}" y="{top - 4}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="7">{label}</text>'
        )

    day_labels = {1: "M", 3: "W", 5: "F"}
    for weekday, label in day_labels.items():
        y = top + weekday * (cell + gap) + cell - 1
        parts.append(
            f'<text x="6" y="{y}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="7">{label}</text>'
        )

    for wi, week in enumerate(weeks):
        for day in week:
            if day is None:
                continue
            weekday = day.get("weekday")
            if weekday is None:
                weekday = (dt.date.fromisoformat(day["date"]).weekday() + 1) % 7
            x = left + wi * (cell + gap)
            y = top + weekday * (cell + gap)
            count = day["contributionCount"]
            fill = GRAPH_LEVELS[level_for_count(count, max_count)]
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="1" fill="{fill}">'
                f'<title>{day["date"]}: {count} contribution{"s" if count != 1 else ""}</title></rect>'
            )

    lx = width - 10 - 5 * (cell + gap) - 28
    ly = height - 12
    parts.append(
        f'<text x="{lx - 18}" y="{ly + cell - 1}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="7">Less</text>'
    )
    for i, color in enumerate(GRAPH_LEVELS):
        parts.append(
            f'<rect x="{lx + i * (cell + gap)}" y="{ly}" width="{cell}" height="{cell}" rx="1" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{lx + 5 * (cell + gap) + 2}" y="{ly + cell - 1}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="7">More</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def contribution_stats(now: dt.datetime, days: int) -> dict:
    start = now - dt.timedelta(days=days)
    query = """
    query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {
        contributionsCollection(from:$from, to:$to) {
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          restrictedContributionsCount
          commitContributionsByRepository(maxRepositories: 100) {
            contributions { totalCount }
            repository { nameWithOwner isPrivate owner { login } name }
          }
        }
      }
    }
    """
    collection = gh_graphql(
        query,
        {"login": LOGIN, "from": iso(start), "to": iso(now)},
    )["user"]["contributionsCollection"]
    activity = (
        collection["totalCommitContributions"]
        + collection["totalIssueContributions"]
        + collection["totalPullRequestContributions"]
        + collection["totalPullRequestReviewContributions"]
        + collection["restrictedContributionsCount"]
    )
    repos = [
        {
            "name": row["repository"]["nameWithOwner"],
            "count": row["contributions"]["totalCount"],
            "private": row["repository"]["isPrivate"],
            "owner": row["repository"]["owner"]["login"],
            "repo": row["repository"]["name"],
        }
        for row in collection["commitContributionsByRepository"]
    ]
    repos.sort(key=lambda item: (-item["count"], item["name"]))
    return {
        "days": days,
        "activity": activity,
        "commits": collection["totalCommitContributions"],
        "repos": repos,
    }


def lifetime_totals(now: dt.datetime) -> dict:
    """Sum activity + commits across account lifetime (≤1-year GraphQL windows)."""
    meta = gh_graphql(
        "query($login:String!) { user(login:$login) { createdAt } }",
        {"login": LOGIN},
    )["user"]
    created = dt.datetime.fromisoformat(meta["createdAt"].replace("Z", "+00:00"))

    query = """
    query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {
        contributionsCollection(from:$from, to:$to) {
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          restrictedContributionsCount
          commitContributionsByRepository(maxRepositories: 100) {
            contributions { totalCount }
            repository { nameWithOwner isPrivate owner { login } name }
          }
        }
      }
    }
    """
    activity = 0
    commits = 0
    repos_by_name: dict[str, dict] = {}
    cursor = created
    while cursor < now:
        end = min(cursor + dt.timedelta(days=364), now)
        collection = gh_graphql(
            query,
            {"login": LOGIN, "from": iso(cursor), "to": iso(end)},
        )["user"]["contributionsCollection"]
        activity += (
            collection["totalCommitContributions"]
            + collection["totalIssueContributions"]
            + collection["totalPullRequestContributions"]
            + collection["totalPullRequestReviewContributions"]
            + collection["restrictedContributionsCount"]
        )
        commits += collection["totalCommitContributions"]
        for row in collection["commitContributionsByRepository"]:
            repo = row["repository"]
            name = repo["nameWithOwner"]
            entry = repos_by_name.get(name)
            if entry is None:
                repos_by_name[name] = {
                    "name": name,
                    "count": row["contributions"]["totalCount"],
                    "private": repo["isPrivate"],
                    "owner": repo["owner"]["login"],
                    "repo": repo["name"],
                }
            else:
                entry["count"] += row["contributions"]["totalCount"]
        cursor = end

    repos = sorted(repos_by_name.values(), key=lambda item: (-item["count"], item["name"]))
    return {
        "activity": activity,
        "commits": commits,
        "repos": repos,
        "created_at": iso(created),
    }


def line_edits(now: dt.datetime, repos: list[dict], since: dt.datetime, max_pages: int = 12) -> tuple[dict[int, int], int]:
    """Return (window_totals, lifetime_or_since_total) of additions+deletions."""
    query = """
    query($owner:String!, $name:String!, $authorId:ID!, $since:GitTimestamp!, $after:String) {
      repository(owner:$owner, name:$name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, author: {id: $authorId}, since: $since, after: $after) {
                pageInfo { hasNextPage endCursor }
                nodes { committedDate additions deletions }
              }
            }
          }
        }
      }
    }
    """
    commits: list[dict] = []
    for row in repos:
        after = None
        for _ in range(max_pages):
            data = gh_graphql(
                query,
                {
                    "owner": row["owner"],
                    "name": row["repo"],
                    "authorId": USER_ID,
                    "since": iso(since),
                    "after": after,
                },
            )
            repository = data.get("repository") or {}
            ref = repository.get("defaultBranchRef") or {}
            target = ref.get("target") or {}
            history = target.get("history")
            if not history:
                break
            commits.extend(history["nodes"])
            if not history["pageInfo"]["hasNextPage"]:
                break
            after = history["pageInfo"]["endCursor"]

    totals = {days: 0 for days in WINDOWS}
    all_edits = 0
    for commit in commits:
        when = dt.datetime.fromisoformat(commit["committedDate"].replace("Z", "+00:00"))
        edits = (commit.get("additions") or 0) + (commit.get("deletions") or 0)
        all_edits += edits
        age = (now - when).total_seconds() / 86400
        for days in WINDOWS:
            if age <= days:
                totals[days] += edits
    return totals, all_edits


def render_stats_svg(rows: list[dict], as_of: dt.datetime) -> str:
    width = 900
    row_h = 32
    header_h = 52
    height = header_h + 18 + row_h * len(rows) + 16
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Activity stats">',
        "<title>Activity, commits, line edits, and consistency</title>",
        f'<rect width="{width}" height="{height}" rx="12" fill="#0d1117" stroke="#30363d"/>',
        f'<text x="24" y="28" fill="#e6edf3" font-family="{FONT}" font-size="15" font-weight="700">Activity</text>',
        f'<text x="{width - 24}" y="28" text-anchor="end" fill="#8b949e" font-family="{MONO}" font-size="11">as of {as_of.strftime("%Y-%m-%d UTC")}</text>',
        f'<text x="24" y="52" fill="#8b949e" font-family="{MONO}" font-size="11">window</text>',
        f'<text x="160" y="52" fill="#8b949e" font-family="{MONO}" font-size="11">activity</text>',
        f'<text x="320" y="52" fill="#8b949e" font-family="{MONO}" font-size="11">commits</text>',
        f'<text x="480" y="52" fill="#8b949e" font-family="{MONO}" font-size="11">line edits</text>',
        f'<text x="680" y="52" fill="#8b949e" font-family="{MONO}" font-size="11">consistency</text>',
    ]
    y = 78
    for row in rows:
        active, window = row["consistency"]
        lines += [
            f'<text x="24" y="{y}" fill="#e6edf3" font-family="{MONO}" font-size="13">last {row["days"]}d</text>',
            f'<text x="160" y="{y}" fill="#3fb950" font-family="{MONO}" font-size="13" font-weight="700">{nicer(row["activity"])}</text>',
            f'<text x="320" y="{y}" fill="#58a6ff" font-family="{MONO}" font-size="13" font-weight="700">{nicer(row["commits"])}</text>',
            f'<text x="480" y="{y}" fill="#d29922" font-family="{MONO}" font-size="13" font-weight="700">{nicer(row["edits"])}</text>',
            f'<text x="680" y="{y}" fill="#a371f7" font-family="{MONO}" font-size="13" font-weight="700">{active}/{window}</text>',
        ]
        y += row_h
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def repos_row_html(repos: list[dict]) -> str:
    """Single table row — one horizontal strip of repo + commit count."""
    cells = []
    for repo in repos:
        short = repo["name"].split("/")[-1]
        cells.append(
            "<td valign=\"middle\" nowrap>"
            f'<a href="https://github.com/{repo["name"]}">{short}</a>&nbsp;'
            f"<code>{repo['count']}</code>"
            "</td>"
        )
    return "<table>\n<tr>\n" + "\n".join(cells) + "\n</tr>\n</table>"


def lifetime_html(lifetime: dict) -> str:
    return (
        '<p align="center">'
        f'<sub>lifetime · activity <code>{nicer(lifetime["activity"])}</code>'
        f' · commits <code>{nicer(lifetime["commits"])}</code>'
        f' · line edits <code>{nicer(lifetime["edits"])}</code></sub>'
        "</p>"
    )


def render_readme_block(rows: list[dict], repos: list[dict], lifetime: dict) -> str:
    return "\n".join(
        [
            START_MARK,
            "",
            '<p align="center">',
            '  <a href="https://github.com/adamsiwiec1">',
            '    <img src="assets/contribution-graph-90d.svg" alt="GitHub contribution graph, last 90 days" width="320">',
            "  </a>",
            "</p>",
            "",
            lifetime_html(lifetime),
            "",
            '<p align="center"><img src="assets/activity-stats.svg" alt="Activity, commits, line edits, and consistency for the last 90, 60, 30, and 7 days" width="100%"></p>',
            "",
            f"Repos contributed to in the last 90 days ({len(repos)}):",
            "",
            repos_row_html(repos),
            "",
            END_MARK,
            "",
        ]
    )


def patch_readme(block: str) -> None:
    text = README.read_text()
    pattern = re.compile(
        re.escape(START_MARK) + r".*?" + re.escape(END_MARK),
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block.strip(), text)
    else:
        section = re.compile(r"(## Activity\n)(.*?)(\n## )", re.DOTALL)
        text, n = section.subn(r"\1\n" + block + r"\3", text, count=1)
        if n != 1:
            raise SystemExit("failed to insert activity block")
    text = re.sub(
        r"<p align=\"center\">\s*<a href=\"https://github.com/adamsiwiec1\">\s*"
        r"<img src=\"https://ghchart\.rshah\.org/[^\"]+\"[^>]*>\s*</a>\s*</p>\s*",
        "",
        text,
        count=1,
    )
    README.write_text(text)


def main() -> int:
    now = dt.datetime.now(timezone.utc)
    calendar_days = contribution_calendar(now, 90)
    consistency = consistency_for_windows(calendar_days, now)
    by_window = {days: contribution_stats(now, days) for days in WINDOWS}
    lifetime = lifetime_totals(now)
    # Crawl recent + top lifetime repos so windows stay accurate without a huge API fanout.
    edit_by_name = {r["name"]: r for r in by_window[90]["repos"]}
    for row in lifetime["repos"][:40]:
        edit_by_name.setdefault(row["name"], row)
    edit_repos = list(edit_by_name.values())
    created = dt.datetime.fromisoformat(lifetime["created_at"].replace("Z", "+00:00"))
    edits, lifetime_edits = line_edits(now, edit_repos, since=created, max_pages=10)
    lifetime["edits"] = lifetime_edits
    rows = [
        {
            "days": days,
            "activity": by_window[days]["activity"],
            "commits": by_window[days]["commits"],
            "edits": edits[days],
            "consistency": list(consistency[days]),
        }
        for days in WINDOWS
    ]
    repos = by_window[90]["repos"]

    OUT_STATS.parent.mkdir(parents=True, exist_ok=True)
    OUT_GRAPH.write_text(render_contribution_graph(calendar_days, now))
    OUT_STATS.write_text(render_stats_svg(rows, now))
    OUT_JSON.write_text(
        json.dumps(
            {
                "as_of": iso(now),
                "lifetime": {
                    "activity": lifetime["activity"],
                    "commits": lifetime["commits"],
                    "edits": lifetime["edits"],
                    "created_at": lifetime.get("created_at"),
                },
                "windows": rows,
                "graph_days": len(calendar_days),
                "graph_total": sum(d["contributionCount"] for d in calendar_days),
                "repos_90d": [
                    {"name": r["name"], "commits": r["count"], "private": r["private"]}
                    for r in repos
                ],
            },
            indent=2,
        )
        + "\n"
    )
    patch_readme(render_readme_block(rows, repos, lifetime))
    old = ROOT / "assets" / "activity-repos.svg"
    if old.exists():
        old.unlink()
    print(json.dumps({"lifetime": lifetime, "windows": rows}, indent=2, default=str))
    print(f"graph_days={len(calendar_days)} repos_90d={len(repos)} edit_repos={len(edit_repos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
