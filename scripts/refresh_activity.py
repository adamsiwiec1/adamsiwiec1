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
        {"login": LOGIN, "from": iso(start.replace(hour=0, minute=0, second=0, microsecond=0)), "to": iso(now)},
    )["user"]["contributionsCollection"]["contributionCalendar"]
    days_out: list[dict] = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days_out.append(day)
    # Keep only the requested window (API may pad to week boundaries).
    cutoff = (now - dt.timedelta(days=days - 1)).date()
    filtered = []
    for day in days_out:
        d = dt.date.fromisoformat(day["date"])
        if d >= cutoff and d <= now.date():
            filtered.append(day)
    return filtered


def level_for_count(count: int, max_count: int) -> int:
    if count <= 0:
        return 0
    if max_count <= 1:
        return 4
    # Match GitHub's quartile-ish ramp against the window max.
    q = count / max_count
    if q <= 0.25:
        return 1
    if q <= 0.5:
        return 2
    if q <= 0.75:
        return 3
    return 4


def render_contribution_graph(days: list[dict], as_of: dt.datetime) -> str:
    """GitHub-style commit contribution calendar for ~90 days."""
    if not days:
        days = []
    # Bucket into weeks starting Sunday (GitHub weekday: 0=Sunday).
    by_date = {d["date"]: d for d in days}
    start = dt.date.fromisoformat(days[0]["date"]) if days else as_of.date()
    end = dt.date.fromisoformat(days[-1]["date"]) if days else as_of.date()
    # Align start to Sunday on or before first day.
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
                week.append({"date": key, "contributionCount": 0, "weekday": (cursor.weekday() + 1) % 7})
            cursor += dt.timedelta(days=1)
        weeks.append(week)

    max_count = max((d["contributionCount"] for d in days), default=0)
    cell = 11
    gap = 3
    left = 36
    top = 52
    width = left + len(weeks) * (cell + gap) + 16
    height = top + 7 * (cell + gap) + 28

    # Month labels
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
        f'<rect width="{width}" height="{height}" rx="12" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
        f'<text x="16" y="22" fill="{GRAPH_TEXT}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" font-weight="700">Contributions</text>',
        f'<text x="{width - 16}" y="22" text-anchor="end" fill="{GRAPH_MUTED}" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">last 90 days</text>',
    ]
    for i, label in month_labels:
        x = left + i * (cell + gap)
        parts.append(
            f'<text x="{x}" y="{top - 6}" fill="{GRAPH_MUTED}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="10">{label}</text>'
        )

    # Day labels Mon/Wed/Fri like GitHub
    day_labels = {1: "Mon", 3: "Wed", 5: "Fri"}
    for weekday, label in day_labels.items():
        y = top + weekday * (cell + gap) + cell - 1
        parts.append(
            f'<text x="8" y="{y}" fill="{GRAPH_MUTED}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="9">{label}</text>'
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
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{fill}">'
                f'<title>{day["date"]}: {count} contribution{"s" if count != 1 else ""}</title></rect>'
            )

    # Legend
    lx = width - 16 - 5 * (cell + gap) - 48
    ly = height - 18
    parts.append(
        f'<text x="{lx - 28}" y="{ly + cell - 2}" fill="{GRAPH_MUTED}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="10">Less</text>'
    )
    for i, color in enumerate(GRAPH_LEVELS):
        parts.append(
            f'<rect x="{lx + i * (cell + gap)}" y="{ly}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{lx + 5 * (cell + gap) + 4}" y="{ly + cell - 2}" fill="{GRAPH_MUTED}" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="10">More</text>'
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


def line_edits(now: dt.datetime, repos: list[dict]) -> dict[int, int]:
    start = now - dt.timedelta(days=max(WINDOWS))
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
        for _ in range(8):
            data = gh_graphql(
                query,
                {
                    "owner": row["owner"],
                    "name": row["repo"],
                    "authorId": USER_ID,
                    "since": iso(start),
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
    for commit in commits:
        when = dt.datetime.fromisoformat(commit["committedDate"].replace("Z", "+00:00"))
        edits = (commit.get("additions") or 0) + (commit.get("deletions") or 0)
        age = (now - when).total_seconds() / 86400
        for days in WINDOWS:
            if age <= days:
                totals[days] += edits
    return totals


def render_stats_svg(rows: list[dict], as_of: dt.datetime) -> str:
    width = 900
    row_h = 32
    header_h = 52
    height = header_h + 18 + row_h * len(rows) + 16
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Activity stats">',
        "<title>Activity, commits, and line edits</title>",
        f'<rect width="{width}" height="{height}" rx="12" fill="#0d1117" stroke="#30363d"/>',
        '<text x="24" y="28" fill="#e6edf3" font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="15" font-weight="700">Activity</text>',
        f'<text x="{width - 24}" y="28" text-anchor="end" fill="#8b949e" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">as of {as_of.strftime("%Y-%m-%d UTC")}</text>',
        '<text x="24" y="52" fill="#8b949e" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">window</text>',
        '<text x="200" y="52" fill="#8b949e" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">activity</text>',
        '<text x="400" y="52" fill="#8b949e" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">commits</text>',
        '<text x="600" y="52" fill="#8b949e" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11">line edits</text>',
    ]
    y = 78
    for row in rows:
        lines += [
            f'<text x="24" y="{y}" fill="#e6edf3" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="13">last {row["days"]}d</text>',
            f'<text x="200" y="{y}" fill="#3fb950" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="13" font-weight="700">{nicer(row["activity"])}</text>',
            f'<text x="400" y="{y}" fill="#58a6ff" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="13" font-weight="700">{nicer(row["commits"])}</text>',
            f'<text x="600" y="{y}" fill="#d29922" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="13" font-weight="700">{nicer(row["edits"])}</text>',
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


def render_readme_block(rows: list[dict], repos: list[dict]) -> str:
    return "\n".join(
        [
            START_MARK,
            "",
            '<p align="center">',
            '  <a href="https://github.com/adamsiwiec1">',
            '    <img src="assets/contribution-graph-90d.svg" alt="GitHub contribution graph, last 90 days" width="100%">',
            "  </a>",
            "</p>",
            "",
            '<p align="center"><img src="assets/activity-stats.svg" alt="Activity, commits, and line edits for the last 90, 60, 30, and 7 days" width="100%"></p>',
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
    # Remove any leftover third-party year chart above the markers.
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
    by_window = {days: contribution_stats(now, days) for days in WINDOWS}
    edits = line_edits(now, by_window[90]["repos"])
    rows = [
        {
            "days": days,
            "activity": by_window[days]["activity"],
            "commits": by_window[days]["commits"],
            "edits": edits[days],
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
    patch_readme(render_readme_block(rows, repos))
    old = ROOT / "assets" / "activity-repos.svg"
    if old.exists():
        old.unlink()
    print(json.dumps(rows, indent=2))
    print(f"graph_days={len(calendar_days)} repos_90d={len(repos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
