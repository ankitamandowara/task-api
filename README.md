# Natural-Language Task API

A REST API where you type a task in plain English, for example
*"Remind me to pay rent on the 1st of every month"*, and get a structured task back.

It supports subtasks, safe retries (`Idempotency-Key`), validation with **one consistent error shape**,
filtering, search, ordering and pagination. The natural-language step lives behind a swappable function,
so the same code works with the rule-based parser now and a real LLM later.

**Stack:** Python, Django, Django REST Framework, django-filter, PostgreSQL (Docker).

## Setup

```bash
# 1. Virtual environment
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Postgres in Docker
docker compose up -d                  # starts Postgres 16 on localhost:5432 (db: tasksdb, user/password: postgres)

# 3. Migrate and run
python manage.py migrate
python manage.py runserver            # http://127.0.0.1:8000/api/
```

Database settings are read from environment variables, with the Docker defaults above:
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`.

Run the tests (46 tests):

```bash
python manage.py test
# no Docker handy? Use SQLite just for tests:  USE_SQLITE=1 python manage.py test
```

**Optional LLM key.** `settings.py` reads `LLM_API_KEY = os.environ.get("LLM_API_KEY")`.
The shipped parser is rule-based and does not need it. If you plug in an LLM-backed parser, return
**503** in the error shape when the key is missing instead of crashing. Never commit a key:
`.env` is in `.gitignore`.

```bash
export LLM_API_KEY="your-key"        # only if you add an LLM-backed parser
```

## The one error shape

Every 400, 404, 405, 422 and 502 response looks exactly like this. `code` and `message` are always strings,
`details` is always a list.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": [{ "field": "title", "issue": "must not be empty" }]
  }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Bad input (empty title, bad date, bad priority, not a boolean, bad filter value, malformed JSON) |
| 400 | `SUBTASK_NOT_ALLOWED` | Giving subtasks to a subtask, or breaking down a subtask |
| 404 | `NOT_FOUND` | Unknown task id, or a page number out of range |
| 422 | `IDEMPOTENCY_KEY_REUSED` | Same `Idempotency-Key` with a different body |
| 502 | `PARSER_ERROR` / `INVALID_PARSER_OUTPUT` / `BREAKDOWN_ERROR` / `INVALID_BREAKDOWN_OUTPUT` | The parser or breakdown raised, or produced something that failed validation. Nothing is saved |

## Task fields and validation

| Field | Type | Rules |
|---|---|---|
| `id` | integer | read-only |
| `title` | string | required, not empty, max 255 |
| `description` | string | required, not empty |
| `completed` | boolean | real `true`/`false` only (`"yes"`, `1`, `null` are rejected). Default `false` |
| `due_date` | string or null | real `YYYY-MM-DD` date. `2026-02-30` is rejected |
| `priority` | string | `low`, `medium` or `high`. Default `medium` |
| `recurrence` | string or null | `daily`, `weekly`, `monthly`, `yearly` |
| `parent` | integer or null | read-only. Set through `/subtasks/` or `/breakdown/` |
| `created_at` | datetime | read-only |

## Endpoints

Base URL: `http://127.0.0.1:8000/api`

### `POST /tasks/`: create a task

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: abc-123" \
  -d '{"title": "Pay rent", "description": "Monthly rent", "priority": "high", "due_date": "2026-12-01"}'
```

`201 Created`

```json
{
  "id": 1, "title": "Pay rent", "description": "Monthly rent", "completed": false,
  "due_date": "2026-12-01", "priority": "high", "recurrence": null, "parent": null,
  "created_at": "2026-11-25T08:11:11.118811+05:30"
}
```

**Validation error example** (`POST /tasks/` with `{"title": "", "description": "x", "due_date": "2026-02-30"}`), `400`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": [
      { "field": "title", "issue": "must not be empty" },
      { "field": "due_date", "issue": "must be a valid date in YYYY-MM-DD format" }
    ]
  }
}
```

### `GET /tasks/`: list tasks

Filtering, search, ordering and pagination all work together in one request:

```
GET /api/tasks/?priority=high&completed=false&search=pay&ordering=-due_date&page_size=2&page=1
```

| Query param | Meaning |
|---|---|
| `completed`, `priority`, `recurrence`, `due_date`, `parent` | exact-match filters |
| `due_before`, `due_after` | `due_date <= x` / `due_date >= x` (`YYYY-MM-DD`) |
| `is_subtask` | `true` for subtasks only, `false` for top-level only |
| `search` | case-insensitive match in `title` and `description` |
| `ordering` | `id`, `title`, `due_date`, `priority`, `created_at`. Prefix `-` for descending |
| `page`, `page_size` | page number, page size (default 10, max 100) |

`200 OK`

```json
{
  "count": 2,
  "next": "http://127.0.0.1:8000/api/tasks/?ordering=-due_date&page=2&page_size=1&priority=high&search=pay",
  "previous": null,
  "results": [ { "id": 3, "title": "Pay phone bill", "...": "..." } ]
}
```

### `GET /tasks/{id}/`, `PUT /tasks/{id}/`, `PATCH /tasks/{id}/`, `DELETE /tasks/{id}/`

* `GET` returns `200` with the task.
* `PUT` replaces the task (`title` and `description` required). `PATCH` changes only the fields you send. Both return `200`.
* `DELETE` returns `204`, and deletes the task's subtasks too.
* An unknown id returns `404`:

```json
{ "error": { "code": "NOT_FOUND", "message": "Task not found", "details": [] } }
```

### `POST /tasks/from-text/`: create a task from plain English

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/from-text/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: text-001" \
  -d '{"text": "Remind me to pay rent on the 1st of every month"}'
```

`201 Created` (today is 2026-11-25 in this example)

```json
{
  "id": 1, "title": "Pay rent",
  "description": "Remind me to pay rent on the 1st of every month",
  "completed": false, "due_date": "2026-12-01", "priority": "medium",
  "recurrence": "monthly", "parent": null, "created_at": "2026-11-25T08:11:11.118811+05:30"
}
```

What the rule-based parser understands: `today`, `tonight`, `tomorrow`, `day after tomorrow`, `in N days/weeks/months`,
`next week`, `next month`, weekdays (`monday`, `next friday`), `every monday`, ordinals (`1st`, `on the 15th`),
month names (`5 dec`, `march 3`), ISO dates (`2026-12-01`), and recurrence words
(`every day/week/month/year`, `daily`, `weekly`, `monthly`, `yearly`).

**Never trust the output.** The dictionary from the parser goes through `TaskSerializer` exactly like user input.
If it fails validation, or the parser raises, the API returns **502** in the error shape and saves nothing:

```json
{
  "error": {
    "code": "INVALID_PARSER_OUTPUT",
    "message": "Parser output failed validation",
    "details": [{ "field": "due_date", "issue": "must be a valid date in YYYY-MM-DD format" }]
  }
}
```

(Try `{"text": "Party on 2026-02-30"}`.) Bad request bodies (missing, blank, or non-string `text`) return `400`.

#### Swapping the parser for an LLM

The function lives in `tasks/ai.py`. Keep this signature and the views do not need to change:

```python
def parse_task_text(text: str, today: datetime.date) -> dict:
    """
    Returns {
        "title": str,
        "description": str,
        "due_date": "YYYY-MM-DD" | None,
        "recurrence": "daily" | "weekly" | "monthly" | "yearly" | None   # optional key
    }
    May raise any exception: the view turns it into a 502.
    """
```

`break_down_task(task) -> list[dict]` follows the same idea (each dict has `title`, `description`, `priority`, `due_date`).

### `POST /tasks/{id}/subtasks/` and `GET /tasks/{id}/subtasks/`

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/1/subtasks/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Check amount due", "description": "Look at the invoice"}'
```

`201 Created`, with `"parent": 1` in the body. `GET` returns `200` with a plain array of that task's subtasks.
A task that is itself a subtask cannot be given subtasks, so the API returns `400`:

```json
{
  "error": {
    "code": "SUBTASK_NOT_ALLOWED",
    "message": "A subtask cannot have subtasks",
    "details": [{ "field": "id", "issue": "task is already a subtask" }]
  }
}
```

### `POST /tasks/{id}/breakdown/`

Calls `break_down_task(task)`: splits the title on commas / `and` / `then`, or uses a template
(pay, write, buy, trip, clean, study) or a generic plan/do/review fallback.

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/1/breakdown/ -H "Idempotency-Key: bd-1"
```

`201 Created`

```json
[
  { "id": 2, "title": "Check the amount due", "description": "Step 1 of 'Pay rent'", "priority": "high", "due_date": "2026-12-01", "parent": 1, "...": "..." },
  { "id": 3, "title": "Make the payment",     "description": "Step 2 of 'Pay rent'", "priority": "high", "due_date": "2026-12-01", "parent": 1, "...": "..." },
  { "id": 4, "title": "Save the receipt",     "description": "Step 3 of 'Pay rent'", "priority": "high", "due_date": "2026-12-01", "parent": 1, "...": "..." }
]
```

All-or-nothing: every item is validated first. If any item is invalid, **none** are saved and the API returns `502`
(`INVALID_BREAKDOWN_OUTPUT`) with details like `{"field": "items[1].title", "issue": "must not be empty"}`.
Breaking down a subtask returns `400`.

## Idempotency

Optional `Idempotency-Key` header on `POST /tasks/`, `POST /tasks/from-text/` and `POST /tasks/{id}/breakdown/`.

* Same key + same body: the stored response is returned. Nothing new is created, and the parser is not called again.
* Same key + different body: `422` (`IDEMPOTENCY_KEY_REUSED`).
* Only **successful** (2xx) responses are stored, so a client can fix bad input and retry with the same key.
* The store is a module-level dictionary in `tasks/idempotency.py`, so it resets when the server restarts.
  Keys are scoped by URL path (the same key on two different endpoints or tasks is not a clash).

**Prove it.** Send the same request twice, then check the database:

```bash
curl -s -X POST http://127.0.0.1:8000/api/tasks/ -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" \
  -d '{"title":"Pay rent","description":"Monthly rent"}'
# run the exact same command again, then in your DB client:
```
```sql
SELECT count(*) FROM tasks_task WHERE title = 'Pay rent';   -- 1
```

## Testing and hardening

* `postman/task-api.postman_collection.json`: 44 requests (CRUD, validation, 404s, from-text, subtasks, idempotency, filtering). Import it into Postman or Echo API. It uses a `base_url` variable.
* `tasks/tests/test_api.py`: 46 automated tests.
* To simulate bad model output without an LLM, temporarily make `parse_task_text` return `{"title": "", ...}`. The API returns 502 and no row is saved (this is also covered by a test using `mock.patch`).

### What a first-pass AI checklist usually misses, and what I added

A generated checklist typically covers the happy path for each endpoint and one or two validation errors. The cases I added after critiquing it:

* **Empty or invalid input:** blank-but-not-empty titles (`"   "`), missing fields, non-object bodies (`[1, 2]`), malformed JSON.
* **Strict types:** `completed` as `"yes"`, `"true"`, `1` or `null` (DRF would quietly accept some of these) and loose date formats like `2026-2-3`.
* **Impossible dates:** `2026-02-30`, `2026-13-01`, both directly and through the parser.
* **Unknown ids and odd ids:** 404 for `PUT`, `PATCH`, `DELETE`, `/subtasks/` and `/breakdown/`, plus a non-numeric id (`/tasks/abc/`) and an out-of-range page. All use the same error shape.
* **Repeated idempotency key:** same key + same body (one row), same key + different body (422), key order inside the JSON body, and a failed request that must **not** be stored so the client can fix it and retry.
* **Bad parser output:** empty title, wrong types, an impossible date, a non-dict return value, and the parser raising. Each returns 502 with nothing saved.
* **Breakdown is all-or-nothing:** one bad item means zero rows saved.
* **A subtask of a subtask**, and breaking down a subtask (both 400).
* **Cascade delete:** deleting a parent removes its subtasks.
* **Errors DRF raises by itself** (405, bad filter values, bad page) are also forced into the one error shape by a custom exception handler.

## Project layout

```
config/            Django settings and root urls
tasks/
  models.py        Task model (self-referencing parent FK, related_name="subtasks", CASCADE)
  serializers.py   TaskSerializer + strict boolean/date fields
  views.py         TaskViewSet: CRUD, from-text, subtasks, breakdown
  errors.py        error_response(), errors_to_details(), exception handler
  ai.py            parse_task_text() and break_down_task()  <- swap for an LLM here
  idempotency.py   in-memory Idempotency-Key store
  filters.py       django-filter FilterSet
  pagination.py    page / page_size
  tests/           automated tests
openapi.yaml       hand-written OpenAPI 3 description
postman/           Postman collection
docker-compose.yml Postgres
```

