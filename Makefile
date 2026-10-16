.PHONY: setup db dev start test
PY = backend/.venv/bin

setup:  ## one-time install of backend + frontend dependencies
	python3 -m venv backend/.venv
	$(PY)/pip install -q -r backend/requirements.txt
	cd frontend && npm install

db:     ## start PostgreSQL 16 in Docker (creates the `portal` and `portal_test` databases)
	docker compose up -d --wait db

dev:    ## API on :8000 + Vite on :5173 (open http://localhost:5173); needs the database running
	cd backend && ( ../$(PY)/uvicorn app.main:app --reload --port 8000 & cd ../frontend && npm run dev & wait )

start:  ## single process: build the UI, serve everything on http://localhost:8000
	cd frontend && npm run build
	cd backend && ../$(PY)/uvicorn app.main:app --port 8000

test:   ## backend tests (needs the database running)
	cd backend && ../$(PY)/python -m pytest -q
