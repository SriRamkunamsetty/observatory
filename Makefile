run:
	API_KEY=dev-key DATA_DIR=./data flask --app "observatory.server:create_app" run --port 8082
test:
	python -m pytest --cov=observatory --cov-report=term-missing
lint:
	ruff check .
docker:
	docker compose up --build
screenshots:
	python scripts/capture_screenshots.py
