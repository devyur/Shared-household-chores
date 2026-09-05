# Tech Stack Options

Based on `_docs/plan.md`. A few things about the requirements shape the stack choice more than usual:

- **Time-triggered state changes**: auto-assignment at the due date, failure penalty if not completed, and the 24h point-lock all depend on "what time is it now" relative to stored dates — this needs either a scheduled job (cron/worker) or a lazy "evaluate on read" approach.
- **Aggregation-heavy data**: leaderboard, daily/weekly/monthly summaries, points history — this is a natural fit for a relational database with SQL aggregates. NoSQL isn't proposed as an option for that reason.
- **Single household, simple RBAC** (owner vs member) — no need for multi-tenant complexity, so most frameworks' built-in auth is more than sufficient.
- **Audit log** — a plain append-only `activity_log` table is enough; no need for event sourcing.

Four reasonable stacks, from "one integrated framework" to "minimal moving parts":

## Option A — Next.js full-stack (TypeScript everywhere)
**Next.js (App Router) + Prisma + PostgreSQL + NextAuth/Auth.js, deployed on Vercel**

One codebase, one language, API routes and UI live together. Prisma gives you a typed schema that maps cleanly onto Household → Members → Chores → Claims → PointEvents → ActivityLog. Scheduled transitions (assignment, failure, point-lock) run via Vercel Cron hitting an API route, or you avoid a scheduler entirely by computing "is this chore's state stale?" lazily whenever it's fetched.

- **Pros**: fastest to build end-to-end, single deploy target, strong typing from DB to UI, huge amount of tutorial support.
- **Cons**: serverless cron has coarse scheduling (minimum granularity, cold starts); mixing UI and API concerns in one framework can blur "backend logic" if a grader wants to see a distinct API/service layer.
- **Best fit**: if the goal is a working, polished app quickly and the grading doesn't require a separate backend service.

## Option B — Separated SPA + REST API (classic client/server)
**React (Vite) + TypeScript frontend, Node/Express or NestJS backend, PostgreSQL via Prisma/TypeORM, node-cron or BullMQ+Redis for scheduled jobs**

Frontend and backend are distinct deployables talking over a REST API. NestJS in particular gives you modules/controllers/services that map well onto the domain (ChoresModule, PointsModule, ActivityModule) and has a built-in scheduling package (`@nestjs/schedule`) for the due-date job.

- **Pros**: clean separation of concerns, closest to a "textbook" architecture if the assignment wants you to demonstrate API design and a documented contract (OpenAPI/Swagger), easy to unit-test business logic (points math, assignment rules) independent of HTTP/UI.
- **Cons**: more moving parts (two apps, two deploys, CORS/auth token handling), more boilerplate for a homework-sized project.
- **Best fit**: if the assignment emphasizes backend/API design and testable business logic over UI polish.

## Option C — Django batteries-included
**Django + Django REST Framework + PostgreSQL + Celery + Redis (or Celery Beat / django-crontab for scheduling)**

Django's built-in auth, admin site, and ORM cover a lot of this spec for free — the admin panel alone gives the owner a workable "review completed chores / adjust points" interface without building custom UI. Model-level signals or a periodic Celery task can drive the due-date logic (assignment, failure, lock).

- **Pros**: least code to write for auth, admin/review workflows, and migrations; Django ORM's aggregation API handles leaderboard/weekly/monthly rollups cleanly; mature and very well documented.
- **Cons**: Celery+Redis is a heavier dependency for what's ultimately a simple cron need; if you want a custom (non-admin) UI, you're now building a separate frontend anyway.
- **Best fit**: if you're comfortable in Python and want the owner-review/audit parts essentially free via the admin site.

## Option D — Minimal stack, lazy evaluation instead of a scheduler
**FastAPI (or Flask) + SQLite (or Postgres) + SQLModel/SQLAlchemy + server-rendered templates (Jinja2 + a little HTMX) — no background worker at all**

Instead of running a cron job to flip chore states at the due date, every read path (loading the main screen, fetching a chore) checks "has this chore's due date passed and does it need auto-assignment/failure processing?" and applies the transition inline before returning data. This sidesteps the need for Celery/BullMQ/cron entirely, which for a single-household homework app is legitimate — no real-time requirement was specified.

- **Pros**: smallest number of dependencies and deployable artifacts (can literally run as one process with a file-based SQLite DB), easiest to reason about and demo locally, still uses real SQL so the aggregation queries stay natural.
- **Cons**: correctness depends on someone actually loading data after a due date passes (fine for homework/demo use, not fine for a real product); HTMX-based UI is less flashy than a full SPA.
- **Best fit**: if the priority is finishing correctly and simply, and demonstrating the business rules matters more than infrastructure or UI sophistication.

---

## Recommendation

Option D's lazy-evaluation trick is worth using *regardless of which stack is picked* — it removes an entire class of infra (schedulers, workers, Redis) that adds risk without adding grading value for a homework project. On top of that, lean toward **Option A** (Next.js + Prisma + Postgres) for a real UI with the least setup friction, or **Option B** (React + NestJS) if the assignment wants to see a clearly separated, testable backend.
