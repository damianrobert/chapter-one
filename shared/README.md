# shared

Code that more than one service needs. **Only** event envelope/schema definitions and
tiny helpers — never business logic (see `CLAUDE.md`).

| File        | What                                                                  |
|-------------|-----------------------------------------------------------------------|
| `events.py` | `EventEnvelope` (the wrapper around every event), `new_event()`, the exchange name, event type names and payload classes |
| `messaging.py` | RabbitMQ helpers: `declare_exchange()` and `to_message()`, so every service sets up the exchange and wraps events the same way (needs aio-pika) |

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
folder (`additional_contexts` in `docker-compose.yml`), and the Dockerfile copies the
files it needs with `COPY --from=shared`.
