#!/usr/bin/env python3

from __future__ import annotations

import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def clamp(value, low=0.0, high=10.0):
    return max(low, min(high, value))


def python_stats(root=ROOT):
    files = list((root / "app").rglob("*.py"))

    total_lines = 0
    functions = 0
    classes = 0

    for path in files:
        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        total_lines += len(text.splitlines())

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        functions += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(tree)
        )

        classes += sum(
            isinstance(node, ast.ClassDef)
            for node in ast.walk(tree)
        )

    return {
        "python_files": len(files),
        "lines": total_lines,
        "functions": functions,
        "classes": classes,
    }


def count_tests(root=ROOT):
    tests = 0

    for path in (root / "tests").glob("test_*.py"):
        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        tests += sum(
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        )

    return tests


def component_scores(
    *,
    merged_prs,
    closed_issues,
    open_issues,
    open_prs,
    tests_passed,
    tests_total,
    ci_success,
    active_hours,
    python_files,
    lines,
    functions,
    test_count,
):
    active_hours = max(float(active_hours), 0.1)

    # ---------------------------------------------------------
    # COMPLEXITY — 0..10
    #
    # Rewards meaningful functional density without rewarding
    # unlimited code volume.
    # ---------------------------------------------------------

    function_density = (
        functions / max(python_files, 1)
    )

    line_complexity = min(
        lines / 1500.0,
        1.0,
    )

    function_complexity = min(
        function_density / 25.0,
        1.0,
    )

    delivered_features = (
        merged_prs + closed_issues
    )

    feature_complexity = min(
        delivered_features / 12.0,
        1.0,
    )

    complexity = clamp(
        (
            line_complexity * 0.30
            + function_complexity * 0.30
            + feature_complexity * 0.40
        )
        * 10
    )

    # ---------------------------------------------------------
    # MAINTAINABILITY — 0..10
    #
    # Tests, test-to-function ratio, CI presence/health,
    # and manageable functional density.
    # ---------------------------------------------------------

    test_ratio = min(
        test_count / max(functions * 0.25, 1),
        1.0,
    )

    test_volume = min(
        test_count / 15.0,
        1.0,
    )

    ci_factor = 1.0 if ci_success else 0.0

    density_penalty = min(
        function_density / 50.0,
        1.0,
    )

    structure_factor = (
        1.0 - density_penalty * 0.30
    )

    maintainability = clamp(
        (
            test_ratio * 0.35
            + test_volume * 0.20
            + ci_factor * 0.30
            + structure_factor * 0.15
        )
        * 10
    )

    # ---------------------------------------------------------
    # THROUGHPUT — 0..10
    #
    # Delivered units per active development hour.
    #
    # 1 shipped unit / hour ~= 10/10.
    # ---------------------------------------------------------

    shipped_units = (
        merged_prs
        + closed_issues
    )

    units_per_hour = (
        shipped_units / active_hours
    )

    throughput = clamp(
        units_per_hour * 10
    )

    # ---------------------------------------------------------
    # RELIABILITY — 0..10
    #
    # Automated test pass rate + CI health.
    # ---------------------------------------------------------

    pass_rate = (
        tests_passed / tests_total
        if tests_total
        else 0
    )

    reliability = clamp(
        (
            pass_rate * 0.75
            + (1.0 if ci_success else 0.0) * 0.25
        )
        * 10
    )

    # ---------------------------------------------------------
    # VELOCITY — 0..10
    #
    # Closed roadmap work relative to hours, penalized by
    # unresolved work.
    # ---------------------------------------------------------

    completed_per_hour = (
        closed_issues / active_hours
    )

    completion_ratio = (
        closed_issues
        / max(
            closed_issues + open_issues,
            1,
        )
    )

    velocity = clamp(
        (
            min(completed_per_hour, 1.0) * 0.60
            + completion_ratio * 0.40
        )
        * 10
    )

    final_score = round(
        complexity * 0.20
        + maintainability * 0.20
        + throughput * 0.25
        + reliability * 0.20
        + velocity * 0.15,
        2,
    )

    return {
        "complexity": round(complexity, 2),
        "maintainability": round(
            maintainability,
            2,
        ),
        "throughput": round(
            throughput,
            2,
        ),
        "reliability": round(
            reliability,
            2,
        ),
        "velocity": round(
            velocity,
            2,
        ),
        "final_score": final_score,
        "units_per_hour": round(
            units_per_hour,
            3,
        ),
    }


def calculate_metrics(
    *,
    merged_prs,
    closed_issues,
    open_issues,
    open_prs,
    tests_passed,
    tests_total,
    ci_success,
    active_hours,
):
    stats = python_stats()
    test_count = count_tests()

    scores = component_scores(
        merged_prs=merged_prs,
        closed_issues=closed_issues,
        open_issues=open_issues,
        open_prs=open_prs,
        tests_passed=tests_passed,
        tests_total=tests_total,
        ci_success=ci_success,
        active_hours=active_hours,
        python_files=stats["python_files"],
        lines=stats["lines"],
        functions=stats["functions"],
        test_count=test_count,
    )

    return {
        **stats,
        "test_count": test_count,
        **scores,
    }


def print_scorecard(metrics):
    print("=== SERVESENSE ENGINEERING SCORECARD ===")
    print()

    print(
        f"Complexity:      "
        f"{metrics['complexity']:.2f} / 10"
    )

    print(
        f"Maintainability: "
        f"{metrics['maintainability']:.2f} / 10"
    )

    print(
        f"Throughput:      "
        f"{metrics['throughput']:.2f} / 10"
    )

    print(
        f"Reliability:     "
        f"{metrics['reliability']:.2f} / 10"
    )

    print(
        f"Velocity:        "
        f"{metrics['velocity']:.2f} / 10"
    )

    print()
    print(
        f"WEISSMAN-STYLE SCORE: "
        f"{metrics['final_score']:.2f} / 10"
    )

    print()
    print(
        f"Delivery rate: "
        f"{metrics['units_per_hour']:.3f} "
        f"units/hour"
    )

    print(
        f"Python LOC: "
        f"{metrics['lines']}"
    )

    print(
        f"Functions: "
        f"{metrics['functions']}"
    )

    print(
        f"Automated tests: "
        f"{metrics['test_count']}"
    )


if __name__ == "__main__":
    active_hours = float(
        os.getenv(
            "SERVESENSE_ACTIVE_HOURS",
            "1",
        )
    )

    metrics = calculate_metrics(
        merged_prs=int(
            os.getenv(
                "SERVESENSE_MERGED_PRS",
                "0",
            )
        ),
        closed_issues=int(
            os.getenv(
                "SERVESENSE_CLOSED_ISSUES",
                "0",
            )
        ),
        open_issues=int(
            os.getenv(
                "SERVESENSE_OPEN_ISSUES",
                "0",
            )
        ),
        open_prs=int(
            os.getenv(
                "SERVESENSE_OPEN_PRS",
                "0",
            )
        ),
        tests_passed=int(
            os.getenv(
                "SERVESENSE_TESTS_PASSED",
                "0",
            )
        ),
        tests_total=int(
            os.getenv(
                "SERVESENSE_TESTS_TOTAL",
                "0",
            )
        ),
        ci_success=os.getenv(
            "SERVESENSE_CI_SUCCESS",
            "0",
        ) == "1",
        active_hours=active_hours,
    )

    print_scorecard(metrics)
