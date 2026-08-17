from tools.richmack_metrics import component_scores


def sample():
    return component_scores(
        merged_prs=6,
        closed_issues=5,
        open_issues=4,
        open_prs=0,
        tests_passed=11,
        tests_total=11,
        ci_success=True,
        active_hours=10,
        python_files=5,
        lines=1500,
        functions=50,
        test_count=11,
    )


def test_complexity_score_is_bounded():
    result = sample()

    assert 0 <= result["complexity"] <= 10


def test_maintainability_score_is_bounded():
    result = sample()

    assert 0 <= result["maintainability"] <= 10


def test_throughput_score_is_bounded():
    result = sample()

    assert 0 <= result["throughput"] <= 10


def test_reliability_score_is_bounded():
    result = sample()

    assert result["reliability"] == 10.0


def test_velocity_score_is_bounded():
    result = sample()

    assert 0 <= result["velocity"] <= 10


def test_weissman_score_is_weighted_components():
    result = sample()

    expected = round(
        result["complexity"] * 0.20
        + result["maintainability"] * 0.20
        + result["throughput"] * 0.25
        + result["reliability"] * 0.20
        + result["velocity"] * 0.15,
        2,
    )

    assert result["final_score"] == expected


def test_score_is_deterministic():
    assert sample() == sample()
