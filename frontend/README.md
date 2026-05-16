# Frontend — React + TypeScript + Vite

React SPA that connects to the FastAPI backend via Server-Sent Events and displays real-time stock screening results.

## Stack

- **React 19** + **TypeScript** (strict mode)
- **Vite 8** — dev server and bundler
- **Lucide React** — icon set

## Project structure

```
src/
  App.tsx              # Root component — SSE client, scan state, layout
  App.css              # Global styles and CSS custom properties
  types.ts             # Shared Stock interface (27 fields)
  components/
    StockTable.tsx     # Sortable results table with expandable rows
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_API_URL` | `http://localhost:8000` | Backend base URL |

Set `VITE_API_URL` in the Vercel dashboard to point at your Render service.

## Running locally

```bash
npm install
npm run dev
# UI at http://localhost:5173
```

Requires the backend to be running at `http://localhost:8000` (or set `VITE_API_URL`).

## Building

```bash
npm run build    # TypeScript check + Vite bundle → dist/
npm run preview  # Serve the dist/ build locally
```

## Linting

```bash
npm run lint
```
