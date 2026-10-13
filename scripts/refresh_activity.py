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
OUT_SVG = ROOT / "assets" / "activity-stats.svg"
OUT_JSON = ROOT / "assets" / "activity-stats.json"
LOGIN = "adamsiwiec1"
USER_ID = "MDQ6VXNlcjczMDg1OTI3"
WINDOWS = (90, 60, 30, 7)
START_MARK = "<!-- activity:start -->"
END_MARK = "<!-- activity:end -->"


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
            f'<p align="center"><img src="assets/activity-stats.svg" alt="Activity, commits, and line edits for the last 90, 60, 30, and 7 days" width="100%"></p>',
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
        # Insert after the contribution chart under ## Activity
        needle = "## Activity\n"
        if needle not in text:
            raise SystemExit("## Activity section missing from README.md")
        # Replace entire Activity section body until next ##
        section = re.compile(r"(## Activity\n)(.*?)(\n## )", re.DOTALL)
        replacement = (
            r"\1\n"
            '<p align="center">\n'
            '  <a href="https://github.com/adamsiwiec1">\n'
            '    <img src="https://ghchart.rshah.org/40c463/adamsiwiec1" alt="GitHub contribution graph" width="100%">\n'
            "  </a>\n"
            "</p>\n\n"
            + block
            + r"\3"
        )
        text, n = section.subn(replacement, text, count=1)
        if n != 1:
            raise SystemExit("failed to insert activity block")
    README.write_text(text)


def main() -> int:
    now = dt.datetime.now(timezone.utc)
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

    OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
    OUT_SVG.write_text(render_stats_svg(rows, now))
    OUT_JSON.write_text(
        json.dumps(
            {
                "as_of": iso(now),
                "windows": rows,
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
    # Drop the old ultra-wide repos SVG if present
    old = ROOT / "assets" / "activity-repos.svg"
    if old.exists():
        old.unlink()
    print(json.dumps(rows, indent=2))
    print(f"repos_90d={len(repos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
