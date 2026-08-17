from tools.richmack_metrics import calculate_metrics


def test_weissman_score():
    metrics = calculate_metrics(
        merged_prs=4,
        closed_issues=4,
        open_issues=5,
        open_prs=0,
        issue_closure_rate=44.4,
        pr_merge_rate=100.0,
        roadmap_completion=44.4,
    )

    expected = round(
        ((1 + 4) * (1 + 4))
        / (1 + 5 + 0),
        2,
    )

    assert metrics["weissman_score"] == expected


def test_richmack_score():
    metrics = calculate_metrics(
        merged_prs=4,
        closed_issues=4,
        open_issues=5,
        open_prs=0,
        issue_closure_rate=44.4,
        pr_merge_rate=100.0,
        roadmap_completion=44.4,
    )

    expected = round(
        44.4 * 0.40
        + 100.0 * 0.35
        + 44.4 * 0.25,
        1,
    )

    assert metrics["richmack_score"] == expected


def test_zero_work_does_not_divide_by_zero():
    metrics = calculate_metrics(
        merged_prs=0,
        closed_issues=0,
        open_issues=0,
        open_prs=0,
        issue_closure_rate=0,
        pr_merge_rate=0,
        roadmap_completion=0,
    )

    assert metrics["weissman_score"] == 1.0
    assert metrics["richmack_score"] == 0.0
