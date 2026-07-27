# Despliegue y operación

## Desarrollo local: Docker Compose

1. Copiar `.env.example` a `.env`.
2. Completar como mínimo `STAFF_DEMO_USERNAME`, `STAFF_DEMO_PASSWORD`, `JWT_SECRET` y `MCP_INTERNAL_TOKEN`. Las claves de Gemini, LangSmith y Phoenix Cloud son opcionales para el modo local.
3. Ejecutar `docker compose up --build`.
4. Abrir:
   - UI: `http://localhost:8501`
   - API: `http://localhost:8000/docs`
   - MCP: `http://localhost:8001/health`
   - Phoenix: `http://localhost:6006`

El backend ejecuta la ingesta y la indexación Chroma durante el arranque. La primera ejecución puede tener cold start por descarga/carga del modelo de embeddings. Si se reutiliza un índice local antiguo o inconsistente, reconstruirlo con `docker compose run --rm --no-deps backend python scripts/ingest_chroma.py --force` y volver a levantar el backend. Los datos no quedan en la imagen: Compose monta `data/raw`, `data/processed`, `data/chroma` y `data/sqlite`; Phoenix y la caché de Hugging Face usan volúmenes nombrados.

Los contenedores tienen healthchecks y dependencias ordenadas: MCP debe estar saludable antes del backend y el backend antes de la UI.

## Kubernetes

La opción de despliegue es Kubernetes mediante Kustomize; fue aplicada y probada end-to-end en el cluster local de Docker Desktop:

- Base: `deploy/base`.
- Entorno de desarrollo: `deploy/overlays/dev`.
- Namespace: `british-museum-agent`.
- Componentes: backend, UI, MCP server y Phoenix.
- Persistencia: PVC separados para Chroma, SQLite, Hugging Face y Phoenix.
- Seguridad: pods no root, filesystem de solo lectura, capacidades Linux descartadas, probes y NetworkPolicy.

Validar sin aplicar:

```powershell
python scripts/validate_k8s.py --root . --kubectl-dry-run skip
.\scripts\deploy_k8s.ps1 -Action validate -Overlay dev
.\scripts\deploy_k8s.ps1 -Action render -Overlay dev
.\scripts\deploy_k8s.ps1 -Action dry-run -Overlay dev
```

Aplicar en un cluster con `kubectl` configurado:

```powershell
$env:STAFF_DEMO_PASSWORD = "una-clave-real-larga"
$env:JWT_SECRET = "un-secreto-real-de-al-menos-32-caracteres"
$env:MCP_INTERNAL_TOKEN = "un-token-real-de-al-menos-32-caracteres"
.\scripts\deploy_k8s.ps1 -Action apply -Overlay dev
.\scripts\deploy_k8s.ps1 -Action status -Overlay dev
```

El script crea/actualiza el `Secret` desde variables de proceso. `deploy/base/secret.example.yaml` es solo una plantilla y no se aplica.

### Operación diaria en Docker Desktop

El administrador único carga `.env` sin imprimir secretos, valida `metrics-server`, construye las imágenes, aplica Kustomize, espera rollouts y crea port-forwards ocultos:

```powershell
.\scripts\manage_k8s.ps1 start
.\scripts\manage_k8s.ps1 status
.\scripts\manage_k8s.ps1 stop
```

URLs: UI `http://localhost:18501`, API `http://localhost:18000/docs` y Phoenix `http://localhost:16006`. La acción `stop` libera memoria eliminando HPA y escalando los cuatro deployments a cero, pero conserva los PVC. En Docker Desktop el script agrega `--kubelet-insecure-tls` a `metrics-server` cuando el certificado interno del kubelet no incluye su IP; no aplica ese ajuste automáticamente en otros contexts.

## Escalado y límites actuales

La UI tiene HPA de 2 a 5 réplicas. El backend tiene HPA declarado, pero queda intencionalmente limitado a 1 réplica (`minReplicas: 1`, `maxReplicas: 1`) porque comparte volúmenes `ReadWriteOnce` con SQLite, Chroma y la caché de embeddings. Esto evita corrupción o bloqueos por múltiples escritores.

Para escalar el backend de verdad hay que migrar primero SQLite/Chroma y la caché a servicios o almacenamiento multi-writer adecuados, y luego subir `maxReplicas`. El HPA necesita un `metrics server` (`metrics-server`) instalado en el cluster; los manifiestos no lo instalan.

No se implementó serverless como ruta principal: el proceso depende de volúmenes persistentes y tiene warm-up de embeddings. Kubernetes es la alternativa entregable para mostrar despliegue, healthchecks, escalado controlado y rollback.

## Rollback

```powershell
.\scripts\deploy_k8s.ps1 -Action rollback -Overlay dev -Component backend
```

El rollback usa `kubectl rollout history` y `kubectl rollout undo`. Phoenix está fijado a `arizephoenix/phoenix:19.6.0`; antes de producción también deben publicarse las imágenes propias en un registry con tags inmutables.

## Operación recomendada

- Verificar `kubectl get pods,deployments,services,persistentvolumeclaims,horizontalpodautoscalers -n british-museum-agent`.
- Confirmar que los PVC estén `Bound`.
- Revisar `/api/v1/health`, `/metrics` y `/api/v1/metrics/summary`.
- Mantener los secretos fuera de Git y nunca construirlos dentro de una imagen.
- Respaldar SQLite, Chroma y Phoenix antes de cambios de infraestructura.