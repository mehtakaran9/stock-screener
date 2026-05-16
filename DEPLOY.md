# Deployment

## GitHub Actions — daily scan + email

### Required secrets

Go to **Settings → Secrets and variables → Actions → Repository secrets** and add:

| Secret | Example |
|--------|---------|
| `EMAIL_SMTP_HOST` | `smtp.gmail.com` |
| `EMAIL_SMTP_PORT` | `587` |
| `EMAIL_USER` | `you@gmail.com` |
| `EMAIL_PASSWORD` | 16-char Gmail App Password ¹ |
| `EMAIL_TO` | one address per line ² |

¹ Gmail requires an **App Password** (not your account password) when 2-Step Verification is enabled.  
Create one at **myaccount.google.com → Security → App Passwords**.

² `EMAIL_TO` is a multi-line secret — each line is one recipient address. The workflow writes it to `backend/recipients.txt` before the scan runs. Example value:
```
alice@example.com
bob@example.com
```

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
| `ALLOWED_ORIGINS` | `https://your-app.vercel.app` |
| `PYTHONPATH` | `/opt/render/project/src` |

---

## Vercel — React frontend

1. Import the repo in Vercel; set the **Root Directory** to `frontend/`.
2. Add one environment variable:

| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://your-service.onrender.com` |
