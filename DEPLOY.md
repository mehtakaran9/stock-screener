# Deployment

## GitHub Actions — daily scan + email

### Required secrets

Go to **Settings → Secrets and variables → Actions → Repository secrets** and add:

| Secret | Purpose |
|--------|---------|
| `EMAIL_SMTP_HOST` | SMTP server hostname (e.g. `smtp.gmail.com`) |
| `EMAIL_USER` | Sender email address |
| `EMAIL_PASSWORD` | 16-char Gmail App Password ¹ |
| `GH_PAT` | Personal Access Token with `repo` scope ² |

### Optional secret

| Secret | Default | Purpose |
|--------|---------|---------|
| `EMAIL_SMTP_PORT` | `587` | SMTP port — omit to use the standard submission port |

¹ Gmail requires an **App Password** (not your account password) when 2-Step Verification is enabled.  
Create one at **myaccount.google.com → Security → App Passwords**.

² `GH_PAT` is used by the `add-subscriber` workflow to update the `EMAIL_LIST` variable.  
Create one at **github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)** with `repo` scope.

### Required variable

Go to **Settings → Secrets and variables → Actions → Variables tab** and add:

| Variable | Value |
|----------|-------|
| `EMAIL_LIST` | Seed recipient list — one address per line |
| `RENDER_URL` | `https://your-service.onrender.com` — enables the **Render Keepalive** workflow, which pings the backend at 11:50 AM ET on weekdays so it is warm before the noon scan. Note: GitHub disables scheduled workflows after 60 days of repository inactivity; push a commit or manually trigger the workflow to re-enable it. |

Example initial value:
```
alice@example.com
bob@example.com
```

The daily scan writes this variable to `backend/recipients.txt` at runtime. Use the **Actions → Add Email Subscriber → Run workflow** button to manage the list going forward.

### Manual workflow dispatch options

Trigger a run from **Actions → Daily Market Scan → Run workflow** and choose:

| Input | `true` | `false` |
|-------|--------|---------|
| `full_scan` | Run the full scan, skip trading-day check | Skip scan |
| `send_email` | Send results email | Suppress email |

**Testing SMTP credentials:** Set `full_scan=false, send_email=true`. This skips the scan entirely and sends a styled test email with sample data in seconds — useful for verifying SMTP config without waiting for a full scan.

---

## Render.com — FastAPI backend

1. Connect your GitHub repo in the Render dashboard (or use the included `render.yaml` blueprint).
2. Set these environment variables in the Render dashboard:

| Variable | Value |
|----------|-------|
| `ALLOWED_ORIGINS` | `https://your-app.vercel.app` (stable production URL) |
| `ALLOWED_ORIGIN_REGEX` | `^https://stock-screener[^.]*\.vercel\.app$` (covers Vercel preview URLs) |
| `PYTHONPATH` | `/opt/render/project/src` |

---

## Vercel — React frontend

1. Import the repo in Vercel; set the **Root Directory** to `frontend/`.
2. Add one environment variable:

| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://your-service.onrender.com` |
