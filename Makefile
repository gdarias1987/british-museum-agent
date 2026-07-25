PYTHON ?= python
KUBECTL ?= kubectl
POWERSHELL ?= powershell
K8S_OVERLAY ?= dev

.PHONY: setup ingest api ui test docker-up docker-down k8s-validate k8s-render k8s-deploy k8s-status

setup:
	python -m venv .venv
	.venv/Scripts/python -m pip install -r requirements.txt

ingest:
	python scripts/ingest.py

api:
	uvicorn british_museum_agent.api.main:app --reload --app-dir src

ui:
	streamlit run src/british_museum_agent/interfaces/streamlit/app.py

test:
	pytest

docker-up:
	docker compose up --build

docker-down:
	docker compose down

k8s-validate:
	$(PYTHON) scripts/validate_k8s.py --root .

k8s-render:
	$(KUBECTL) kustomize deploy/overlays/$(K8S_OVERLAY)

k8s-deploy:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_k8s.ps1 -Action apply -Overlay $(K8S_OVERLAY)

k8s-status:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_k8s.ps1 -Action status -Overlay $(K8S_OVERLAY)
