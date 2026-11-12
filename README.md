# FPN Digital Library — Deployment & Run Guide

A role-based (Student / Lecturer / Admin) digital library built with Flask.

## 1. Run locally

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

**Default admin login:** username `admin`  /  password `admin2025`
(Change this password immediately after your first login.)

## 2. Host it online (any day, anytime)

The app is production-ready:

- All secrets/config come from **environment variables** (nothing hard-coded).
- Runs behind a proxy via **ProxyFix** (correct HTTPS URLs on Render/Railway/Heroku/Nginx).
- Served by **gunicorn** (`Procfile` + `wsgi.py` included).
- Uploads are capped at **50 MB**; oversized/invalid input shows a friendly message instead of crashing.
- Custom **error pages** (400/403/404/405/413/500) — users never see a raw traceback.

### Option A — Render.com (easiest, free tier)
1. Push this folder to a GitHub repo.
2. On Render: **New → Web Service**, pick the repo (`render.yaml` is detected automatically).
3. Render sets `SECRET_KEY` for you. Deploy. Done.

### Option B — Any host with gunicorn
```bash
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export FLASK_DEBUG=0
export SESSION_COOKIE_SECURE=1
gunicorn wsgi:app --bind 0.0.0.0:$PORT
```

### Using PostgreSQL instead of SQLite
Set `DATABASE_URL` to your Postgres connection string. Tables are created automatically on first boot.

## 3. Registration without admin approval (whitelist)

Students and lecturers can only register if they are on the **approved list**:

1. Admin logs in → sidebar **“Approved List”**.
2. Add students (matric number) and lecturers (staff ID) with their details.
3. On the registration page the person enters their matric number / staff ID, their
   details appear automatically, they set an email + password + upload documents,
   and the account is created **instantly — no approval step**.
4. Anyone whose number is **not** on the list is politely refused.

Sample rows are seeded on first boot (e.g. student `FPN/CS/22/0001`, lecturer `FPN/STAFF/SE/001`).

## 4. Security checklist before going live
- [ ] Change the default admin password.
- [ ] Set a strong `SECRET_KEY`.
- [ ] Set `SESSION_COOKIE_SECURE=1` (HTTPS).
- [ ] Keep `FLASK_DEBUG=0`.
