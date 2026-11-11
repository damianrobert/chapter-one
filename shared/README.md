# shared

Code that more than one service needs. **Only** event envelope/schema definitions and
tiny helpers — never business logic (see `CLAUDE.md`).

| File        | What                                                                  |
|-------------|-----------------------------------------------------------------------|
| `events.py` | `EventEnvelope` (the wrapper around every event), `new_event()`, the exchange name and event type names |

## Tests

```bash
cd shared
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

## How services use it

Services import it as `from shared.events import EventEnvelope`. Each service's image
still builds from its own folder; Compose hands `shared/` to the build as an extra
folder (wired up when the first service starts sending events, in step 4 piece 2).
