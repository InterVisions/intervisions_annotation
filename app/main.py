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
    description TEXT
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
    UNIQUE(term_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    suitability TEXT DEFAULT 'Suitable',
    suitability_reason TEXT,
    intersectional_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

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

TERMS_DATA = [
    ("C1", "a mechanic", "Productive"),
    ("C1", "a nurse", "Productive"),
    ("C1", "an electrician", "Productive"),
    ("C1", "a hairdresser", "Productive"),
    ("C1", "a cook / chef", "Productive"),
    ("C1", "an IT technician", "Productive"),
    ("C1", "students in a workshop", "Productive"),
    ("C1", "a welder", "Productive"),
    ("C2", "a person running", "Reproductive"),
    ("C2", "a person doing yoga", "Reproductive"),
    ("C2", "a person swimming", "Reproductive"),
    ("C2", "a person lifting weights", "Reproductive"),
    ("C2", "a family cycling", "Reproductive"),
    ("C2", "an elderly person exercising", "Reproductive"),
    ("C3", "people in a park", "Reproductive"),
    ("C3", "children playing outdoors", "Reproductive"),
    ("C3", "a person walking a dog", "Reproductive"),
    ("C3", "elderly people on a bench", "Reproductive"),
    ("C3", "a community garden", "Reproductive"),
    ("C4", "a mayor", "Power"),
    ("C4", "a city council member", "Power"),
    ("C4", "a politician giving a speech", "Power"),
    ("C4", "a public servant", "Power"),
    ("C4", "a police officer", "Power"),
    ("C5", "a reading club", "Reproductive"),
    ("C5", "a language exchange group", "Reproductive"),
    ("C5", "a computing class", "Productive"),
    ("C5", "a crafts workshop", "Reproductive"),
    ("C5", "a yoga class", "Reproductive"),
    ("C6", "a neighbourhood assembly", "Power"),
    ("C6", "people in a community garden", "Reproductive"),
    ("C6", "a protest or demonstration", "Power"),
    ("C6", "volunteers serving food", "Reproductive"),
    ("C7", "caring for an elderly relative", "Reproductive"),
    ("C7", "a person doing housework", "Reproductive"),
    ("C7", "a person grocery shopping", "Reproductive"),
    ("C7", "a parent with a child", "Reproductive"),
    ("C7", "a home care worker", "Reproductive,Productive"),
    ("C8", "a theatre performance", "Reproductive"),
    ("C8", "a person visiting a museum", "Reproductive"),
    ("C8", "a local music concert", "Reproductive"),
    ("C8", "an art exhibition", "Reproductive"),
]

def seed_campaigns(db):
    for cid, name, dim, desc in CAMPAIGNS_DATA:
        db.execute("INSERT OR IGNORE INTO campaigns VALUES (?, ?, ?, ?)", (cid, name, dim, desc))
    for cid, term, dims in TERMS_DATA:
        db.execute("INSERT OR IGNORE INTO terms (campaign_id, term, dimensions) VALUES (?, ?, ?)", (cid, term, dims))
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
        # Check term not already taken
        existing = db.execute("SELECT id FROM tasks WHERE term_id = ?", (term_id,)).fetchone()
        if existing:
            flash("This term is already reserved by another annotator", "error")
            return redirect(url_for("new_task"))
        db.execute(
            "INSERT INTO tasks (term_id, annotator_id, extra_fields) VALUES (?, ?, ?)",
            (term_id, uid, json.dumps(extra_fields))
        )
        db.commit()
        task = db.execute("SELECT id FROM tasks WHERE term_id = ?", (term_id,)).fetchone()
        return redirect(url_for("annotate", task_id=task["id"]))

    # Get all campaigns with terms, marking taken ones
    campaigns = db.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
    terms = db.execute("""
        SELECT te.*, t.id as task_id, t.annotator_id, u.display_name as taken_by
        FROM terms te
        LEFT JOIN tasks t ON te.id = t.term_id
        LEFT JOIN users u ON t.annotator_id = u.id
        ORDER BY te.campaign_id, te.term
    """).fetchall()
    return render_template("new_task.html", campaigns=campaigns, terms=terms)

@app.route("/annotator/task/<int:task_id>")
@login_required
def annotate(task_id):
    db = get_db()
    task = db.execute("""
        SELECT t.*, te.term, te.campaign_id, te.target_images, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim
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
            perceived_disability, body_type_notes, suitability,
            suitability_reason, intersectional_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data.get("suitability", "Suitable"),
        data.get("suitability_reason"),
        data.get("intersectional_notes"),
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
            (SELECT COUNT(*) FROM annotations a JOIN tasks t ON a.task_id = t.id WHERE t.annotator_id = u.id) as total_images
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
        SELECT te.id, te.term, te.campaign_id, t.id as task_id
        FROM terms te JOIN tasks t ON te.id = t.term_id
        WHERE (SELECT COUNT(*) FROM annotations WHERE task_id = t.id) > 0
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
        task = db.execute("SELECT id FROM tasks WHERE term_id = ?", (term_id,)).fetchone()
        if not task:
            return jsonify({"error": "No task for this term"}), 404
        annotations = db.execute(
            "SELECT * FROM annotations WHERE task_id = ?", (task["id"],)
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
        if cid and name and dimension:
            try:
                db.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?)",
                           (cid, name, dimension, description))
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

@app.route("/api/admin/export")
@admin_required
def api_export_csv():
    """Export all annotations as CSV."""
    db = get_db()
    annotations = db.execute("""
        SELECT a.*, te.term, te.campaign_id, te.dimensions as term_dims,
               c.name as campaign_name, c.dimension as campaign_dim,
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
        "annotation_id", "campaign_id", "campaign_name", "term", "term_dimensions",
        "annotator", "image_url", "image_width", "image_height", "image_size_kb",
        "image_format", "licence", "concept_match", "num_people", "perceived_gender",
        "perceived_age", "perceived_skin_tone", "perceived_disability",
        "body_type_notes", "suitability", "suitability_reason",
        "intersectional_notes", "created_at"
    ])
    for a in annotations:
        writer.writerow([
            a["id"], a["campaign_id"], a["campaign_name"], a["term"], a["term_dims"],
            a["annotator_name"], a["image_url"], a["image_width"], a["image_height"],
            a["image_size_kb"], a["image_format"], a["licence"], a["concept_match"],
            a["num_people"], a["perceived_gender"], a["perceived_age"],
            a["perceived_skin_tone"], a["perceived_disability"],
            a["body_type_notes"], a["suitability"], a["suitability_reason"],
            a["intersectional_notes"], a["created_at"]
        ])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=intervisions_annotations.csv"}
    )

# ─── Helpers ────────────────────────────────────────────────────────────────

GENDER_LABELS = [
    "Predominantly feminine", "Mostly feminine",
    "Non-binary / ambiguous", "Mostly masculine", "Predominantly masculine"
]

def compute_task_stats(db, task_id):
    annotations = db.execute(
        "SELECT * FROM annotations WHERE task_id = ?", (task_id,)
    ).fetchall()
    return compute_stats_from_annotations(annotations)

def compute_stats_from_annotations(annotations):
    total = len(annotations)
    if total == 0:
        return {"total": 0, "gender": {}, "skin_tone": [0]*10, "age": {},
                "gender_labels": GENDER_LABELS}

    # Gender distribution (5-step)
    gender_counts = [0] * 5
    gender_na = 0
    for a in annotations:
        g = a["perceived_gender"]
        if g is not None and 0 <= g <= 4:
            gender_counts[g] += 1
        else:
            gender_na += 1

    # Skin tone distribution (MST 1-10)
    skin_counts = [0] * 10
    for a in annotations:
        st = a["perceived_skin_tone"]
        if st and 1 <= st <= 10:
            skin_counts[st - 1] += 1

    # Age distribution
    age_labels = ["Child (0-12)", "Adolescent (13-17)", "Young adult (18-30)",
                  "Middle-aged (31-60)", "Older adult (60+)", "Cannot determine"]
    age_counts = {l: 0 for l in age_labels}
    for a in annotations:
        age = a["perceived_age"]
        if age in age_counts:
            age_counts[age] += 1

    # MST spread
    mst_spread = sum(1 for c in skin_counts if c > 0)

    return {
        "total": total,
        "gender": {
            "counts": gender_counts,
            "labels": GENDER_LABELS,
            "na": gender_na,
        },
        "skin_tone": skin_counts,
        "mst_spread": mst_spread,
        "age": age_counts,
    }

# ─── Template context ──────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "gender_labels": GENDER_LABELS,
        "mst_colors": [
            "#f6ede4", "#f3e7db", "#f7d7b4", "#efc68e", "#d99f6e",
            "#b07a4b", "#8c5a2e", "#6a3d1a", "#4a2912", "#2d1a0b"
        ],
        "gender_colors": ["#C77DBA", "#CFA0C8", "#A0A0A0", "#7BA3C9", "#4A8BC2"],
        "gender_short": ["Fem", "M.Fem", "NB", "M.Masc", "Masc"],
        "dim_colors": {
            "Productive": "#4472C4",
            "Reproductive": "#70AD47",
            "Power": "#BF8F00",
        },
    }

# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
