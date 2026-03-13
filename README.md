# InterVisions Annotation Platform

Annotation tool for the **InterVisions** project (101214711 — CERV-2024-CHAR-LITI).  
Designed to collect and annotate a balanced fairness evaluation dataset across Nancy Fraser's three dimensions of social life: **productive**, **reproductive**, and **power**.

## Features

### Annotator interface
- **Dashboard** with open/completed tasks and summary stats
- **Task picker** with term reservation (no two annotators on the same term), extra field selection, and Fraser dimension badges
- **Annotation view**: paste an image URL → the server downloads it, extracts metadata (resolution, size, format) → annotate with:
  - Licence (default: CC-BY)
  - Concept match (default: Yes)
  - Suitability (default: Suitable)
  - 5-step gender presentation scale (Predominantly feminine → Predominantly masculine + Cannot determine)
  - Monk Skin Tone 10-level visual selector with reference popup
  - Perceived age (6 categories)
  - Optional: perceived disability, body type notes
  - Free-text intersectional notes
- Real-time balance indicators (gender distribution, MST spread) while annotating
- Max 3 concurrent open tasks per annotator

### Admin interface
- **Annotator Progress**: who is working on what, how many images, task status
- **Dataset Overview**: per-campaign table with term counts, active/completed/remaining
- **Balancing**: interactive charts (gender, skin tone, age) filterable by **global** or **individual term** — with automatic imbalance warnings
- **Campaigns & Terms**: add/remove campaigns and terms, edit target images per term
- **Settings**: configure default minimum images per term, apply to existing terms
- **User Management**: create annotator and admin accounts
- **CSV Export**: download all annotations as a CSV file

## Tech stack

- **Backend**: Python / Flask / SQLite / Gunicorn / Pillow
- **Frontend**: Jinja2 templates + vanilla JS (no heavy frameworks)
- **Deployment**: Gunicorn + systemd on any Linux server (e.g. AWS EC2)

## Quick start (local development)

```bash
# 1. Clone or extract the project
git clone https://github.com/InterVisions/intervisions_annotation
cd intervisions_annotation

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create data directories
mkdir -p /tmp/intervisions-data/images

# 5. Run
DATABASE_PATH=/tmp/intervisions-data/intervisions.db \
UPLOAD_FOLDER=/tmp/intervisions-data/images \
python -m app.main

# 6. Open http://localhost:5000
#    Login: admin / intervisions2025
```

## Deploy in server

### Connect and install dependencies

```bash
# Ubuntu 22.04
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

### Set up the project

```bash

# On the cloud instance
cd ~
git clone https://github.com/InterVisions/intervisions_annotation
cd intervisions_annotation

# Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create data directories
sudo mkdir -p /data/intervisions/images
sudo chown -R <user:user> /data/intervisions
```

### Configure the systemd service

```bash
# Edit the service file to set your SECRET_KEY
vi intervisions.service
# Change SECRET_KEY to a random string (generate one with: python3 -c "import secrets; print(secrets.token_hex(32))")
# Change ADMIN_PASSWORD if desired

# Install the service
sudo cp intervisions.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable intervisions
sudo systemctl start intervisions

# Check it's running
sudo systemctl status intervisions

# View logs
sudo journalctl -u intervisions -f
```

### 5. Access

Open `http://YOUR_CLOUD_PUBLIC_IP` in your browser.

- Login as **admin** with the password you set in the service file
- Go to **Users** tab to create annotator accounts
- Annotators can then log in and start working

### 6. Common operations

```bash
# Restart after code changes
sudo systemctl restart intervisions

# Stop the service
sudo systemctl stop intervisions

# View recent logs
sudo journalctl -u intervisions --since "1 hour ago"
```

### Backup

```bash
# Backup the database and images
cp /data/intervisions/intervisions.db ~/backup-$(date +%Y%m%d).db

# Or download the CSV export from the admin interface
```

## Project structure

```
intervisions/
├── app/
│   ├── __init__.py
│   ├── main.py              # Flask app: routes, models, API
│   ├── static/
│   │   └── css/
│   │       └── style.css
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── annotator_dashboard.html
│       ├── new_task.html
│       ├── annotate.html
│       ├── admin_progress.html
│       ├── admin_dataset.html
│       ├── admin_balance.html
│       ├── admin_campaigns.html
│       ├── admin_settings.html
│       └── admin_users.html
├── intervisions.service     # systemd service file for deployment
├── requirements.txt
└── README.md
```

## Database schema

- **users**: id, username, password_hash, role, display_name
- **campaigns**: id (C1-C8), name, dimension, description
- **terms**: id, campaign_id, term, dimensions, target_images
- **tasks**: id, term_id, annotator_id, status, extra_fields
- **annotations**: id, task_id, image_url, image_path, image metadata, all annotation fields
- **settings**: key-value store for platform configuration

The database is pre-seeded with 8 campaigns and 42 terms from the InterVisions use-case scenario document.

## Default credentials

- **Admin**: username `admin`, password `intervisions2025` (change in the service file before deploying)

