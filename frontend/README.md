# HR platform frontend

React, TypeScript, Vite, and Tailwind CSS presentation layer for recruiter workflows, public applications, and candidate voice interviews. Business rules remain in the Python application layer and are accessed through `src/api/`.

## Run locally

The frontend requires Node.js 20.19 or newer and the FastAPI backend on `http://127.0.0.1:8000`.

```powershell
npm ci
Copy-Item .env.example .env
npm run dev
```

Leave `VITE_API_BASE_URL` blank for local development. Vite proxies `/api` and `/health` to the backend.

## Quality checks

```powershell
npm run test
npm run lint
npm run typecheck
npm run build
```

## Structure

```text
src/api/          Centralized HTTP client and resource modules
src/auth/         Authentication state and protected routes
src/components/   Layout, reusable controls, and workflow panels
src/lib/          Shared helpers
src/pages/        Recruiter, public application, and interview screens
src/test/         Vitest setup and rendering helpers
```

Only `src/api/` calls `fetch`; pages and components use that layer for authentication, error handling, and consistent request behavior.
