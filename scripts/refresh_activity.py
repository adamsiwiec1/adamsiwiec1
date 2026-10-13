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
OUT_PANEL = ROOT / "assets" / "activity-panel.svg"
OUT_JSON = ROOT / "assets" / "activity-stats.json"
# Full README content width — one SVG so GitHub can't leave a dead column.
PANEL_WIDTH = 920
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


def _calendar_weeks(days: list[dict], as_of: dt.datetime) -> tuple[list[list[dict | None]], int]:
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
    return weeks, max_count


def _graph_body(
    weeks: list[list[dict | None]],
    max_count: int,
    *,
    ox: int = 0,
    oy: int = 0,
    cell: int = 14,
    gap: int = 3,
) -> tuple[list[str], int, int]:
    """Return (svg elements, width, height) for the contribution calendar."""
    left = 18
    top = 24
    width = left + len(weeks) * (cell + gap) + 8
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
        f'<text x="{ox + 10}" y="{oy + 14}" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="11" font-weight="700">Contributions</text>',
        f'<text x="{ox + width - 10}" y="{oy + 14}" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">90d</text>',
    ]
    for i, label in month_labels:
        x = ox + left + i * (cell + gap)
        parts.append(
            f'<text x="{x}" y="{oy + top - 4}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="9">{label}</text>'
        )

    for weekday, label in {1: "M", 3: "W", 5: "F"}.items():
        y = oy + top + weekday * (cell + gap) + cell - 1
        parts.append(
            f'<text x="{ox + 5}" y="{y}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="8">{label}</text>'
        )

    for wi, week in enumerate(weeks):
        for day in week:
            if day is None:
                continue
            weekday = day.get("weekday")
            if weekday is None:
                weekday = (dt.date.fromisoformat(day["date"]).weekday() + 1) % 7
            x = ox + left + wi * (cell + gap)
            y = oy + top + weekday * (cell + gap)
            count = day["contributionCount"]
            fill = GRAPH_LEVELS[level_for_count(count, max_count)]
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{fill}">'
                f'<title>{day["date"]}: {count} contribution{"s" if count != 1 else ""}</title></rect>'
            )

    lx = ox + width - 10 - 5 * (cell + gap) - 36
    ly = oy + height - 14
    parts.append(
        f'<text x="{lx - 22}" y="{ly + cell - 1}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="8">Less</text>'
    )
    for i, color in enumerate(GRAPH_LEVELS):
        parts.append(
            f'<rect x="{lx + i * (cell + gap)}" y="{ly}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{lx + 5 * (cell + gap) + 3}" y="{ly + cell - 1}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="8">More</text>'
    )
    return parts, width, height


def _stats_body(
    rows: list[dict],
    as_of: dt.datetime,
    lifetime: dict | None,
    *,
    ox: int,
    oy: int,
    width: int,
    height: int,
) -> list[str]:
    """Activity table drawn into a panel of the given size (fills width)."""
    row_h = max(18, (height - 56) // max(len(rows), 1))
    y_title = oy + 16
    y_life = oy + 32
    y_cols = oy + 48
    y0 = oy + 66
    # Five columns evenly across the full panel (last col near the right edge).
    usable = width - 28
    xs = [ox + 14 + int(usable * i / 4) for i in range(5)]
    parts = [
        f'<text x="{ox + 14}" y="{y_title}" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="11" font-weight="700">Activity</text>',
        f'<text x="{ox + width - 14}" y="{y_title}" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">as of {as_of.strftime("%Y-%m-%d")}</text>',
    ]
    if lifetime:
        parts.append(
            f'<text x="{ox + 14}" y="{y_life}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">'
            f'lifetime · {nicer(lifetime["activity"])} act · {nicer(lifetime["commits"])} commits · {nicer(lifetime["edits"])} edits'
            f"</text>"
        )
    for x, label in zip(xs, ("window", "activity", "commits", "edits", "days")):
        parts.append(
            f'<text x="{x}" y="{y_cols}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">{label}</text>'
        )
    y = y0
    for row in rows:
        active, window = row["consistency"]
        vals = (
            (xs[0], GRAPH_TEXT, f'last {row["days"]}d', False),
            (xs[1], "#3fb950", nicer(row["activity"]), True),
            (xs[2], "#58a6ff", nicer(row["commits"]), True),
            (xs[3], "#d29922", nicer(row["edits"]), True),
            (xs[4], "#a371f7", f"{active}/{window}", True),
        )
        for x, color, text, bold in vals:
            weight = ' font-weight="700"' if bold else ""
            parts.append(
                f'<text x="{x}" y="{y}" fill="{color}" font-family="{MONO}" font-size="12"{weight}>{text}</text>'
            )
        y += row_h
    return parts


def render_activity_panel(
    days: list[dict],
    rows: list[dict],
    as_of: dt.datetime,
    lifetime: dict | None = None,
) -> str:
    """Single full-width SVG: contributions | activity (no dead horizontal space)."""
    weeks, max_count = _calendar_weeks(days, as_of)
    pad = 10
    gap = 12
    # Graph gets ~44% of the inner width; activity fills the rest.
    inner = PANEL_WIDTH - 2 * pad
    graph_w_target = int(inner * 0.44)
    # Pick cell size so the calendar roughly fills its column.
    nweeks = max(len(weeks), 1)
    cell = max(10, min(18, (graph_w_target - 30) // nweeks - 3))
    g_gap = 3 if cell < 14 else 4
    g_parts, g_w, g_h = _graph_body(weeks, max_count, ox=pad, oy=pad, cell=cell, gap=g_gap)
    height = g_h + 2 * pad
    stats_x = pad + g_w + gap
    stats_w = PANEL_WIDTH - stats_x - pad
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PANEL_WIDTH}" height="{height}" viewBox="0 0 {PANEL_WIDTH} {height}" role="img" aria-label="Contributions and activity, last 90 days">',
        "<title>Contributions and activity</title>",
        f'<rect width="{PANEL_WIDTH}" height="{height}" rx="8" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
        # Subtle divider between panels
        f'<line x1="{stats_x - gap // 2}" y1="{pad + 8}" x2="{stats_x - gap // 2}" y2="{height - pad - 8}" stroke="{GRAPH_BORDER}" stroke-width="1"/>',
    ]
    parts.extend(g_parts)
    parts.extend(
        _stats_body(
            rows,
            as_of,
            lifetime,
            ox=stats_x,
            oy=pad,
            width=stats_w,
            height=g_h,
        )
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_contribution_graph(days: list[dict], as_of: dt.datetime) -> str:
    """Standalone contribution calendar (kept for assets / debugging)."""
    weeks, max_count = _calendar_weeks(days, as_of)
    body, width, height = _graph_body(weeks, max_count, cell=16, gap=4)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Contribution graph, last 90 days">',
        "<title>Contribution graph (last 90 days)</title>",
        f'<rect width="{width}" height="{height}" rx="6" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
        *body,
        "</svg>",
    ]
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


def render_stats_svg(
    rows: list[dict],
    as_of: dt.datetime,
    lifetime: dict | None = None,
    height: int = 137,
    width: int = 360,
) -> str:
    """Activity panel sized to match contribution-graph height."""
    row_h = 20
    y_title = 14
    y_life = 28
    y_cols = 44
    y0 = 60
    # Five columns spread across the panel.
    xs = [10, int(width * 0.22), int(width * 0.40), int(width * 0.58), int(width * 0.78)]
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Activity stats">',
        "<title>Activity, commits, line edits, and consistency</title>",
        f'<rect width="{width}" height="{height}" rx="6" fill="#0d1117" stroke="{GRAPH_BORDER}"/>',
        f'<text x="10" y="{y_title}" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="10" font-weight="700">Activity</text>',
        f'<text x="{width - 10}" y="{y_title}" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="8">as of {as_of.strftime("%Y-%m-%d")}</text>',
    ]
    if lifetime:
        lines.append(
            f'<text x="10" y="{y_life}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="8">'
            f'lifetime · {nicer(lifetime["activity"])} act · {nicer(lifetime["commits"])} commits · {nicer(lifetime["edits"])} edits'
            f"</text>"
        )
    for x, label in zip(xs, ("window", "activity", "commits", "edits", "days")):
        lines.append(
            f'<text x="{x}" y="{y_cols}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="8">{label}</text>'
        )
    y = y0
    for row in rows:
        active, window = row["consistency"]
        vals = (
            (xs[0], GRAPH_TEXT, f'last {row["days"]}d'),
            (xs[1], "#3fb950", nicer(row["activity"])),
            (xs[2], "#58a6ff", nicer(row["commits"])),
            (xs[3], "#d29922", nicer(row["edits"])),
            (xs[4], "#a371f7", f"{active}/{window}"),
        )
        for x, color, text in vals:
            weight = "" if x == xs[0] else ' font-weight="700"'
            lines.append(
                f'<text x="{x}" y="{y}" fill="{color}" font-family="{MONO}" font-size="10"{weight}>{text}</text>'
            )
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


def render_readme_block(rows: list[dict], repos: list[dict], lifetime: dict) -> str:
    # One full-width SVG — avoids GitHub table/image shrink-wrap dead space.
    return "\n".join(
        [
            START_MARK,
            "",
            '<p align="center">',
            '<a href="https://github.com/adamsiwiec1">',
            '<img src="assets/activity-panel.svg" alt="Contributions and activity for the last 90 days" width="100%">',
            "</a>",
            "</p>",
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
    panel_svg = render_activity_panel(calendar_days, rows, now, lifetime=lifetime)
    OUT_PANEL.write_text(panel_svg)
    # Keep standalone assets in sync for reference / older links.
    graph_svg = render_contribution_graph(calendar_days, now)
    OUT_GRAPH.write_text(graph_svg)
    graph_h = int(re.search(r'height="(\d+)"', graph_svg).group(1))
    graph_w = int(re.search(r'width="(\d+)"', graph_svg).group(1))
    OUT_STATS.write_text(
        render_stats_svg(
            rows,
            now,
            lifetime=lifetime,
            height=graph_h,
            width=int(graph_w * 54 / 46),
        )
    )
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
