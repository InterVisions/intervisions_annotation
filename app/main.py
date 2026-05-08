import os
import sqlite3
import hashlib
import secrets
import json
import time
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from io import BytesIO
from PIL import Image as PILImage

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", "/data/intervisions.db")
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "/data/images")
app.config["MAX_OPEN_TASKS"] = 3

# ─── Database ───────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # Seed default admin if not exists
    cur = db.execute("SELECT id FROM users WHERE username = 'admin'")
    if cur.fetchone() is None:
        pw = os.environ.get("ADMIN_PASSWORD", "intervisions2025")
        db.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ("admin", generate_password_hash(pw), "admin", "Admin"),
        )
    db.commit()
    # Seed campaigns and terms
    cur = db.execute("SELECT COUNT(*) as c FROM campaigns")
    if cur.fetchone()["c"] == 0:
        seed_campaigns(db)
    # Seed default settings
    db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
               ("default_target_images", "40"))
    db.commit()
    db.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'annotator')),
    display_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dimension TEXT NOT NULL,
    description TEXT,
    annotation_type TEXT NOT NULL DEFAULT 'single' CHECK(annotation_type IN ('single', 'couple'))
);

CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    term TEXT NOT NULL,
    dimensions TEXT NOT NULL,
    target_images INTEGER DEFAULT 40,
    UNIQUE(campaign_id, term)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id INTEGER NOT NULL REFERENCES terms(id),
    annotator_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress', 'completed')),
    extra_fields TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(term_id, annotator_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_logins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    logged_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    image_url TEXT NOT NULL,
    image_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    image_size_kb INTEGER,
    image_format TEXT,
    licence TEXT DEFAULT 'CC-BY',
    concept_match TEXT DEFAULT 'Yes',
    num_people TEXT DEFAULT '1',
    perceived_gender INTEGER DEFAULT 2,
    perceived_age TEXT,
    perceived_skin_tone INTEGER DEFAULT 0,
    perceived_disability TEXT,
    body_type_notes TEXT,
    perceived_socioeconomic_status TEXT,
    suitability TEXT DEFAULT 'Suitable',
    suitability_reason TEXT,
    intersectional_notes TEXT,
    p2_perceived_gender INTEGER,
    p2_perceived_age TEXT,
    p2_perceived_skin_tone INTEGER,
    p2_perceived_disability TEXT,
    p2_body_type_notes TEXT,
    p2_perceived_socioeconomic_status TEXT,
    couple_relationship_type TEXT,
    couple_interracial TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

def migrate_db():
    """Apply schema migrations to existing production databases safely."""
    db = sqlite3.connect(app.config["DATABASE"])
    campaign_cols = {r[1] for r in db.execute("PRAGMA table_info(campaigns)").fetchall()}
    if "annotation_type" not in campaign_cols:
        db.execute("ALTER TABLE campaigns ADD COLUMN annotation_type TEXT NOT NULL DEFAULT 'single'")
    ann_cols = {r[1] for r in db.execute("PRAGMA table_info(annotations)").fetchall()}
    for col, typ in [
        ("p2_perceived_gender",           "INTEGER"),
        ("p2_perceived_age",              "TEXT"),
        ("p2_perceived_skin_tone",        "INTEGER"),
        ("p2_perceived_disability",       "TEXT"),
        ("p2_body_type_notes",            "TEXT"),
        ("p2_perceived_socioeconomic_status", "TEXT"),
        ("couple_relationship_type",      "TEXT"),
        ("couple_interracial",            "TEXT"),
    ]:
        if col not in ann_cols:
            db.execute(f"ALTER TABLE annotations ADD COLUMN {col} {typ}")
    db.commit()
    db.close()

CAMPAIGNS_DATA = [
    ("C1", "Vocational Training (FP/TVET)", "Productive", "Promoting the local vocational training centre"),
    ("C2", "Sports & Healthy Living", "Reproductive", "Municipal sports centre and healthy living campaign"),
    ("C3", "Public Park / Green Zone", "Reproductive", "Inaugurating a new public park or green area"),
    ("C4", "Municipal Government", "Power", "Government communication and political representation"),
    ("C5", "Community Centre", "Reproductive,Productive", "Community centre seasonal programme"),
    ("C6", "Civic Engagement", "Power,Reproductive", "Civic activities and local activism"),
    ("C7", "Care & Domestic Life", "Reproductive", "Care-related services and domestic life"),
    ("C8", "Cultural Events", "Reproductive", "Local cultural events and arts programme"),
]

def seed_campaigns(db):
    for cid, name, dim, desc in CAMPAIGNS_DATA:
        db.execute("INSERT OR IGNORE INTO campaigns VALUES (?, ?, ?, ?)", (cid, name, dim, desc))
    db.commit()

# ─── Auth ───────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session or session.get("role") != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if "user_id" in session:
        db = get_db()
        return db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    return None

# ─── Routes: Auth ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("annotator_dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["display_name"] = user["display_name"]
            db.execute("INSERT INTO user_logins (user_id) VALUES (?)", (user["id"],))
            db.commit()
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("annotator_dashboard"))
        flash("Invalid username or password", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Routes: Annotator ─────────────────────────────────────────────────────

@app.route("/annotator")
@login_required
def annotator_dashboard():
    db = get_db()
    uid = session["user_id"]
    tasks = db.execute("""
        SELECT t.*, te.term, te.campaign_id, te.target_images, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim,
               (SELECT COUNT(*) FROM annotations WHERE task_id = t.id) as image_count
        FROM tasks t
        JOIN terms te ON t.term_id = te.id
        JOIN campaigns c ON te.campaign_id = c.id
        WHERE t.annotator_id = ?
        ORDER BY t.status ASC, t.created_at DESC
    """, (uid,)).fetchall()
    open_count = sum(1 for t in tasks if t["status"] == "in_progress")
    total_images = sum(t["image_count"] for t in tasks)
    return render_template("annotator_dashboard.html",
        tasks=tasks, open_count=open_count, total_images=total_images,
        max_open=app.config["MAX_OPEN_TASKS"])

@app.route("/annotator/new-task", methods=["GET", "POST"])
@login_required
def new_task():
    db = get_db()
    uid = session["user_id"]
    open_count = db.execute(
        "SELECT COUNT(*) as c FROM tasks WHERE annotator_id = ? AND status = 'in_progress'",
        (uid,)
    ).fetchone()["c"]
    if open_count >= app.config["MAX_OPEN_TASKS"]:
        flash(f"You already have {open_count} open tasks (max {app.config['MAX_OPEN_TASKS']})", "error")
        return redirect(url_for("annotator_dashboard"))

    if request.method == "POST":
        term_id = request.form.get("term_id")
        extra_fields = request.form.getlist("extra_fields")
        existing = db.execute(
            "SELECT id FROM tasks WHERE term_id = ? AND annotator_id = ?", (term_id, uid)
        ).fetchone()
        if existing:
            flash("You are already working on this term", "error")
            return redirect(url_for("new_task"))
        db.execute(
            "INSERT INTO tasks (term_id, annotator_id, extra_fields) VALUES (?, ?, ?)",
            (term_id, uid, json.dumps(extra_fields))
        )
        db.commit()
        task = db.execute(
            "SELECT id FROM tasks WHERE term_id = ? AND annotator_id = ?", (term_id, uid)
        ).fetchone()
        return redirect(url_for("annotate", task_id=task["id"]))

    campaigns = db.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
    terms = db.execute("""
        SELECT te.*,
            COUNT(t.id) as task_count,
            SUM(CASE WHEN t.annotator_id = ? THEN 1 ELSE 0 END) as already_joined,
            GROUP_CONCAT(u.display_name, ', ') as collaborators
        FROM terms te
        LEFT JOIN tasks t ON te.id = t.term_id
        LEFT JOIN users u ON t.annotator_id = u.id
        GROUP BY te.id
        ORDER BY te.campaign_id, te.term
    """, (uid,)).fetchall()
    return render_template("new_task.html", campaigns=campaigns, terms=terms)

@app.route("/annotator/task/<int:task_id>")
@login_required
def annotate(task_id):
    db = get_db()
    task = db.execute("""
        SELECT t.*, te.term, te.campaign_id, te.target_images, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim, c.annotation_type
        FROM tasks t
        JOIN terms te ON t.term_id = te.id
        JOIN campaigns c ON te.campaign_id = c.id
        WHERE t.id = ? AND t.annotator_id = ?
    """, (task_id, session["user_id"])).fetchone()
    if not task:
        flash("Task not found", "error")
        return redirect(url_for("annotator_dashboard"))

    annotations = db.execute(
        "SELECT * FROM annotations WHERE task_id = ? ORDER BY created_at DESC",
        (task_id,)
    ).fetchall()

    # Balance stats
    stats = compute_task_stats(db, task_id)
    extra_fields = json.loads(task["extra_fields"]) if task["extra_fields"] else []

    return render_template("annotate.html",
        task=task, annotations=annotations, stats=stats,
        extra_fields=extra_fields, annotation_count=len(annotations))

@app.route("/api/annotate/<int:task_id>", methods=["POST"])
@login_required
def api_save_annotation(task_id):
    db = get_db()
    task = db.execute(
        "SELECT * FROM tasks WHERE id = ? AND annotator_id = ?",
        (task_id, session["user_id"])
    ).fetchone()
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data = request.get_json()
    image_url = data.get("image_url", "").strip()
    if not image_url:
        return jsonify({"error": "Image URL is required"}), 400

    # Try to download and get image metadata
    img_width, img_height, img_size_kb, img_format, img_path = None, None, None, None, None
    try:
        resp = requests.get(image_url, timeout=10, stream=True,
                           headers={"User-Agent": "InterVisions/1.0"})
        if resp.status_code == 200:
            content = resp.content
            img_size_kb = len(content) // 1024
            img = PILImage.open(BytesIO(content))
            img_width, img_height = img.size
            img_format = img.format or "UNKNOWN"
            # Save locally
            ext = img_format.lower() if img_format else "jpg"
            if ext == "jpeg":
                ext = "jpg"
            fname = f"task{task_id}_{int(time.time())}_{secrets.token_hex(4)}.{ext}"
            img_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            with open(img_path, "wb") as f:
                f.write(content)
            img_path = fname  # Store relative path
    except Exception as e:
        # If download fails, still save annotation with URL only
        pass

    db.execute("""
        INSERT INTO annotations (
            task_id, image_url, image_path, image_width, image_height,
            image_size_kb, image_format, licence, concept_match, num_people,
            perceived_gender, perceived_age, perceived_skin_tone,
            perceived_disability, body_type_notes, perceived_socioeconomic_status,
            suitability, suitability_reason, intersectional_notes,
            p2_perceived_gender, p2_perceived_age, p2_perceived_skin_tone,
            p2_perceived_disability, p2_body_type_notes, p2_perceived_socioeconomic_status,
            couple_relationship_type, couple_interracial
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id, image_url, img_path, img_width, img_height,
        img_size_kb, img_format,
        data.get("licence", "CC-BY"),
        data.get("concept_match", "Yes"),
        data.get("num_people", "1"),
        data.get("perceived_gender", 2),
        data.get("perceived_age"),
        data.get("perceived_skin_tone", 0),
        data.get("perceived_disability"),
        data.get("body_type_notes"),
        data.get("perceived_socioeconomic_status"),
        data.get("suitability", "Suitable"),
        data.get("suitability_reason"),
        data.get("intersectional_notes"),
        data.get("p2_perceived_gender"),
        data.get("p2_perceived_age"),
        data.get("p2_perceived_skin_tone", 0),
        data.get("p2_perceived_disability"),
        data.get("p2_body_type_notes"),
        data.get("p2_perceived_socioeconomic_status"),
        data.get("couple_relationship_type"),
        data.get("couple_interracial"),
    ))
    db.commit()

    ann_count = db.execute(
        "SELECT COUNT(*) as c FROM annotations WHERE task_id = ?", (task_id,)
    ).fetchone()["c"]
    stats = compute_task_stats(db, task_id)

    return jsonify({"ok": True, "annotation_count": ann_count, "stats": stats})

@app.route("/api/task/<int:task_id>/complete", methods=["POST"])
@login_required
def api_complete_task(task_id):
    db = get_db()
    db.execute(
        "UPDATE tasks SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ? AND annotator_id = ?",
        (task_id, session["user_id"])
    )
    db.commit()
    return jsonify({"ok": True})

@app.route("/images/<path:filename>")
@login_required
def serve_image(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/api/preview-image", methods=["POST"])
@login_required
def api_preview_image():
    data = request.get_json()
    image_url = data.get("image_url", "").strip()
    if not image_url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        from urllib.parse import urlparse
        parsed = urlparse(image_url)
        # Use the root domain as referer (strip subdomains like cdn.)
        parts = parsed.netloc.split(".")
        root_domain = ".".join(parts[-2:]) if len(parts) > 2 else parsed.netloc
        referer = f"{parsed.scheme}://www.{root_domain}/"
        resp = requests.get(image_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
            "Connection": "keep-alive",
        })
        if resp.status_code != 200:
            return jsonify({"error": f"HTTP {resp.status_code}"}), 400
        content = resp.content
        img = PILImage.open(BytesIO(content))
        ext = (img.format or "jpeg").lower()
        if ext == "jpeg":
            ext = "jpg"
        fname = f"tmp_{secrets.token_hex(8)}.{ext}"
        with open(os.path.join(app.config["UPLOAD_FOLDER"], fname), "wb") as f:
            f.write(content)
        return jsonify({"url": url_for("serve_image", filename=fname)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Routes: Admin ──────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    return redirect(url_for("admin_progress"))

@app.route("/admin/progress")
@admin_required
def admin_progress():
    db = get_db()
    annotators = db.execute("""
        SELECT u.*,
            (SELECT COUNT(*) FROM tasks WHERE annotator_id = u.id) as total_tasks,
            (SELECT COUNT(*) FROM tasks WHERE annotator_id = u.id AND status = 'completed') as completed_tasks,
            (SELECT COUNT(*) FROM tasks WHERE annotator_id = u.id AND status = 'in_progress') as open_tasks,
            (SELECT COUNT(*) FROM annotations a JOIN tasks t ON a.task_id = t.id WHERE t.annotator_id = u.id) as total_images,
            (SELECT COUNT(*) FROM user_logins WHERE user_id = u.id) as login_count,
            (SELECT MAX(logged_in_at) FROM user_logins WHERE user_id = u.id) as last_login,
            (SELECT MAX(a.created_at) FROM annotations a JOIN tasks t ON a.task_id = t.id WHERE t.annotator_id = u.id) as last_annotation
        FROM users u WHERE u.role = 'annotator'
    """).fetchall()

    # Get tasks for each annotator
    annotator_tasks = {}
    for ann in annotators:
        tasks = db.execute("""
            SELECT t.*, te.term, te.campaign_id,
                   (SELECT COUNT(*) FROM annotations WHERE task_id = t.id) as image_count,
                   te.target_images
            FROM tasks t JOIN terms te ON t.term_id = te.id
            WHERE t.annotator_id = ?
            ORDER BY t.status ASC, t.created_at DESC
        """, (ann["id"],)).fetchall()
        annotator_tasks[ann["id"]] = tasks

    return render_template("admin_progress.html",
        annotators=annotators, annotator_tasks=annotator_tasks, tab="progress")

@app.route("/admin/dataset")
@admin_required
def admin_dataset():
    db = get_db()
    campaigns = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM terms WHERE campaign_id = c.id) as total_terms,
            (SELECT COUNT(*) FROM tasks t JOIN terms te ON t.term_id = te.id
             WHERE te.campaign_id = c.id AND t.status = 'in_progress') as active_terms,
            (SELECT COUNT(*) FROM tasks t JOIN terms te ON t.term_id = te.id
             WHERE te.campaign_id = c.id AND t.status = 'completed') as completed_terms,
            (SELECT COUNT(*) FROM annotations a JOIN tasks t ON a.task_id = t.id
             JOIN terms te ON t.term_id = te.id WHERE te.campaign_id = c.id) as total_images
        FROM campaigns c ORDER BY c.id
    """).fetchall()
    totals = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM annotations) as total_images,
            (SELECT COUNT(*) FROM tasks WHERE status = 'in_progress') as active_terms,
            (SELECT COUNT(*) FROM tasks WHERE status = 'completed') as completed_terms,
            (SELECT COUNT(*) FROM terms) as total_terms
    """).fetchone()
    return render_template("admin_dataset.html",
        campaigns=campaigns, totals=totals, tab="dataset")

@app.route("/admin/balance")
@admin_required
def admin_balance():
    db = get_db()
    # Get terms that have annotations
    active_terms = db.execute("""
        SELECT te.id, te.term, te.campaign_id
        FROM terms te
        WHERE (SELECT COUNT(*) FROM annotations a JOIN tasks t ON a.task_id = t.id WHERE t.term_id = te.id) > 0
        ORDER BY te.campaign_id, te.term
    """).fetchall()
    return render_template("admin_balance.html", active_terms=active_terms, tab="balance")

@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    """Get balance stats, optionally filtered by term."""
    db = get_db()
    term_id = request.args.get("term_id")

    if term_id:
        tasks = db.execute("SELECT id FROM tasks WHERE term_id = ?", (term_id,)).fetchall()
        if not tasks:
            return jsonify({"error": "No task for this term"}), 404
        task_ids = [t["id"] for t in tasks]
        annotations = db.execute(
            "SELECT * FROM annotations WHERE task_id IN ({})".format(",".join("?" * len(task_ids))),
            task_ids
        ).fetchall()
    else:
        annotations = db.execute("SELECT * FROM annotations").fetchall()

    stats = compute_stats_from_annotations(annotations)
    return jsonify(stats)

@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()
        role = request.form.get("role", "annotator")
        if username and password and display_name:
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), role, display_name),
                )
                db.commit()
                flash(f"User '{username}' created", "success")
            except sqlite3.IntegrityError:
                flash(f"Username '{username}' already exists", "error")
        else:
            flash("All fields are required", "error")
    users = db.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return render_template("admin_users.html", users=users, tab="users")

# ─── Admin: Campaigns & Terms ──────────────────────────────────────────────

@app.route("/admin/campaigns", methods=["GET", "POST"])
@admin_required
def admin_campaigns():
    db = get_db()
    action = request.form.get("action") if request.method == "POST" else None

    if action == "add_campaign":
        cid = request.form.get("campaign_id", "").strip()
        name = request.form.get("name", "").strip()
        dimension = request.form.get("dimension", "").strip()
        description = request.form.get("description", "").strip()
        annotation_type = request.form.get("annotation_type", "single")
        if annotation_type not in ("single", "couple"):
            annotation_type = "single"
        if cid and name and dimension:
            try:
                db.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?)",
                           (cid, name, dimension, description, annotation_type))
                db.commit()
                flash(f"Campaign '{cid}' created", "success")
            except sqlite3.IntegrityError:
                flash(f"Campaign ID '{cid}' already exists", "error")
        else:
            flash("Campaign ID, name, and dimension are required", "error")

    elif action == "delete_campaign":
        cid = request.form.get("campaign_id")
        # Check if any terms have tasks with annotations
        has_annotations = db.execute("""
            SELECT COUNT(*) as c FROM annotations a
            JOIN tasks t ON a.task_id = t.id
            JOIN terms te ON t.term_id = te.id
            WHERE te.campaign_id = ?
        """, (cid,)).fetchone()["c"]
        if has_annotations > 0:
            flash(f"Cannot delete campaign '{cid}': it has {has_annotations} annotations. Delete annotations first.", "error")
        else:
            db.execute("DELETE FROM tasks WHERE term_id IN (SELECT id FROM terms WHERE campaign_id = ?)", (cid,))
            db.execute("DELETE FROM terms WHERE campaign_id = ?", (cid,))
            db.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
            db.commit()
            flash(f"Campaign '{cid}' and its terms deleted", "success")

    elif action == "add_term":
        campaign_id = request.form.get("campaign_id")
        term = request.form.get("term", "").strip()
        dimensions = request.form.get("dimensions", "").strip()
        target = request.form.get("target_images", "")
        if not target:
            target = db.execute("SELECT value FROM settings WHERE key = 'default_target_images'").fetchone()
            target = int(target["value"]) if target else 40
        else:
            target = int(target)
        if campaign_id and term and dimensions:
            try:
                db.execute("INSERT INTO terms (campaign_id, term, dimensions, target_images) VALUES (?, ?, ?, ?)",
                           (campaign_id, term, dimensions, target))
                db.commit()
                flash(f"Term '{term}' added to {campaign_id}", "success")
            except sqlite3.IntegrityError:
                flash(f"Term '{term}' already exists in {campaign_id}", "error")
        else:
            flash("Campaign, term, and dimensions are required", "error")

    elif action == "delete_term":
        term_id = request.form.get("term_id")
        has_annotations = db.execute("""
            SELECT COUNT(*) as c FROM annotations a
            JOIN tasks t ON a.task_id = t.id WHERE t.term_id = ?
        """, (term_id,)).fetchone()["c"]
        if has_annotations > 0:
            flash(f"Cannot delete term: it has {has_annotations} annotations", "error")
        else:
            db.execute("DELETE FROM tasks WHERE term_id = ?", (term_id,))
            db.execute("DELETE FROM terms WHERE id = ?", (term_id,))
            db.commit()
            flash("Term deleted", "success")

    elif action == "update_target":
        term_id = request.form.get("term_id")
        target = int(request.form.get("target_images", 40))
        db.execute("UPDATE terms SET target_images = ? WHERE id = ?", (target, term_id))
        db.commit()
        flash("Target updated", "success")

    campaigns = db.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
    terms = db.execute("""
        SELECT te.*,
            (SELECT COUNT(*) FROM tasks WHERE term_id = te.id) as has_task,
            (SELECT COUNT(*) FROM annotations a JOIN tasks t ON a.task_id = t.id WHERE t.term_id = te.id) as ann_count
        FROM terms te ORDER BY te.campaign_id, te.term
    """).fetchall()
    default_target = db.execute("SELECT value FROM settings WHERE key = 'default_target_images'").fetchone()
    default_target = int(default_target["value"]) if default_target else 40

    return render_template("admin_campaigns.html",
        campaigns=campaigns, terms=terms, default_target=default_target, tab="campaigns")

# ─── Admin: Settings ───────────────────────────────────────────────────────

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        default_target = request.form.get("default_target_images", "40")
        try:
            val = int(default_target)
            if val < 1:
                raise ValueError
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                       ("default_target_images", str(val)))
            # Optionally update all terms that still have the old default
            if request.form.get("apply_to_existing"):
                old_val = request.form.get("old_default", "40")
                db.execute("UPDATE terms SET target_images = ? WHERE target_images = ?",
                           (val, int(old_val)))
            db.commit()
            flash(f"Default target set to {val} images per term", "success")
        except (ValueError, TypeError):
            flash("Target must be a positive integer", "error")

    default_target = db.execute("SELECT value FROM settings WHERE key = 'default_target_images'").fetchone()
    default_target = int(default_target["value"]) if default_target else 40
    total_terms = db.execute("SELECT COUNT(*) as c FROM terms").fetchone()["c"]
    total_campaigns = db.execute("SELECT COUNT(*) as c FROM campaigns").fetchone()["c"]

    return render_template("admin_settings.html",
        default_target=default_target, total_terms=total_terms,
        total_campaigns=total_campaigns, tab="settings")

@app.route("/api/admin/backup-config")
@admin_required
def api_backup_config():
    db = get_db()
    users = db.execute(
        "SELECT username, password_hash, role, display_name FROM users WHERE username != 'admin'"
    ).fetchall()
    terms = db.execute(
        "SELECT campaign_id, term, dimensions, target_images FROM terms ORDER BY campaign_id, term"
    ).fetchall()
    settings = db.execute("SELECT key, value FROM settings").fetchall()
    payload = {
        "users": [dict(u) for u in users],
        "terms": [dict(t) for t in terms],
        "settings": [dict(s) for s in settings],
    }
    from flask import Response
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=intervisions_config_backup.json"}
    )

@app.route("/api/admin/restore-config", methods=["POST"])
@admin_required
def api_restore_config():
    f = request.files.get("backup_file")
    if not f:
        flash("No file uploaded", "error")
        return redirect(url_for("admin_settings"))
    try:
        payload = json.load(f)
        db = get_db()
        for u in payload.get("users", []):
            try:
                db.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
                    (u["username"], u["password_hash"], u["role"], u["display_name"])
                )
            except Exception:
                pass
        for t in payload.get("terms", []):
            try:
                db.execute(
                    "INSERT OR IGNORE INTO terms (campaign_id, term, dimensions, target_images) VALUES (?, ?, ?, ?)",
                    (t["campaign_id"], t["term"], t["dimensions"], t["target_images"])
                )
            except Exception:
                pass
        for s in payload.get("settings", []):
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (s["key"], s["value"]))
        db.commit()
        flash("Configuration restored successfully", "success")
    except Exception as e:
        flash(f"Restore failed: {e}", "error")
    return redirect(url_for("admin_settings"))

@app.route("/api/admin/export")
@admin_required
def api_export_csv():
    """Export all annotations as CSV."""
    db = get_db()
    annotations = db.execute("""
        SELECT a.*, te.term, te.campaign_id, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim,
               c.annotation_type as campaign_annotation_type,
               u.display_name as annotator_name
        FROM annotations a
        JOIN tasks t ON a.task_id = t.id
        JOIN terms te ON t.term_id = te.id
        JOIN campaigns c ON te.campaign_id = c.id
        JOIN users u ON t.annotator_id = u.id
        ORDER BY a.created_at
    """).fetchall()

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "annotation_id", "campaign_id", "campaign_name", "annotation_type", "term", "term_dimensions",
        "annotator", "image_url", "image_width", "image_height", "image_size_kb",
        "image_format", "licence", "num_people", "perceived_gender",
        "perceived_age", "perceived_skin_tone", "perceived_disability",
        "body_type_notes", "perceived_socioeconomic_status", "intersectional_notes",
        "p2_perceived_gender", "p2_perceived_age", "p2_perceived_skin_tone",
        "p2_perceived_disability", "p2_body_type_notes", "p2_perceived_socioeconomic_status",
        "created_at"
    ])
    for a in annotations:
        writer.writerow([
            a["id"], a["campaign_id"], a["campaign_name"], a["campaign_annotation_type"], a["term"], a["term_dims"],
            a["annotator_name"], a["image_url"], a["image_width"], a["image_height"],
            a["image_size_kb"], a["image_format"], a["licence"],
            a["num_people"], a["perceived_gender"], a["perceived_age"],
            a["perceived_skin_tone"], a["perceived_disability"],
            a["body_type_notes"], a["perceived_socioeconomic_status"], a["intersectional_notes"],
            a["p2_perceived_gender"], a["p2_perceived_age"], a["p2_perceived_skin_tone"],
            a["p2_perceived_disability"], a["p2_body_type_notes"], a["p2_perceived_socioeconomic_status"],
            a["created_at"]
        ])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=intervisions_annotations.csv"}
    )

# ─── API: Edit annotation ──────────────────────────────────────────────────

@app.route("/api/annotation/<int:ann_id>/update", methods=["POST"])
@login_required
def api_update_annotation(ann_id):
    db = get_db()
    ann = db.execute("""
        SELECT a.id, t.annotator_id FROM annotations a
        JOIN tasks t ON a.task_id = t.id
        WHERE a.id = ?
    """, (ann_id,)).fetchone()
    if not ann:
        return jsonify({"error": "Not found"}), 404
    if session.get("role") != "admin" and ann["annotator_id"] != session["user_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    db.execute("""
        UPDATE annotations SET
            concept_match = ?, num_people = ?, perceived_gender = ?,
            perceived_age = ?, perceived_skin_tone = ?,
            perceived_disability = ?, body_type_notes = ?,
            perceived_socioeconomic_status = ?,
            suitability = ?, suitability_reason = ?, intersectional_notes = ?,
            p2_perceived_gender = ?, p2_perceived_age = ?, p2_perceived_skin_tone = ?,
            p2_perceived_disability = ?, p2_body_type_notes = ?,
            p2_perceived_socioeconomic_status = ?,
            couple_relationship_type = ?, couple_interracial = ?
        WHERE id = ?
    """, (
        data.get("concept_match"),
        data.get("num_people"),
        data.get("perceived_gender"),
        data.get("perceived_age"),
        data.get("perceived_skin_tone"),
        data.get("perceived_disability") or None,
        data.get("body_type_notes") or None,
        data.get("perceived_socioeconomic_status") or None,
        data.get("suitability"),
        data.get("suitability_reason") or None,
        data.get("intersectional_notes") or None,
        data.get("p2_perceived_gender"),
        data.get("p2_perceived_age") or None,
        data.get("p2_perceived_skin_tone"),
        data.get("p2_perceived_disability") or None,
        data.get("p2_body_type_notes") or None,
        data.get("p2_perceived_socioeconomic_status") or None,
        data.get("couple_relationship_type") or None,
        data.get("couple_interracial") or None,
        ann_id,
    ))
    db.commit()
    return jsonify({"ok": True})

# ─── Admin: Dataset Viewer ─────────────────────────────────────────────────

@app.route("/admin/viewer")
@login_required
def admin_viewer():
    db = get_db()

    campaign_id  = request.args.get("campaign_id", "")
    term_id      = request.args.get("term_id", "")
    annotator_id = request.args.get("annotator_id", "")
    concept_match = request.args.get("concept_match", "")
    suitability  = request.args.get("suitability", "")
    page         = max(1, int(request.args.get("page", 1)))
    per_page     = 50

    conditions, params = ["1=1"], []
    if campaign_id:
        conditions.append("te.campaign_id = ?"); params.append(campaign_id)
    if term_id:
        conditions.append("t.term_id = ?"); params.append(int(term_id))
    if annotator_id:
        conditions.append("t.annotator_id = ?"); params.append(int(annotator_id))
    if concept_match:
        conditions.append("a.concept_match = ?"); params.append(concept_match)
    if suitability:
        conditions.append("a.suitability = ?"); params.append(suitability)

    where = " AND ".join(conditions)
    base_sql = f"""
        FROM annotations a
        JOIN tasks t ON a.task_id = t.id
        JOIN terms te ON t.term_id = te.id
        JOIN campaigns c ON te.campaign_id = c.id
        JOIN users u ON t.annotator_id = u.id
        WHERE {where}
    """

    total = db.execute(f"SELECT COUNT(*) as c {base_sql}", params).fetchone()["c"]
    annotations = db.execute(f"""
        SELECT a.id, a.image_path, a.image_url, a.image_width, a.image_height,
               a.image_size_kb, a.image_format, a.licence,
               a.concept_match, a.num_people, a.perceived_gender, a.perceived_age,
               a.perceived_skin_tone, a.perceived_disability, a.body_type_notes,
               a.perceived_socioeconomic_status, a.suitability, a.suitability_reason,
               a.intersectional_notes, a.created_at,
               a.p2_perceived_gender, a.p2_perceived_age, a.p2_perceived_skin_tone,
               a.p2_perceived_disability, a.p2_body_type_notes, a.p2_perceived_socioeconomic_status,
               a.couple_relationship_type, a.couple_interracial,
               te.term, te.campaign_id, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim,
               c.annotation_type,
               u.display_name as annotator_name, u.id as annotator_user_id
        {base_sql}
        ORDER BY a.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)

    campaigns  = db.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
    terms      = db.execute("SELECT id, term, campaign_id FROM terms ORDER BY campaign_id, term").fetchall()
    annotators = db.execute(
        "SELECT id, display_name FROM users WHERE role = 'annotator' ORDER BY display_name"
    ).fetchall()

    return render_template("admin_viewer.html",
        annotations=annotations,
        total=total, page=page, per_page=per_page, total_pages=total_pages,
        campaigns=campaigns, terms=terms, annotators=annotators,
        f_campaign_id=campaign_id, f_term_id=term_id, f_annotator_id=annotator_id,
        f_concept_match=concept_match, f_suitability=suitability,
        tab="viewer")

# ─── Helpers ────────────────────────────────────────────────────────────────

GENDER_LABELS = [
    "More maleness", "Non-binary", "More femaleness"
]

def compute_task_stats(db, task_id):
    annotations = db.execute(
        "SELECT * FROM annotations WHERE task_id = ?", (task_id,)
    ).fetchall()
    return compute_stats_from_annotations(annotations)

def compute_stats_from_annotations(annotations):
    total = len(annotations)
    if total == 0:
        return {"total": 0, "gender": {}, "skin_tone": [0]*6, "age": {},
                "gender_labels": GENDER_LABELS, "is_couple": False}

    age_labels = ["Child (0-12)", "Adolescent (13-17)", "Young adult (18-30)",
                  "Middle-aged (31-60)", "Older adult (60+)", "Cannot determine"]

    # Gender distribution (P1)
    gender_counts = [0] * 3
    gender_na = 0
    for a in annotations:
        g = a["perceived_gender"]
        if g is not None and 0 <= g <= 2:
            gender_counts[g] += 1
        else:
            gender_na += 1

    # Skin tone distribution (P1)
    skin_counts = [0] * 6
    for a in annotations:
        st = a["perceived_skin_tone"]
        if st and 1 <= st <= 6:
            skin_counts[st - 1] += 1

    # Age distribution (P1)
    age_counts = {l: 0 for l in age_labels}
    for a in annotations:
        age = a["perceived_age"]
        if age in age_counts:
            age_counts[age] += 1

    mst_spread = sum(1 for c in skin_counts if c > 0)

    result = {
        "total": total,
        "gender": {"counts": gender_counts, "labels": GENDER_LABELS, "na": gender_na},
        "skin_tone": skin_counts,
        "mst_spread": mst_spread,
        "age": age_counts,
        "is_couple": False,
    }

    # Couple pair matrices — only when p2 data is present
    couple_anns = [a for a in annotations if a["p2_perceived_gender"] is not None]
    if couple_anns:
        gender_pairs = [[0] * 3 for _ in range(3)]
        for a in couple_anns:
            g1, g2 = a["perceived_gender"], a["p2_perceived_gender"]
            if g1 is not None and 0 <= g1 <= 2 and g2 is not None and 0 <= g2 <= 2:
                gender_pairs[g1][g2] += 1

        skin_pairs = [[0] * 6 for _ in range(6)]
        for a in couple_anns:
            s1, s2 = a["perceived_skin_tone"], a["p2_perceived_skin_tone"]
            if s1 and 1 <= s1 <= 6 and s2 and 1 <= s2 <= 6:
                skin_pairs[s1 - 1][s2 - 1] += 1

        p2_gender_counts = [0] * 3
        for a in couple_anns:
            g = a["p2_perceived_gender"]
            if g is not None and 0 <= g <= 2:
                p2_gender_counts[g] += 1

        p2_age_counts = {l: 0 for l in age_labels}
        for a in couple_anns:
            age = a["p2_perceived_age"]
            if age in p2_age_counts:
                p2_age_counts[age] += 1

        result["is_couple"] = True
        result["couple_total"] = len(couple_anns)
        result["gender_pairs"] = gender_pairs
        result["skin_pairs"] = skin_pairs
        result["p2_gender"] = {"counts": p2_gender_counts, "labels": GENDER_LABELS}
        result["p2_age"] = p2_age_counts

    return result

# ─── Template context ──────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "gender_labels": GENDER_LABELS,
        "mst_colors": [
            "#664e41", "#886951", "#a48367", "#af9478", "#bda389", "#c6b49d"
        ],
        "gender_colors": ["#4A8BC2", "#A0A0A0", "#C77DBA"],
        "gender_short": ["M.Male", "NB", "M.Female"],
        "dim_colors": {
            "Productive": "#4472C4",
            "Reproductive": "#70AD47",
            "Power": "#BF8F00",
        },
    }

# ─── Main ──────────────────────────────────────────────────────────────────

# Always initialize DB on import (needed for gunicorn which doesn't run __main__)
init_db()
migrate_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
