#!/usr/bin/env python3

"""
Internal ServeSense engineering metrics.

Not exposed through the restaurant-facing Flask application.
These formulas are intended for CI/developer analysis only.
"""


def calculate_metrics(
    *,
    merged_prs,
    closed_issues,
    open_issues,
    open_prs,
    issue_closure_rate,
    pr_merge_rate,
    roadmap_completion,
):
    richmack_score = round(
        issue_closure_rate * 0.40
        + pr_merge_rate * 0.35
        + roadmap_completion * 0.25,
        1,
    )

    weissman_score = round(
        (
            (1 + merged_prs)
            * (1 + closed_issues)
        )
        /
        (
            1
            + open_issues
            + open_prs
        ),
        2,
    )

    return {
        "richmack_score": richmack_score,
        "weissman_score": weissman_score,
    }


if __name__ == "__main__":
    example = calculate_metrics(
        merged_prs=4,
        closed_issues=4,
        open_issues=5,
        open_prs=0,
        issue_closure_rate=44.4,
        pr_merge_rate=100.0,
        roadmap_completion=44.4,
    )

    print(
        f"Richmack Score: "
        f"{example['richmack_score']}/100"
    )

    print(
        f"Weissman-style Index: "
        f"{example['weissman_score']}x"
    )
