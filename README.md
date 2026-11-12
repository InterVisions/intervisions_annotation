# InterVisions Annotation Platform

Annotation tool for the **InterVisions** project (101214711 — CERV-2024-CHAR-LITI).  
Designed to collect and annotate a balanced fairness evaluation dataset across Nancy Fraser's three dimensions of social life: **productive**, **reproductive**, and **power**.

## Features

### Annotator interface
- **Dashboard** with open/completed tasks and summary stats
- **Task picker**: several annotators can work on the same term (collaborators are shown), extra field selection, and Fraser dimension badges
- **Annotation view**: paste an image URL → the server downloads it, extracts metadata (resolution, size, format) → annotate with:
  - Licence (CC or Others/Unknown; default: CC)
  - Concept match (default: Yes)
  - Suitability (default: Suitable)
  - 3-step perceived gender presentation scale (More maleness / Androgynous or unclear / More femaleness) + Cannot determine (see [Gender coding](#gender-coding))
  - Perceived skin tone on the 6-type Fitzpatrick scale (see [Skin tone coding](#skin-tone-coding)), visual selector with reference popup
  - Perceived age (6 categories)
  - Optional: perceived disability, body type notes, socio-economic status
  - Free-text intersectional notes
- Real-time balance indicators (gender distribution, skin tone spread) while annotating
- **Couple annotations**: in campaigns of type *couple*, gender, age and skin tone are annotated for two people per image
- **Dataset** viewer and **Balancing** charts (annotators can edit their own annotations)
- Max 15 concurrent open tasks per annotator

### Admin interface
- **Annotator Progress**: who is working on what, how many images, task status; reopen completed tasks
- **Dataset Overview**: per-campaign table with term counts, active/completed/remaining
- **Balancing**: interactive charts (gender, skin tone, age) filterable by **global** or **individual term** — with automatic imbalance warnings
- **Viewer**: browse all annotations with filters; view, edit or delete each one
- **Campaigns & Terms**: add/remove campaigns (single or couple annotation type) and terms, edit target images per term
- **Settings**: configure default minimum images per term, apply to existing terms; backup/restore of users, terms and settings as JSON
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

### (Optional) Port forwarding (browser can access through port 80)
```
sudo sh -c 'echo "iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080" >> /etc/rc.local'
sudo chmod +x /etc/rc.local
```

### Access

Open `http://YOUR_CLOUD_PUBLIC_IP` in your browser.

- Login as **admin** with the password you set in the service file
- Go to **Users** tab to create annotator accounts
- Annotators can then log in and start working

### Common operations

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
intervisions_annotation/
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
│       ├── admin_users.html
│       ├── admin_viewer.html
│       ├── admin_xa.html        # cross-annotation: import sample, progress, export
│       └── annotate_xa.html     # blind re-annotation view
├── scripts/
│   ├── xa_sample.py             # stratified sample for the cross-annotation study
│   ├── xa_analysis.py           # agreement metrics, CIs, confusion matrices
│   └── requirements-analysis.txt
├── intervisions.service     # systemd service file for deployment
├── requirements.txt
├── eu-funded.png
└── README.md
```

## Database schema

- **users**: id, username, password_hash, role, display_name
- **campaigns**: id, name, dimension, description, annotation_type (single / couple)
- **terms**: id, campaign_id, term, dimensions, target_images
- **tasks**: id, term_id, annotator_id, status, extra_fields
- **annotations**: id, task_id, image_url, image_path, image metadata, all annotation fields (`p2_*` fields for the second person in couple annotations)
- **settings**: key-value store for platform configuration
- **user_logins**: login history (user_id, timestamp)
- **xa_units**: cross-annotation sample, one row per person (annotation_id = image, person_idx, prompt, original annotator, re-annotator, phase inter/intra/test, stratum, pi, weight, display position)
- **xa_annotations**: blind re-annotations, one row per unit (gender, age, skin tone). The `annotations` table is never modified by the study.

The database is pre-seeded with 8 campaigns (C1–C8) from the InterVisions use-case scenario document; terms are added from the admin Campaigns page.

## Gender coding

`perceived_gender` and `p2_perceived_gender` record perceived gender *presentation* — what the image shows —
not anyone's gender identity:

| Value | Meaning |
|---|---|
| 0 | More maleness |
| 1 | Androgynous / unclear — the person's look is androgynous or ambiguous |
| 2 | More femaleness |
| -1 | Cannot determine — the image is too poor to judge |

Value 1 is deliberately not labelled "Non-binary": being non-binary is an identity, and nobody can see it in a
photo. Earlier versions of the app showed value 1 as "Non-binary"; the stored values are the same, so older
data and CSV exports need no conversion.

## Skin tone coding

`perceived_skin_tone` and `p2_perceived_skin_tone` use the Fitzpatrick scale:

| Value | Meaning |
|---|---|
| 1 | Fitzpatrick type I — lightest |
| 2–5 | types II–V, progressively darker |
| 6 | Fitzpatrick type VI — darkest |
| 0 | Cannot determine |

**Recoding note.** Earlier versions of the app showed the swatches in reverse order (1 = darkest, 6 = lightest).
On first start after the fix, existing annotations are recoded automatically (`v → 7 − v` for values 1–6; 0 and empty
values are untouched). The migration runs once and records its date in the `settings` table under
`skin_tone_fitzpatrick_order`. **CSV exports made before that date use the old, reversed coding** — convert them
with `7 − value` for values 1–6.

## Cross-annotation study (inter-annotator agreement)

Alexia and Blanca each blindly re-annotate a stratified sample of the people annotated by the other.
The unit is one person. Only images with exactly one person are in the study (couple images and multi-person
images are left out, because a blind re-annotator cannot know which person the original label refers to).

**1. Sample** (stdlib only; reads the database or a CSV export):

```bash
python scripts/xa_sample.py --db /data/intervisions/intervisions.db -o sample.csv
# optional: --intra 30 (own-image units per annotator), --test-user lluis (20 test units to try the interface)
```

Strata are prompt × FST group of the original label (1–2, 3–4, 5–6, 0). Defaults: all FST 5–6 (global cap 50,
`--cap-56`), 65 FST 3–4 (`--n-34`) and 65 FST 1–2 (`--n-12`) spread evenly over prompts, FST 0 at the FST 1–2
rate of its prompt, at least 10 people per prompt (`--min-per-prompt`). Selection inside a stratum is random
with a fixed `--seed` (default 2026) and balanced between original annotators; the same seed and data give the
same file. `pi` = selected / available in the stratum and `weight` = 1/pi. Participants default to
`--annotators alexia,blanca`; each unit goes to the other one. `--intra N` adds N units per annotator from
their own images (phase `intra`). `--test-user U` adds units for U with phase `test`: they use a separate random
stream (the study sample is identical with or without them), are ignored by the analysis, and can be removed from
the admin page at any time. The script prints a per-stratum summary (selected/available).

**2. Re-annotate.** Admin → *Cross-annotation* → import `sample.csv`. The import is all-or-nothing and rejects any
unit assigned to its original annotator (outside the intra phase). Annotators get a *Cross-annotation* card on
their dashboard. The view shows only the image and the three attributes (gender, age, skin tone; all required,
with the "Cannot determine" options available; Enter saves). No prompt, URL, licence, notes, original labels,
stratum, original annotator or balance counter; the image is served through the app so its URL and file name are
not exposed. The order is shuffled across prompts and strata. While an annotator has pending items the dataset
viewer is closed to them, and images in the sample cannot be deleted.

**3. Analyse.** Admin → *Cross-annotation* → *Export re-annotations* (one row per unit with sample info,
original labels and new labels), then:

```bash
pip install -r scripts/requirements-analysis.txt
python scripts/xa_analysis.py intervisions_xa_annotations.csv -o xa_report
```

Output in `xa_report/`: `report.md` (summary tables), `metrics.csv` (every metric with 95% CI and n),
`confusion/*.csv` and `confusion_*.png`, and `cd_usage.csv` ("cannot determine" use per annotator, as original
annotator and as re-annotator). Metrics: Cohen's kappa for gender; quadratic weighted kappa and ordinal
Krippendorff's alpha for FST and age; percent agreement for all three. Scopes: overall weighted by `w`,
overall unweighted, per FST group and per stratum (unweighted), and per annotator for intra units.
"Cannot determine" is analysed both as a category (at maximum distance from every other value in the ordinal
metrics) and excluded. CIs: stratified bootstrap within strata, 2,000 iterations (`--iterations`, `--seed`).

## Default credentials

- **Admin**: username `admin`, password `intervisions2025` (change in the service file before deploying)

## Funding Acknowledgement

![Co-funded by the European Union](eu-funded.png)

Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or the European Education and Culture Executive Agency (EACEA). Neither the European Union nor EACEA can be held responsible for them.
