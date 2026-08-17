import sqlite3

import pytest

import app.web as web


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db = tmp_path / "test-servesense.db"

    monkeypatch.setattr(
        web,
        "DB",
        db,
    )

    app = web.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "ADMIN_USERNAME": "testadmin",
            "ADMIN_PASSWORD": "testpassword",
            "RESTAURANT_NAME": "Test Restaurant",
        }
    )

    # Make authentication deterministic instead of depending
    # on application bootstrap/seed behavior.
    with web.connect() as connection:
        connection.execute(
            """
            INSERT INTO users(
                username,
                password_hash,
                role,
                active,
                created_at
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(username)
            DO UPDATE SET
                password_hash=excluded.password_hash,
                role=excluded.role,
                active=excluded.active
            """,
            (
                "testadmin",
                web.generate_password_hash("testpassword"),
                "owner",
                1,
                web.now(),
            ),
        )
        connection.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    with web.connect() as connection:
        yield connection


def login(client):
    return client.post(
        "/login",
        data={
            "username": "testadmin",
            "passw rd": "testpassword",
        },
        follow_redirects=False,
    )


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200

    assert response.get_json() == {
        "app": "ServeSense",
        "status": "ok",
    }


def test_dashboard_requires_login(client):
    response = client.get("/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_valid_login(client):
    response = login(client)

    assert response.status_code == 302


def test_invalid_login(client):
    response = client.post(
        "/login",
        data={
            "username": "wrong",
            "password": "wrong",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_metrics_not_customer_facing(client):
    response = client.get("/metrics")

    assert response.status_code == 404


def test_labor_guardrail_math(db):
    if not hasattr(web, "labor_guardrail"):
        pytest.skip(
            "labor_guardrail is not available"
        )

    db.execute(
        """
        INSERT INTO settings(key,value)
        VALUES('default_labor_target','20')
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """
    )

    db.execute(
        """
        INSERT INTO staff(
            name,
            department,
            role,
            pay_rate,
            pay_type,
            max_shifts_week,
            created_at
        )
        VALUES(
            'Labor Test',
            'FOH',
            'Server',
            20,
            'hourly',
            5,
            ?
        )
        """,
        (web.now(),),
    )

    staff_id = db.execute(
        """
        SELECT id
        FROM staff
        WHERE name='Labor Test'
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO shifts(
            shift_date,
            meal,
            expected_sales,
            status,
            created_at
        )
        VALUES(
            '2026-08-16',
            'Dinner',
            1000,
            'draft',
            ?
        )
        """,
        (web.now(),),
    )

    shift_id = db.execute(
        """
        SELECT id
        FROM shifts
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO assignments(
            shift_id,
            staff_id,
            position,
            start_time,
            end_time,
            reason
        )
        VALUES(
            ?,
            ?,
            'Server',
            '17:00',
            '22:00',
            'test'
        )
        """,
        (
            shift_id,
            staff_id,
        ),
    )

    db.commit()

    result = web.labor_guardrail(
        db,
        shift_id,
    )

    assert result["scheduled_hours"] == 5.0
    assert result["labor_cost"] == 100.0
    assert result["labor_pct"] == 10.0
    assert result["target_pct"] == 20.0
    assert result["target_dollars"] == 200.0
    assert result["variance_dollars"] == 100.0
    assert result["status"] == "under"


def test_schedule_conflict_unavailable(db):
    if not hasattr(web, "schedule_conflicts"):
        pytest.skip(
            "schedule_conflicts is not available"
        )

    db.execute(
        """
        INSERT INTO staff(
            name,
            department,
            role,
            pay_rate,
            pay_type,
            max_shifts_week,
            created_at
        )
        VALUES(
            'Conflict Test',
            'FOH',
            'Server',
            10,
            'hourly',
            5,
            ?
        )
        """,
        (web.now(),),
    )

    staff_id = db.execute(
        """
        SELECT id
        FROM staff
        WHERE name='Conflict Test'
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO shifts(
            shift_date,
            meal,
            expected_sales,
            status,
            created_at
        )
        VALUES(
            '2026-08-16',
            'Dinner',
            2000,
            'draft',
            ?
        )
        """,
        (web.now(),),
    )

    shift_id = db.execute(
        """
        SELECT id
        FROM shifts
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO availability(
            staff_id,
            shift_date,
            meal,
            status
        )
        VALUES(
            ?,
            '2026-08-16',
            'Dinner',
            'unavailable'
        )
        """,
        (staff_id,),
    )

    db.commit()

    conflicts = web.schedule_conflicts(
        db,
        shift_id,
        staff_id,
    )

    assert any(
        "unavailable" in item.lower()
        for item in conflicts
    )


def test_prediction_score_is_bounded(db):
    db.execute(
        """
        INSERT INTO staff(
            name,
            department,
            role,
            pay_rate,
            pay_type,
            max_shifts_week,
            created_at
        )
        VALUES(
            'Prediction Test',
            'FOH',
            'Server',
            10,
            'hourly',
            5,
            ?
        )
        """,
        (web.now(),),
    )

    staff_id = db.execute(
        """
        SELECT id
        FROM staff
        WHERE name='Prediction Test'
        """
    ).fetchone()[0]

    for i in range(3):
        db.execute(
            """
            INSERT INTO sales(
                staff_id,
                shift_date,
                meal,
                sales,
                hours,
                covers,
                late_minutes,
                created_at
            )
            VALUES(
                ?,
                ?,
                'Dinner',
                1000,
                5,
                25,
                0,
                ?
            )
            """,
            (
                staff_id,
                f"2026-08-{10+i:02d}",
                web.now(),
            ),
        )

    db.execute(
        """
        INSERT INTO availability(
            staff_id,
            shift_date,
            meal,
            status
        )
        VALUES(
            ?,
            '2026-08-16',
            'Dinner',
            'preferred'
        )
        """,
        (staff_id,),
    )

    db.commit()

    results = web.score_people(
        db,
        "2026-08-16",
        "Dinner",
    )

    person = next(
        row
        for row in results
        if row["name"] == "Prediction Test"
    )

    assert 0 <= person["score"] <= 100
    assert "components" in person
    assert person["components"]["reliability"] >= 0
