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
PANEL_WIDTH = 888
LOGIN = "adamsiwiec1"
USER_ID = "MDQ6VXNlcjczMDg1OTI3"
WINDOWS = (90, 60, 30, 7)
START_MARK = "<!-- activity:start -->"
END_MARK = "<!-- activity:end -->"
CONTRIB_START = "<!-- contributed:start -->"
CONTRIB_END = "<!-- contributed:end -->"
OUT_REPOS = ROOT / "assets" / "repos-90d.svg"

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
    cell: int = 11,
    gap: int = 3,
) -> tuple[list[str], int, int]:
    """Return (svg elements, width, height) for the contribution calendar."""
    left = 14
    title_y = 12
    top = 26
    width = left + len(weeks) * (cell + gap) + 6
    height = top + 7 * (cell + gap) + 2

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
        f'<text x="{ox + 8}" y="{oy + title_y}" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="10" font-weight="700">Contributions - Past 90 Days</text>',
    ]
    for i, label in month_labels:
        x = ox + left + i * (cell + gap)
        parts.append(
            f'<text x="{x}" y="{oy + top - 4}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="8">{label}</text>'
        )

    for weekday, label in {1: "M", 3: "W", 5: "F"}.items():
        y = oy + top + weekday * (cell + gap) + cell - 1
        parts.append(
            f'<text x="{ox + 4}" y="{y}" fill="{GRAPH_MUTED}" font-family="{FONT}" font-size="7">{label}</text>'
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
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="1" fill="{fill}">'
                f'<title>{day["date"]}: {count} contribution{"s" if count != 1 else ""}</title></rect>'
            )
    return parts, width, height


def _stats_columns(row: dict) -> list[tuple[str, str, str, bool]]:
    """(label, color, value, bold) — extra prs/repos columns fill leftover width."""
    active, window = row["consistency"]
    cols = [
        ("window", GRAPH_TEXT, f'last {row["days"]}d', False),
        ("activity", "#3fb950", nicer(row["activity"]), True),
        ("commits", "#58a6ff", nicer(row["commits"]), True),
    ]
    if "prs" in row:
        cols.append(("prs", "#f778ba", nicer(row["prs"]), True))
    if "repos" in row:
        cols.append(("repos", "#79c0ff", nicer(row["repos"]), True))
    cols += [
        ("edits", "#d29922", nicer(row["edits"]), True),
        ("days", "#a371f7", f"{active}/{window}", True),
    ]
    return cols


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
    """Activity table — columns spread across leftover panel width."""
    row_h = max(16, (height - 50) // max(len(rows), 1))
    y_title = oy + 12
    y_life = oy + 26
    y_cols = oy + 40
    y0 = oy + 54
    inset = 12
    right = ox + width - inset
    x0 = ox + inset
    spec = _stats_columns(rows[0]) if rows else []
    n = max(len(spec), 1)
    slot = (width - 2 * inset) / n
    xs = [x0 + int(slot * i) for i in range(n)]
    parts = [
        f'<text x="{x0}" y="{y_title}" fill="{GRAPH_TEXT}" font-family="{FONT}" font-size="11" font-weight="700">Activity</text>',
        f'<text x="{right}" y="{y_title}" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">as of {as_of.strftime("%Y-%m-%d")}</text>',
    ]
    if lifetime:
        parts.append(
            f'<text x="{x0}" y="{y_life}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">'
            f'lifetime · {nicer(lifetime["activity"])} act · {nicer(lifetime["commits"])} commits · {nicer(lifetime["edits"])} edits'
            f"</text>"
        )
    for x, (label, *_) in zip(xs, spec):
        parts.append(
            f'<text x="{x}" y="{y_cols}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="9">{label}</text>'
        )
    y = y0
    for row in rows:
        for x, (_label, color, text, bold) in zip(xs, _stats_columns(row)):
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
    """Square contribution cells at compact height; leftover width goes to Activity."""
    weeks, max_count = _calendar_weeks(days, as_of)
    pad = 10
    gap = 10
    g_parts, g_w, g_h = _graph_body(weeks, max_count, ox=pad, oy=pad, cell=11, gap=2)
    height = g_h + 2 * pad
    stats_x = pad + g_w + gap
    stats_w = PANEL_WIDTH - stats_x - pad
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PANEL_WIDTH}" height="{height}" viewBox="0 0 {PANEL_WIDTH} {height}" role="img" aria-label="Contributions and activity, last 90 days">',
        "<title>Contributions and activity</title>",
        f'<rect width="{PANEL_WIDTH}" height="{height}" rx="8" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
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
        "prs": collection["totalPullRequestContributions"],
        "issues": collection["totalIssueContributions"],
        "reviews": collection["totalPullRequestReviewContributions"],
        "repo_count": len(repos),
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
    spec = _stats_columns(rows[0]) if rows else []
    n = max(len(spec), 1)
    slot = (width - 20) / n
    xs = [10 + int(slot * i) for i in range(n)]
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
    for x, (label, *_) in zip(xs, spec):
        lines.append(
            f'<text x="{x}" y="{y_cols}" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="8">{label}</text>'
        )
    y = y0
    for row in rows:
        for x, (_label, color, text, bold) in zip(xs, _stats_columns(row)):
            weight = ' font-weight="700"' if bold else ""
            lines.append(
                f'<text x="{x}" y="{y}" fill="{color}" font-family="{MONO}" font-size="10"{weight}>{text}</text>'
            )
        y += row_h
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_repos_strip_svg(repos: list[dict], width: int = PANEL_WIDTH, height: int = 72) -> str:
    """Chip row that overflows the panel, with a fade so the overflow is obvious."""
    fade_w = 96
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Repos contributed to in the last 90 days">',
        "<defs>",
        '<linearGradient id="chipFade" x1="0" y1="0" x2="1" y2="0">',
        '<stop offset="0%" stop-color="#0d1117" stop-opacity="0"/>',
        '<stop offset="55%" stop-color="#0d1117" stop-opacity="0.75"/>',
        '<stop offset="100%" stop-color="#0d1117" stop-opacity="1"/>',
        "</linearGradient>",
        f'<clipPath id="chipClip"><rect width="{width}" height="{height}" rx="6"/></clipPath>',
        "</defs>",
        f'<rect width="{width}" height="{height}" rx="6" fill="{GRAPH_BG}" stroke="{GRAPH_BORDER}"/>',
        '<g clip-path="url(#chipClip)">',
    ]
    x = 12
    y = 20
    chip_h = 32
    for repo in repos:
        short = repo["name"].split("/")[-1]
        label = f"{short}  {repo['count']}"
        chip_w = min(max(len(label) * 7 + 24, 80), 176)
        parts.append(
            f'<rect x="{x}" y="{y}" width="{chip_w}" height="{chip_h}" rx="16" fill="#161b22" stroke="{GRAPH_BORDER}"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 21}" fill="{GRAPH_TEXT}" font-family="{MONO}" font-size="11">{_xml(label)}</text>'
        )
        x += chip_w + 8
        if x > width + 240:
            break
    parts.append(f'<rect x="{width - fade_w}" y="0" width="{fade_w}" height="{height}" fill="url(#chipFade)"/>')
    parts.append(
        f'<text x="{width - 14}" y="{height // 2 + 4}" text-anchor="end" fill="{GRAPH_MUTED}" font-family="{MONO}" font-size="11">more →</text>'
    )
    parts.append("</g></svg>")
    return "\n".join(parts) + "\n"


def repos_grid_html(repos: list[dict], per_row: int = 4) -> str:
    """Wrapped chip table — GitHub strips overflow CSS, so wrap instead of a fake scroller."""
    rows: list[str] = []
    for i in range(0, len(repos), per_row):
        chunk = repos[i : i + per_row]
        tds = []
        for repo in chunk:
            short = repo["name"].split("/")[-1]
            tds.append(
                '<td valign="middle">'
                f'<a href="https://github.com/{repo["name"]}"><code>{short}</code></a>'
                f" · {repo['count']}"
                "</td>"
            )
        while len(tds) < per_row:
            tds.append("<td></td>")
        rows.append("<tr>\n" + "\n".join(tds) + "\n</tr>")
    return "<table width=\"100%\">\n" + "\n".join(rows) + "\n</table>"


def render_readme_block() -> str:
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
            END_MARK,
            "",
        ]
    )


def render_contributed_block(repos: list[dict]) -> str:
    return "\n".join(
        [
            CONTRIB_START,
            "",
            f"### Repos contributed to in the last 90 days ({len(repos)})",
            "",
            '<p align="center">',
            '<img src="assets/repos-90d.svg" alt="Repos contributed to in the last 90 days" width="100%">',
            "</p>",
            "",
            repos_grid_html(repos),
            "",
            CONTRIB_END,
            "",
        ]
    )


def patch_readme(activity_block: str, contrib_block: str) -> None:
    text = README.read_text()
    pattern = re.compile(
        re.escape(START_MARK) + r".*?" + re.escape(END_MARK),
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(activity_block.strip(), text)
    else:
        section = re.compile(r"(## Activity\n)(.*?)(\n## )", re.DOTALL)
        text, n = section.subn(r"\1\n" + activity_block + r"\3", text, count=1)
        if n != 1:
            raise SystemExit("failed to insert activity block")
    contrib_pattern = re.compile(
        re.escape(CONTRIB_START) + r".*?" + re.escape(CONTRIB_END),
        re.DOTALL,
    )
    if contrib_pattern.search(text):
        text = contrib_pattern.sub(contrib_block.strip(), text)
    else:
        after_top = re.search(
            r"(## Top repositories\n.*?</table>\n)",
            text,
            re.DOTALL,
        )
        if after_top:
            text = (
                text[: after_top.end()]
                + "\n"
                + contrib_block.strip()
                + "\n\n"
                + text[after_top.end() :]
            )
        else:
            marker = "## Devrecated Solutions"
            idx = text.find(marker)
            if idx < 0:
                raise SystemExit("failed to insert contributed block")
            text = text[:idx] + contrib_block.strip() + "\n\n" + text[idx:]
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
            "prs": by_window[days]["prs"],
            "repos": by_window[days]["repo_count"],
            "consistency": list(consistency[days]),
        }
        for days in WINDOWS
    ]
    repos = by_window[90]["repos"]

    OUT_STATS.parent.mkdir(parents=True, exist_ok=True)
    panel_svg = render_activity_panel(calendar_days, rows, now, lifetime=lifetime)
    OUT_PANEL.write_text(panel_svg)
    OUT_REPOS.write_text(render_repos_strip_svg(repos))
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
    patch_readme(render_readme_block(), render_contributed_block(repos))
    old = ROOT / "assets" / "activity-repos.svg"
    if old.exists():
        old.unlink()
    print(json.dumps({"lifetime": lifetime, "windows": rows}, indent=2, default=str))
    print(f"graph_days={len(calendar_days)} repos_90d={len(repos)} edit_repos={len(edit_repos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
