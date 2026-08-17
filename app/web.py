from __future__ import annotations
import csv, io, os, secrets, sqlite3, zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"; DB = DATA / "servesense.db"
login_manager = LoginManager(); login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, username, role): self.id=username; self.role=role
    @property
    def is_owner(self): return self.role == "owner"
    @property
    def is_admin(self): return self.role in {"owner","admin","manager"}


def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

@login_manager.user_loader
def load_user(uid):
    with connect() as c: r=c.execute("SELECT username,role,active FROM users WHERE username=?",(uid,)).fetchone()
    return User(r["username"],r["role"]) if r and r["active"] else None

def init_db(app):
    DATA.mkdir(exist_ok=True)
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'admin',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,department TEXT NOT NULL,role TEXT NOT NULL,email TEXT DEFAULT '',phone TEXT DEFAULT '',hire_date TEXT DEFAULT '',birthday TEXT DEFAULT '',pay_rate REAL NOT NULL DEFAULT 0,pay_type TEXT NOT NULL DEFAULT 'hourly',max_shifts_week INTEGER NOT NULL DEFAULT 5,active INTEGER NOT NULL DEFAULT 1,certifications TEXT DEFAULT '',notes TEXT DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,staff_id INTEGER NOT NULL,shift_date TEXT NOT NULL,meal TEXT NOT NULL,sales REAL DEFAULT 0,hours REAL DEFAULT 0,covers INTEGER DEFAULT 0,tips REAL DEFAULT 0,late_minutes INTEGER DEFAULT 0,notes TEXT DEFAULT '',created_at TEXT NOT NULL,FOREIGN KEY(staff_id) REFERENCES staff(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS availability(id INTEGER PRIMARY KEY AUTOINCREMENT,staff_id INTEGER NOT NULL,shift_date TEXT NOT NULL,meal TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'available',note TEXT DEFAULT '',UNIQUE(staff_id,shift_date,meal),FOREIGN KEY(staff_id) REFERENCES staff(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY AUTOINCREMENT,shift_date TEXT NOT NULL,meal TEXT NOT NULL,expected_sales REAL DEFAULT 0,notes TEXT DEFAULT '',status TEXT DEFAULT 'draft',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,shift_id INTEGER NOT NULL,staff_id INTEGER NOT NULL,position TEXT NOT NULL,start_time TEXT DEFAULT '',end_time TEXT DEFAULT '',score REAL DEFAULT 0,reason TEXT DEFAULT '',sort_order INTEGER DEFAULT 0,UNIQUE(shift_id,staff_id),FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE,FOREIGN KEY(staff_id) REFERENCES staff(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY AUTOINCREMENT,reservation_date TEXT NOT NULL,meal TEXT NOT NULL,party_name TEXT NOT NULL,party_size INTEGER NOT NULL,notes TEXT DEFAULT '');
        ''')
        u=app.config['ADMIN_USERNAME']
        if not c.execute("SELECT 1 FROM users WHERE username=?",(u,)).fetchone():
            c.execute("INSERT INTO users VALUES(?,?,?,?,?)",(u,generate_password_hash(app.config['ADMIN_PASSWORD']),"owner",1,now()))
        c.execute("INSERT OR IGNORE INTO settings VALUES('restaurant_name',?)",(app.config['RESTAURANT_NAME'],))

def setting(c,key,default=''):
    r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return r[0] if r else default

def score_people(c, shift_date, meal):
    rows = c.execute(
        """
        SELECT
            s.*,

            COUNT(sa.id) AS logged,

            COALESCE(
                AVG(
                    CASE
                        WHEN sa.hours > 0
                        THEN sa.sales / sa.hours
                    END
                ),
                0
            ) AS career_sph,

            COALESCE(
                AVG(
                    CASE
                        WHEN sa.meal = ?
                         AND sa.hours > 0
                        THEN sa.sales / sa.hours
                    END
                ),
                0
            ) AS meal_sph,

            COALESCE(
                AVG(
                    CASE
                        WHEN sa.meal = ?
                        THEN sa.sales
                    END
                ),
                0
            ) AS meal_avg_sales,

            COALESCE(
                AVG(
                    CASE
                        WHEN sa.hours > 0
                        THEN CAST(sa.covers AS REAL) / sa.hours
                    END
                ),
                0
            ) AS covers_per_hour,

            COALESCE(
                AVG(
                    CASE
                        WHEN sa.shift_date >= date(?, '-14 day')
                         AND sa.hours > 0
                        THEN sa.sales / sa.hours
                    END
                ),
                0
            ) AS recent_sph,

            COALESCE(
                SUM(
                    CASE
                        WHEN sa.shift_date >= date(?, '-7 day')
                        THEN sa.hours
                        ELSE 0
                    END
                ),
                0
            ) AS recent_hours,

            COALESCE(
                AVG(sa.late_minutes),
                0
            ) AS avg_late_minutes,

            COALESCE(
                SUM(
                    CASE
                        WHEN sa.late_minutes > 0
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS late_shifts,

            COALESCE(
                a.status,
                'available'
            ) AS avail

        FROM staff s

        LEFT JOIN sales sa
          ON sa.staff_id = s.id

        LEFT JOIN availability a
          ON a.staff_id = s.id
         AND a.shift_date = ?
         AND a.meal = ?

        WHERE s.active = 1

        GROUP BY s.id
        """,
        (
            meal,
            meal,
            shift_date,
            shift_date,
            shift_date,
            meal,
        ),
    ).fetchall()

    if not rows:
        return []

    active = [
        r for r in rows
        if r["avail"] not in ("unavailable", "pto")
    ]

    if not active:
        return []

    def max_value(key, minimum=1.0):
        return max(
            minimum,
            max(float(r[key] or 0) for r in active)
        )

    max_meal_sph = max_value("meal_sph")
    max_career_sph = max_value("career_sph")
    max_recent_sph = max_value("recent_sph")
    max_covers = max_value("covers_per_hour")
    max_experience = max_value("logged")

    result = []

    for r in active:
        logged = int(r["logged"] or 0)

        # ----------------------------------------------------
        # 1. Meal-specific efficiency — 25 points
        # ----------------------------------------------------
        source_sph = (
            float(r["meal_sph"] or 0)
            if float(r["meal_sph"] or 0) > 0
            else float(r["career_sph"] or 0)
        )

        sph_base = (
            max_meal_sph
            if float(r["meal_sph"] or 0) > 0
            else max_career_sph
        )

        efficiency_score = min(
            25.0,
            (source_sph / sph_base) * 25.0
            if sph_base > 0
            else 0
        )

        # ----------------------------------------------------
        # 2. Recent performance — 15 points
        # ----------------------------------------------------
        recent_score = min(
            15.0,
            (
                float(r["recent_sph"] or 0)
                / max_recent_sph
            ) * 15.0
            if max_recent_sph > 0
            else 0
        )

        # ----------------------------------------------------
        # 3. Guest throughput — 10 points
        # ----------------------------------------------------
        cover_score = min(
            10.0,
            (
                float(r["covers_per_hour"] or 0)
                / max_covers
            ) * 10.0
            if max_covers > 0
            else 0
        )

        # ----------------------------------------------------
        # 4. Experience — 10 points
        # ----------------------------------------------------
        experience_score = min(
            10.0,
            (
                logged
                / max_experience
            ) * 10.0
            if max_experience > 0
            else 0
        )

        # ----------------------------------------------------
        # 5. Reliability — 15 points
        # ----------------------------------------------------
        late_minutes = float(
            r["avg_late_minutes"] or 0
        )

        late_shifts = int(
            r["late_shifts"] or 0
        )

        reliability_penalty = min(
            15.0,
            late_minutes * 0.35
            + late_shifts * 0.75
        )

        reliability_score = max(
            0.0,
            15.0 - reliability_penalty
        )

        # ----------------------------------------------------
        # 6. Availability preference — 10 points
        # ----------------------------------------------------
        preference_score = (
            10.0
            if r["avail"] == "preferred"
            else 5.0
        )

        # ----------------------------------------------------
        # 7. Fatigue / workload — 10 points
        # ----------------------------------------------------
        recent_hours = float(
            r["recent_hours"] or 0
        )

        if recent_hours <= 32:
            fatigue_score = 10.0
        elif recent_hours >= 48:
            fatigue_score = 0.0
        else:
            fatigue_score = max(
                0.0,
                10.0
                - ((recent_hours - 32) / 16) * 10.0
            )

        # ----------------------------------------------------
        # 8. Historical meal familiarity — 5 points
        # ----------------------------------------------------
        meal_familiarity_score = (
            5.0
            if float(r["meal_sph"] or 0) > 0
            else 2.5
            if logged > 0
            else 0.0
        )

        score = round(
            efficiency_score
            + recent_score
            + cover_score
            + experience_score
            + reliability_score
            + preference_score
            + fatigue_score
            + meal_familiarity_score,
            1
        )

        components = {
            "efficiency": round(
                efficiency_score,
                1
            ),
            "recent": round(
                recent_score,
                1
            ),
            "covers": round(
                cover_score,
                1
            ),
            "experience": round(
                experience_score,
                1
            ),
            "reliability": round(
                reliability_score,
                1
            ),
            "preference": round(
                preference_score,
                1
            ),
            "fatigue": round(
                fatigue_score,
                1
            ),
            "meal_fit": round(
                meal_familiarity_score,
                1
            ),
        }

        if logged == 0:
            reason = (
                "New employee · no historical performance yet"
            )
        else:
            reason_parts = [
                f"${source_sph:.0f}/labor hr",
                f"{float(r['covers_per_hour'] or 0):.1f} covers/hr",
                f"{logged} logged shifts",
            ]

            if float(r["recent_sph"] or 0) > 0:
                reason_parts.append(
                    f"${float(r['recent_sph']):.0f} recent SPLH"
                )

            if r["avail"] == "preferred":
                reason_parts.append(
                    "preferred shift"
                )

            if late_minutes > 0:
                reason_parts.append(
                    f"{late_minutes:.0f} avg late min"
                )
            else:
                reason_parts.append(
                    "strong punctuality"
                )

            if recent_hours > 32:
                reason_parts.append(
                    f"{recent_hours:.0f} recent hrs"
                )

            if float(r["meal_sph"] or 0) > 0:
                reason_parts.append(
                    f"{meal} experience"
                )

            reason = " · ".join(reason_parts)

        result.append(
            {
                **dict(r),
                "score": score,
                "reason": reason,
                "components": components,
            }
        )

    return sorted(
        result,
        key=lambda x: (
            x["score"],
            x["meal_sph"],
            x["career_sph"],
        ),
        reverse=True,
    )

def _minutes(value):
    if not value or ":" not in value:
        return None
    try:
        hour, minute = map(int, value.split(":", 1))
        return hour * 60 + minute
    except (TypeError, ValueError):
        return None


def schedule_conflicts(c, shift_id, staff_id, start_time="", end_time=""):
    shift = c.execute(
        "SELECT * FROM shifts WHERE id=?",
        (shift_id,)
    ).fetchone()

    staff = c.execute(
        "SELECT * FROM staff WHERE id=?",
        (staff_id,)
    ).fetchone()

    if not shift:
        return ["Shift not found."]

    if not staff or not staff["active"]:
        return ["Employee is inactive or does not exist."]

    conflicts = []

    availability = c.execute(
        """SELECT status,note
           FROM availability
           WHERE staff_id=?
             AND shift_date=?
             AND meal=?""",
        (
            staff_id,
            shift["shift_date"],
            shift["meal"]
        )
    ).fetchone()

    pto = c.execute(
        """SELECT 1
           FROM availability
           WHERE staff_id=?
             AND shift_date=?
             AND status='pto'
           LIMIT 1""",
        (
            staff_id,
            shift["shift_date"]
        )
    ).fetchone()

    if pto or (
        availability
        and availability["status"] == "pto"
    ):
        conflicts.append(
            "Employee is on PTO for this date."
        )

    elif (
        availability
        and availability["status"] == "unavailable"
    ):
        conflicts.append(
            f"Employee is unavailable for {shift['meal']}."
        )

    duplicate = c.execute(
        """SELECT sh.id
           FROM assignments a
           JOIN shifts sh ON sh.id=a.shift_id
           WHERE a.staff_id=?
             AND sh.id<>?
             AND sh.shift_date=?
             AND sh.meal=?
           LIMIT 1""",
        (
            staff_id,
            shift_id,
            shift["shift_date"],
            shift["meal"]
        )
    ).fetchone()

    if duplicate:
        conflicts.append(
            "Employee is already assigned to another "
            f"{shift['meal']} shift on this date."
        )

    new_start = _minutes(start_time)
    new_end = _minutes(end_time)

    if (
        new_start is not None
        and new_end is not None
        and new_end > new_start
    ):
        assignments = c.execute(
            """SELECT a.start_time,a.end_time,sh.meal
               FROM assignments a
               JOIN shifts sh ON sh.id=a.shift_id
               WHERE a.staff_id=?
                 AND sh.id<>?
                 AND sh.shift_date=?""",
            (
                staff_id,
                shift_id,
                shift["shift_date"]
            )
        ).fetchall()

        for other in assignments:
            old_start = _minutes(other["start_time"])
            old_end = _minutes(other["end_time"])

            if (
                old_start is not None
                and old_end is not None
                and max(new_start, old_start)
                    < min(new_end, old_end)
            ):
                conflicts.append(
                    "Shift time overlaps an existing "
                    f"{other['meal']} assignment."
                )
                break

    shift_day = date.fromisoformat(
        shift["shift_date"]
    )

    week_start = shift_day - timedelta(
        days=shift_day.weekday()
    )

    week_end = week_start + timedelta(days=6)

    weekly_count = c.execute(
        """SELECT COUNT(DISTINCT sh.id)
           FROM assignments a
           JOIN shifts sh ON sh.id=a.shift_id
           WHERE a.staff_id=?
             AND sh.id<>?
             AND sh.shift_date BETWEEN ? AND ?""",
        (
            staff_id,
            shift_id,
            week_start.isoformat(),
            week_end.isoformat()
        )
    ).fetchone()[0]

    if weekly_count >= staff["max_shifts_week"]:
        conflicts.append(
            "Weekly shift limit reached "
            f"({staff['max_shifts_week']})."
        )

    return conflicts



def _scheduled_hours(start_time, end_time, fallback=6.0):
    """Return planned hours, falling back when schedule times are blank."""
    start = _minutes(start_time)
    end = _minutes(end_time)

    if start is None or end is None or end <= start:
        return fallback

    return round((end - start) / 60.0, 2)


def labor_guardrail(c, shift_id):
    shift = c.execute(
        "SELECT * FROM shifts WHERE id=?",
        (shift_id,)
    ).fetchone()

    if not shift:
        return {
            "labor_cost": 0,
            "labor_pct": 0,
            "target_pct": 22,
            "target_dollars": 0,
            "variance_dollars": 0,
            "splh": 0,
            "scheduled_hours": 0,
            "status": "unknown",
            "assigned_count": 0,
        }

    try:
        target_pct = float(
            setting(
                c,
                "default_labor_target",
                "22"
            ) or 22
        )
    except (TypeError, ValueError):
        target_pct = 22.0

    expected_sales = float(
        shift["expected_sales"] or 0
    )

    assignments = c.execute(
        """SELECT
               a.start_time,
               a.end_time,
               s.pay_rate,
               s.pay_type
           FROM assignments a
           JOIN staff s ON s.id=a.staff_id
           WHERE a.shift_id=?""",
        (shift_id,)
    ).fetchall()

    labor_cost = 0.0
    scheduled_hours = 0.0

    for row in assignments:

        hours = _scheduled_hours(
            row["start_time"],
            row["end_time"]
        )

        scheduled_hours += hours

        if row["pay_type"] == "salary":
            # Approximate one scheduled shift as 1/5
            # of the employee's weekly salary cost.
            labor_cost += (
                float(row["pay_rate"] or 0)
                / 52.0
                / 5.0
            )
        else:
            labor_cost += (
                float(row["pay_rate"] or 0)
                * hours
            )

    target_dollars = (
        expected_sales
        * target_pct
        / 100.0
    )

    labor_pct = (
        labor_cost
        / expected_sales
        * 100.0
        if expected_sales > 0
        else 0.0
    )

    splh = (
        expected_sales
        / scheduled_hours
        if scheduled_hours > 0
        else 0.0
    )

    variance = target_dollars - labor_cost

    if expected_sales <= 0:
        status = "no-sales-target"
    elif labor_cost > target_dollars:
        status = "over"
    else:
        status = "under"

    return {
        "labor_cost": round(labor_cost, 2),
        "labor_pct": round(labor_pct, 1),
        "target_pct": round(target_pct, 1),
        "target_dollars": round(target_dollars, 2),
        "variance_dollars": round(variance, 2),
        "splh": round(splh, 2),
        "scheduled_hours": round(
            scheduled_hours,
            2
        ),
        "status": status,
        "assigned_count": len(assignments),
    }


def export_csv(name, headers, rows):
    s=io.StringIO(); w=csv.writer(s); w.writerow(headers); w.writerows(rows)
    b=io.BytesIO(s.getvalue().encode()); b.seek(0); return send_file(b,as_attachment=True,download_name=name,mimetype='text/csv')

def seed_demo(c):
    people=[
      ('Amanda Collins','Management','General Manager',35,'hourly'),('Jason Brooks','Management','Assistant GM',27,'hourly'),('Ethan Carter','Management','FOH Manager',24,'hourly'),
      ('Ashley Brown','FOH','Server',2.13,'hourly'),('Sarah Johnson','FOH','Server',2.13,'hourly'),('Jessica Kim','FOH','Server',2.13,'hourly'),('Michael Rodriguez','FOH','Server',2.13,'hourly'),('Emily Carter','FOH','Server',2.13,'hourly'),('Christopher Young','FOH','Server',2.13,'hourly'),
      ('Brandon White','FOH','Bartender',8,'hourly'),('Alexis Moore','FOH','Bartender',8,'hourly'),('Madison Hall','FOH','Host',14,'hourly'),('Hannah Scott','FOH','Host',14,'hourly'),('Kevin Adams','FOH','Busser',12,'hourly'),('Jose Martinez','FOH','Busser',12,'hourly'),('Chloe Evans','FOH','Food Runner',13,'hourly')]
    for n,d,r,p,pt in people:
        c.execute("INSERT OR IGNORE INTO staff(name,department,role,pay_rate,pay_type,max_shifts_week,created_at) VALUES(?,?,?,?,?,?,?)",(n,d,r,p,pt,5,now()))
    ids={r['name']:r['id'] for r in c.execute('SELECT id,name FROM staff')}
    base=date.today()-timedelta(days=28)
    server_sales={'Ashley Brown':2960,'Sarah Johnson':2820,'Jessica Kim':2710,'Michael Rodriguez':2590,'Emily Carter':2430,'Christopher Young':2240,'Brandon White':3350,'Alexis Moore':3180}
    for i in range(28):
        d=(base+timedelta(days=i)).isoformat(); meal='Dinner' if i%3 else 'Lunch'
        for j,(name,avg) in enumerate(server_sales.items()):
            if (i+j)%3==0: continue
            val=avg*(.82+((i+j)%7)*.055)
            c.execute("INSERT INTO sales(staff_id,shift_date,meal,sales,hours,covers,tips,created_at) VALUES(?,?,?,?,?,?,?,?)",(ids[name],d,meal,round(val,2),6+(j%3)*.5,int(val/42),round(val*.18,2),now()))
    target=date.today().isoformat()
    for name in ids:
        status='preferred' if name in ('Ashley Brown','Amanda Collins','Brandon White') else 'available'
        c.execute("INSERT OR REPLACE INTO availability(staff_id,shift_date,meal,status) VALUES(?,?,?,?)",(ids[name],target,'Dinner',status))

def create_app(test_config=None):
    app=Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY',secrets.token_hex(32)),ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','owner'),ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','ServeSenseDemo123!'),RESTAURANT_NAME=os.getenv('RESTAURANT_NAME','Copper Oak Kitchen & Bar'),APP_VERSION='1.0.0')
    if test_config: app.config.update(test_config)
    login_manager.init_app(app); init_db(app)

    @app.context_processor
    def ctx():
        with connect() as c: rn=setting(c,'restaurant_name',app.config['RESTAURANT_NAME'])
        return {'restaurant_name':rn,'app_version':app.config['APP_VERSION']}

    @app.get('/health')
    def health(): return {'status':'ok','app':'ServeSense'}

    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            with connect() as c: r=c.execute("SELECT * FROM users WHERE username=?",(request.form.get('username','').strip(),)).fetchone()
            if r and r['active'] and check_password_hash(r['password_hash'],request.form.get('password','')):
                login_user(User(r['username'],r['role'])); return redirect(url_for('dashboard'))
            flash('Invalid username or password.','error')
        return render_template('login.html')

    @app.post('/logout')
    @login_required
    def logout(): logout_user(); return redirect(url_for('login'))

    @app.get('/')
    @login_required
    def dashboard():
        with connect() as c:
            stats=dict(staff=c.execute("SELECT COUNT(*) FROM staff WHERE active=1").fetchone()[0],sales=c.execute("SELECT COALESCE(SUM(sales),0) FROM sales WHERE shift_date>=date('now','-7 day')").fetchone()[0],hours=c.execute("SELECT COALESCE(SUM(hours),0) FROM sales WHERE shift_date>=date('now','-7 day')").fetchone()[0],shifts=c.execute("SELECT COUNT(*) FROM shifts WHERE shift_date>=date('now')").fetchone()[0])
            stats['splh']=stats['sales']/stats['hours'] if stats['hours'] else 0
            recent=c.execute("SELECT * FROM shifts ORDER BY shift_date DESC,id DESC LIMIT 6").fetchall()
            leaders=c.execute("SELECT s.name,s.role,AVG(sa.sales) avg_sales,AVG(CASE WHEN sa.hours>0 THEN sa.sales/sa.hours END) sph FROM sales sa JOIN staff s ON s.id=sa.staff_id GROUP BY s.id ORDER BY sph DESC LIMIT 5").fetchall()
        return render_template('dashboard.html',stats=stats,recent=recent,leaders=leaders)

    @app.route('/staff',methods=['GET','POST'])
    @login_required
    def staff():
        with connect() as c:
            if request.method=='POST':
                try:
                    c.execute('''INSERT INTO staff(name,department,role,email,phone,hire_date,birthday,pay_rate,pay_type,max_shifts_week,certifications,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                      request.form['name'].strip(),request.form['department'],request.form['role'].strip(),request.form.get('email','').strip(),request.form.get('phone','').strip(),request.form.get('hire_date',''),request.form.get('birthday',''),float(request.form.get('pay_rate') or 0),request.form.get('pay_type','hourly'),int(request.form.get('max_shifts_week') or 5),request.form.get('certifications','').strip(),request.form.get('notes','').strip(),now()))
                    flash('👤 Staff member added.','success')
                except (sqlite3.Error,ValueError) as e: flash(f'Could not add staff: {e}','error')
                return redirect(url_for('staff'))
            rows=c.execute("SELECT * FROM staff ORDER BY active DESC,department,role,name").fetchall()
        return render_template('staff.html',staff=rows)

    @app.post('/staff/<int:sid>/toggle')
    @login_required
    def staff_toggle(sid):
        with connect() as c: c.execute("UPDATE staff SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(sid,))
        return redirect(url_for('staff'))

    @app.route('/sales',methods=['GET','POST'])
    @login_required
    def sales():
        with connect() as c:
            if request.method=='POST':
                try:
                    c.execute("INSERT INTO sales(staff_id,shift_date,meal,sales,hours,covers,tips,late_minutes,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(int(request.form['staff_id']),request.form['shift_date'],request.form['meal'],float(request.form.get('sales') or 0),float(request.form.get('hours') or 0),int(request.form.get('covers') or 0),float(request.form.get('tips') or 0),int(request.form.get('late_minutes') or 0),request.form.get('notes',''),now()))
                    flash('💵 Sales entry saved.','success')
                except (ValueError,sqlite3.Error) as e: flash(f'Could not save sales: {e}','error')
                return redirect(url_for('sales'))
            people=c.execute("SELECT id,name,role FROM staff WHERE active=1 ORDER BY name").fetchall(); rows=c.execute("SELECT sa.*,s.name,s.role FROM sales sa JOIN staff s ON s.id=sa.staff_id ORDER BY shift_date DESC,sa.id DESC LIMIT 100").fetchall()
        return render_template('sales.html',people=people,rows=rows,today=date.today().isoformat())

    @app.route('/availability',methods=['GET','POST'])
    @login_required
    def availability():
        with connect() as c:
            if request.method=='POST':
                try:
                    sid=int(request.form.get('staff_id','0')); shift_date=request.form.get('shift_date',''); meal=request.form.get('meal',''); status=request.form.get('status','')
                    if not c.execute('SELECT 1 FROM staff WHERE id=?',(sid,)).fetchone(): raise ValueError('Select a valid employee')
                    if not shift_date or meal not in ('Lunch','Dinner','Brunch','Open') or status not in ('available','preferred','unavailable','pto'): raise ValueError('Complete all availability fields')
                    c.execute('''INSERT INTO availability(staff_id,shift_date,meal,status,note) VALUES(?,?,?,?,?) ON CONFLICT(staff_id,shift_date,meal) DO UPDATE SET status=excluded.status,note=excluded.note''',(sid,shift_date,meal,status,request.form.get('note','').strip()))
                    flash('📆 Availability saved.','success')
                except (ValueError,sqlite3.Error) as e: flash(f'Availability was not saved: {e}','error')
                return redirect(url_for('availability'))
            people=c.execute("SELECT id,name,role FROM staff WHERE active=1 ORDER BY name").fetchall(); rows=c.execute("SELECT a.*,s.name,s.role FROM availability a JOIN staff s ON s.id=a.staff_id ORDER BY shift_date DESC,name LIMIT 150").fetchall()
        return render_template('availability.html',people=people,rows=rows,today=date.today().isoformat())

    @app.post('/availability/<int:aid>/delete')
    @login_required
    def availability_delete(aid):
        with connect() as c: c.execute('DELETE FROM availability WHERE id=?',(aid,))
        return redirect(url_for('availability'))

    @app.route('/predict',methods=['GET','POST'])
    @login_required
    def predict():
        prediction=None
        if request.method=='POST':
            shift_date=request.form['shift_date']; meal=request.form['meal']; counts={r:int(request.form.get(r,0) or 0) for r in ['Manager','Server','Bartender','Host','Busser','Food Runner']}
            with connect() as c:
                candidates=score_people(c,shift_date,meal); selected=[]
                for role,count in counts.items():
                    role_people=[x for x in candidates if (x['department']=='Management' if role=='Manager' else x['role']==role)]
                    selected += role_people[:count]
                prediction={'date':shift_date,'meal':meal,'expected_sales':float(request.form.get('expected_sales') or 0),'selected':selected,'counts':counts}
        return render_template('predict.html',today=date.today().isoformat(),prediction=prediction)

    @app.post('/predict/save')
    @login_required
    def save_prediction():
        shift_date=request.form['shift_date']; meal=request.form['meal']; ids=[int(x) for x in request.form.getlist('staff_id')]
        with connect() as c:
            cur=c.execute("INSERT INTO shifts(shift_date,meal,expected_sales,notes,status,created_at) VALUES(?,?,?,?,?,?)",(shift_date,meal,float(request.form.get('expected_sales') or 0),request.form.get('notes',''),'draft',now())); sh=cur.lastrowid
            ranked={x['id']:x for x in score_people(c,shift_date,meal)}
            for order,sid in enumerate(ids):
                s=c.execute('SELECT role,department FROM staff WHERE id=?',(sid,)).fetchone(); pos='Manager' if s['department']=='Management' else s['role']; item=ranked.get(sid,{'score':0,'reason':'Manually selected'})
                c.execute("INSERT OR IGNORE INTO assignments(shift_id,staff_id,position,score,reason,sort_order) VALUES(?,?,?,?,?,?)",(sh,sid,pos,item['score'],item['reason'],order))
        return redirect(url_for('schedule_builder',shift_id=sh))

    @app.route('/schedule/new',methods=['GET','POST'])
    @login_required
    def schedule_new():
        with connect() as c:
            if request.method=='POST':
                cur=c.execute("INSERT INTO shifts(shift_date,meal,expected_sales,notes,status,created_at) VALUES(?,?,?,?,?,?)",(request.form['shift_date'],request.form['meal'],float(request.form.get('expected_sales') or 0),request.form.get('notes',''),'draft',now()))
                return redirect(url_for('schedule_builder',shift_id=cur.lastrowid))
        return render_template('schedule_new.html',today=date.today().isoformat())

    @app.get('/schedule/<int:shift_id>')
    @login_required
    def schedule_builder(shift_id):
        with connect() as c:
            shift = c.execute(
                'SELECT * FROM shifts WHERE id=?',
                (shift_id,)
            ).fetchone()

            if not shift:
                abort(404)

            staff = c.execute(
                """SELECT *
                   FROM staff
                   WHERE active=1
                   ORDER BY department,role,name"""
            ).fetchall()

            assigned = c.execute(
                """SELECT
                       a.*,
                       s.name,
                       s.role,
                       s.department,
                       s.pay_rate,
                       s.pay_type
                   FROM assignments a
                   JOIN staff s
                     ON s.id=a.staff_id
                   WHERE shift_id=?
                   ORDER BY sort_order""",
                (shift_id,)
            ).fetchall()

            labor = labor_guardrail(
                c,
                shift_id
            )

        return render_template(
            'schedule_builder.html',
            shift=shift,
            staff=staff,
            assigned=assigned,
            labor=labor
        )

    @app.post('/api/schedule/<int:shift_id>/assign')
    @login_required
    def api_assign(shift_id):
        data = request.get_json(force=True)
        sid = int(data['staff_id'])
        position = data.get('position', 'Server')
        order = int(data.get('sort_order', 0))
        start_time = data.get('start_time', '')
        end_time = data.get('end_time', '')

        with connect() as c:
            conflicts = schedule_conflicts(
                c,
                shift_id,
                sid,
                start_time,
                end_time
            )

            if conflicts:
                return jsonify(
                    ok=False,
                    conflicts=conflicts
                ), 409

            c.execute(
                """INSERT INTO assignments(
                       shift_id,
                       staff_id,
                       position,
                       start_time,
                       end_time,
                       sort_order,
                       reason
                   )
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(shift_id,staff_id)
                   DO UPDATE SET
                       position=excluded.position,
                       start_time=excluded.start_time,
                       end_time=excluded.end_time,
                       sort_order=excluded.sort_order""",
                (
                    shift_id,
                    sid,
                    position,
                    start_time,
                    end_time,
                    order,
                    'Manually assigned'
                )
            )

        return {'ok': True}

    @app.post('/api/schedule/<int:shift_id>/remove')
    @login_required
    def api_remove(shift_id):
        sid=int(request.get_json(force=True)['staff_id'])
        with connect() as c: c.execute('DELETE FROM assignments WHERE shift_id=? AND staff_id=?',(shift_id,sid))
        return {'ok':True}

    @app.post('/schedule/<int:shift_id>/publish')
    @login_required
    def publish(shift_id):
        with connect() as c:
            assignments = c.execute(
                """SELECT staff_id,start_time,end_time
                   FROM assignments
                   WHERE shift_id=?""",
                (shift_id,)
            ).fetchall()

            blocked = []

            for assignment in assignments:
                conflicts = schedule_conflicts(
                    c,
                    shift_id,
                    assignment['staff_id'],
                    assignment['start_time'],
                    assignment['end_time']
                )

                if conflicts:
                    person = c.execute(
                        "SELECT name FROM staff WHERE id=?",
                        (assignment['staff_id'],)
                    ).fetchone()

                    name = (
                        person['name']
                        if person
                        else f"Staff #{assignment['staff_id']}"
                    )

                    blocked.extend(
                        f"{name}: {message}"
                        for message in conflicts
                    )

            if blocked:
                flash(
                    "⚠️ Cannot publish schedule: "
                    + " | ".join(blocked),
                    "error"
                )

                return redirect(
                    url_for(
                        'schedule_builder',
                        shift_id=shift_id
                    )
                )

            c.execute(
                """UPDATE shifts
                   SET status='published'
                   WHERE id=?""",
                (shift_id,)
            )

        flash(
            '✅ Schedule published.',
            'success'
        )

        return redirect(
            url_for(
                'schedule_builder',
                shift_id=shift_id
            )
        )

    @app.get('/schedules')
    @login_required
    def schedules():
        with connect() as c: rows=c.execute("SELECT sh.*,COUNT(a.id) staff_count FROM shifts sh LEFT JOIN assignments a ON a.shift_id=sh.id GROUP BY sh.id ORDER BY shift_date DESC,id DESC").fetchall()
        return render_template('schedules.html',rows=rows)

    @app.get('/payroll')
    @login_required
    def payroll():
        with connect() as c:
            rows=c.execute("""SELECT s.id,s.name,s.department,s.role,s.pay_rate,s.pay_type,
              COALESCE(SUM(sa.hours),0) hours,COALESCE(SUM(sa.tips),0) tips,
              CASE WHEN s.pay_type='salary' THEN s.pay_rate/52.0 ELSE COALESCE(SUM(sa.hours),0)*s.pay_rate END gross_pay
              FROM staff s LEFT JOIN sales sa ON sa.staff_id=s.id AND sa.shift_date>=date('now','-7 day')
              WHERE s.active=1 GROUP BY s.id ORDER BY s.department,s.role,s.name""").fetchall()
            totals=c.execute("""SELECT COALESCE(SUM(sa.hours*s.pay_rate),0) hourly_pay,
              COALESCE(SUM(sa.tips),0) tips FROM sales sa JOIN staff s ON s.id=sa.staff_id
              WHERE sa.shift_date>=date('now','-7 day') AND s.pay_type='hourly'""").fetchone()
            salaries=c.execute("SELECT COALESCE(SUM(pay_rate/52.0),0) FROM staff WHERE active=1 AND pay_type='salary'").fetchone()[0]
        return render_template('payroll.html',rows=rows,total_pay=(totals['hourly_pay'] or 0)+(salaries or 0),total_tips=totals['tips'] or 0)

    @app.route('/reservations',methods=['GET','POST'])
    @login_required
    def reservations():
        with connect() as c:
            if request.method=='POST':
                try:
                    c.execute("INSERT INTO reservations(reservation_date,meal,party_name,party_size,notes) VALUES(?,?,?,?,?)",(
                      request.form['reservation_date'],request.form['meal'],request.form['party_name'].strip(),int(request.form['party_size']),request.form.get('notes','').strip()))
                    flash('📖 Reservation added.','success')
                except (ValueError,sqlite3.Error) as e: flash(f'Could not add reservation: {e}','error')
                return redirect(url_for('reservations'))
            rows=c.execute("SELECT * FROM reservations ORDER BY reservation_date DESC,id DESC LIMIT 200").fetchall()
        return render_template('reservations.html',rows=rows,today=date.today().isoformat())

    @app.post('/reservations/<int:rid>/delete')
    @login_required
    def reservation_delete(rid):
        with connect() as c: c.execute('DELETE FROM reservations WHERE id=?',(rid,))
        return redirect(url_for('reservations'))

    @app.get('/reports')
    @login_required
    def reports():
        with connect() as c:
            leaderboard=c.execute("SELECT s.name,s.role,COUNT(sa.id) shifts,AVG(sa.sales) avg_sales,AVG(CASE WHEN sa.hours>0 THEN sa.sales/sa.hours END) sph,SUM(sa.tips) tips,SUM(sa.late_minutes) late FROM sales sa JOIN staff s ON s.id=sa.staff_id GROUP BY s.id ORDER BY sph DESC").fetchall()
            labor=c.execute("SELECT sa.shift_date,SUM(sa.sales) sales,SUM(sa.hours*s.pay_rate) labor,CASE WHEN SUM(sa.sales)>0 THEN SUM(sa.hours*s.pay_rate)/SUM(sa.sales)*100 ELSE 0 END labor_pct FROM sales sa JOIN staff s ON s.id=sa.staff_id GROUP BY sa.shift_date ORDER BY sa.shift_date DESC LIMIT 30").fetchall()
        return render_template('reports.html',leaderboard=leaderboard,labor=labor)

    @app.route('/admins',methods=['GET','POST'])
    @login_required
    def admins():
        if not current_user.is_owner: abort(403)
        with connect() as c:
            if request.method=='POST':
                try: c.execute("INSERT INTO users VALUES(?,?,?,?,?)",(request.form['username'].strip(),generate_password_hash(request.form['password']),request.form.get('role','admin'),1,now())); flash('🔐 Admin added.','success')
                except sqlite3.Error as e: flash(f'Could not add admin: {e}','error')
                return redirect(url_for('admins'))
            rows=c.execute('SELECT username,role,active,created_at FROM users ORDER BY username').fetchall()
        return render_template('admins.html',rows=rows)

    @app.post('/admins/<username>/toggle')
    @login_required
    def admin_toggle(username):
        if not current_user.is_owner or username==current_user.id: abort(403)
        with connect() as c: c.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE username=?",(username,))
        return redirect(url_for('admins'))

    @app.route('/settings',methods=['GET','POST'])
    @login_required
    def settings():
        if not current_user.is_admin: abort(403)
        with connect() as c:
            if request.method=='POST':
                for k in ('restaurant_name','business_hours','default_labor_target','weather_note'):
                    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,request.form.get(k,'')))
                flash('⚙️ Settings saved.','success'); return redirect(url_for('settings'))
            vals={r['key']:r['value'] for r in c.execute('SELECT * FROM settings')}
        return render_template('settings.html',vals=vals)

    @app.post('/demo/seed')
    @login_required
    def demo_seed():
        if not current_user.is_owner: abort(403)
        with connect() as c: seed_demo(c)
        flash('🧪 Demo staff, sales, and availability loaded.','success'); return redirect(url_for('dashboard'))

    @app.get('/export/<kind>.csv')
    @login_required
    def csv_export(kind):
        queries={
          'staff':"SELECT * FROM staff ORDER BY department,role,name",
          'sales':"SELECT sa.*,s.name,s.role FROM sales sa JOIN staff s ON s.id=sa.staff_id ORDER BY shift_date DESC",
          'availability':"SELECT a.*,s.name,s.role FROM availability a JOIN staff s ON s.id=a.staff_id ORDER BY shift_date DESC",
          'schedules':"SELECT sh.shift_date,sh.meal,sh.expected_sales,sh.status,s.name,a.position,a.start_time,a.end_time,a.score,a.reason FROM shifts sh LEFT JOIN assignments a ON a.shift_id=sh.id LEFT JOIN staff s ON s.id=a.staff_id ORDER BY sh.shift_date DESC,a.sort_order",
          'admins':"SELECT username,role,active,created_at FROM users ORDER BY username",
          'reservations':"SELECT * FROM reservations ORDER BY reservation_date DESC",
          'payroll':"SELECT s.name,s.department,s.role,s.pay_rate,s.pay_type,COALESCE(SUM(sa.hours),0) hours,COALESCE(SUM(sa.tips),0) tips FROM staff s LEFT JOIN sales sa ON sa.staff_id=s.id GROUP BY s.id ORDER BY s.department,s.role,s.name"}
        if kind not in queries: abort(404)
        with connect() as c: rows=c.execute(queries[kind]).fetchall(); headers=rows[0].keys() if rows else []
        return export_csv(f'servesense-{kind}-{date.today()}.csv',headers,[tuple(r) for r in rows])

    @app.get('/export/all.zip')
    @login_required
    def all_export():
        mem=io.BytesIO(); kinds=['staff','sales','availability','schedules','admins','reservations']
        with zipfile.ZipFile(mem,'w',zipfile.ZIP_DEFLATED) as z:
            with connect() as c:
                q={'staff':'SELECT * FROM staff','sales':'SELECT * FROM sales','availability':'SELECT * FROM availability','schedules':'SELECT * FROM shifts','admins':'SELECT username,role,active,created_at FROM users','reservations':'SELECT * FROM reservations'}
                for k in kinds:
                    rows=c.execute(q[k]).fetchall(); s=io.StringIO(); w=csv.writer(s); w.writerow(rows[0].keys() if rows else []); w.writerows([tuple(r) for r in rows]); z.writestr(k+'.csv',s.getvalue())
            if DB.exists(): z.write(DB,'servesense.db')
        mem.seek(0); return send_file(mem,as_attachment=True,download_name=f'servesense-backup-{date.today()}.zip',mimetype='application/zip')

    return app
