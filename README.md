# Mitraa

*Tell Mitraa what you want done. It remembers your context, plans the work with real optimization math, uses tools on your machine, and asks before doing anything risky.*

Mitraa is a local-first personal AI agent built on NVIDIA Nemotron (served by Nebius Token Factory). The build follows [NOVA_BUILD_PLAN.md](NOVA_BUILD_PLAN.md), where the agent is called NOVA; it was renamed Mitraa.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Agent core and file control | ✅ Done |
| 2 | Memory and project intelligence | ✅ Done |
| 3 | Research and documents | — |
| 4 | Web UI, integration and demo | — |

## Quick start

```powershell
cd backend
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # add your Nebius key, base URL and Nemotron model id
python -m mitraa.cli      # terminal chat
python -m mitraa.main     # API on http://127.0.0.1:8000
python -m pytest          # tests
```

See [backend/README.md](backend/README.md) for the API, the security model and the acceptance tests.
