run:
	API_KEY=dev-key DATA_DIR=./data flask --app "observatory.server:create_app" run --port 8082

test:
	python -m pytest --cov=observatory --cov-report=term-missing

test-ml:
	python -m unittest discover -s tests -p "test_ml.py"

lint:
	ruff check .

docker:
	docker compose up --build

stack:
	docker compose up -d --build

helm-lint:
	helm lint deploy/helm/observatory

terraform-fmt:
	terraform fmt -check deploy/terraform/

screenshots:
	python scripts/capture_screenshots.py
