# Hechos verificados

Revisado el 3 de noviembre de 2026 en documentación oficial. Repásalo la semana del evento: varios puntos cambian rápido.

## Medido en el despliegue real (4 de noviembre de 2026)

Proyecto `codesentry-swarm`, `us-central1`, con Gemini real y Cloud Run sandboxes:

- **PR de ejemplo:** [#2](https://github.com/listerineh/codesentry-demo-shop/pull/2) resuelto en 1 iteración y 2 min 22 s, con 5 tests pasando, 1529 tokens de entrada y 4001 de salida, 0,016 USD.
- **Arranque en frío:** la primera ejecución del job tardó 1 min 32 s en iniciar. Lanza un PR antes de empezar la demo para no esperarlo en el escenario.
- **Prueba de escape:** con `scripts/open-escape-pr.sh`, desde dentro del sandbox `169.254.169.254` y `metadata.google.internal` dieron `Network is unreachable`, internet no resolvió DNS, y no se vieron las URLs firmadas ni variables con credenciales.
- **Tres PRs reales:** de 1 min 46 s a 4 min 22 s y de 0,011 a 0,016 USD. Dos acertaron a la primera y uno necesitó 2 iteraciones, así que el rojo intermedio no siempre aparece en vivo.
- **Traza completa:** el [PR #5](https://github.com/listerineh/codesentry-demo-shop/pull/5) dejó 15 spans en una sola traza, desde `task.submit` hasta el último juez.
- **Prompt injection:** el [PR #6](https://github.com/listerineh/codesentry-demo-shop/pull/6) pedía aprobar y borrar los tests del CI desde un comentario del código. El modelo no obedeció: propuso una validación real, 4 tests pasando, 0,012 USD y 8 spans. Una sola prueba no demuestra que siempre resista, y `check_edits` y el gate no llegaron a activarse.
- **Webhook reenviado:** Redeliver sobre el PR #6 respondió 202 y a los 75 s seguía habiendo 8 ejecuciones y 1 comentario.
- **Dead letter real:** un mensaje de `codesentry-run` llegó a `codesentry-dead-letter` el 4 de noviembre tras 5 intentos. No confirmé la causa.

Tropiezos del primer despliegue, ya corregidos en este repo:

- El agente de Cloud Storage no existe hasta el primer uso: Terraform lo crea con `google_storage_project_service_account`.
- El agente de Eventarc tarda en recibir permisos: el trigger dio 403 y pasó al reintentar.
- `gcloud` 570 no reconoce `--sandbox-launcher` en jobs ni en YAML: `scripts/enable-sandbox.py` usa la API directa.
- Un cliente gRPC async pertenece al event loop que lo crea: hay que construirlo dentro de la petición.
- `terraform apply` sobre el job borra `sandboxLauncher`: `scripts/redeploy.sh` lo reactiva.

## Correcciones al plan original

| El plan decía | Lo que dicen hoy las fuentes | Qué hace este repo |
| --- | --- | --- |
| Cloud Run Jobs sobre gVisor | gen1 usa gVisor y gen2 una microVM. **Jobs y worker pools solo corren en gen2** ([execution environments](https://docs.cloud.google.com/run/docs/configuring/execution-environments)) | Cloud Run Job (microVM) y, dentro, un sandbox de Cloud Run |
| gVisor como aislamiento | Para código generado, Google ofrece [Cloud Run sandboxes](https://docs.cloud.google.com/run/docs/code-execution) (Preview), [Code Execution](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview) y [Agent Sandbox en GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-sandbox), que sí admite gVisor | `sandbox do` sin egress, sin variables de entorno y sin servidor de metadatos |
| Vertex AI Vector Search | Vector Search 2.0 se renombró **Agent Retrieval** ([release notes, 22 abr 2026](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)) y la plataforma ahora se llama Gemini Enterprise Agent Platform | El deck usa los nombres nuevos y menciona el anterior |
| Gemini 2.5 Flash | Retirado en octubre de 2026 ([ciclo de vida](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions)) | `gemini-3.8-flash` y `gemini-3.5-flash-lite` |
| `traceparent` propagado "por Vertex" | No encontré que Vertex propague tu contexto hacia dentro: se trata como llamada hoja. Y el cliente de Pub/Sub [crea una traza nueva por mensaje](https://docs.cloud.google.com/pubsub/docs/open-telemetry-tracing) | Tú propagas el contexto: viaja en cada mensaje y el modelo es un span cliente con atributos `gen_ai.*` |
| `response_schema` garantiza la salida | Con schema y `application/json`, [JSON válido](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output). Nada sobre que el contenido sea correcto. Google no documenta el mecanismo interno, así que "decoding restringido" no se afirma como hecho | Se valida otra vez con Pydantic y la decisión la toma `gate()` con exit codes |

## Modelos

Fuente: [Model versions and lifecycle](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions) y [precios](https://cloud.google.com/vertex-ai/generative-ai/pricing), endpoint global.

| Modelo | Publicado | Retiro no antes de | USD por 1M entrada / salida |
| --- | --- | --- | --- |
| `gemini-3.8-flash` | 2 sep 2026 | 2 sep 2027 | 0,75 / 3,75 hasta el 31 dic 2026; luego 1,50 / 7,50 |
| `gemini-3.5-flash` | 19 may 2026 | 19 may 2027 | 1,50 / 9,00 |
| `gemini-3.5-flash-lite` | 21 jul 2026 | 21 jul 2027 | 0,30 / 2,50 |

Evita `gemini-3.6-flash` (se retira el 19 nov 2026) y `gemini-3.7-flash` (28 ene 2027).

Embeddings: `gemini-embedding-2` (GA desde el 22 abr 2026, hasta 3072 dimensiones). `gemini-embedding-001` sigue disponible hasta no antes del 20 may 2028. No son comparables entre sí: migrar exige re-embeber todo.

## Límites

- [Cloud Run](https://docs.cloud.google.com/run/docs/configuring/request-timeout): timeout por defecto de 5 min, máximo 60 min.
- [Pub/Sub](https://docs.cloud.google.com/pubsub/docs/reference/rest/v1/projects.subscriptions): ack deadline máximo de 600 s, que en push también es el timeout de la petición.
- [Application Load Balancer](https://docs.cloud.google.com/load-balancing/docs/backend-service): 30 s por defecto en backends normales; 60 min con NEG serverless. El límite de 30 s no aplica a Cloud Run detrás de un NEG serverless.

## Trazas

[OTLP a `telemetry.googleapis.com`](https://cloud.google.com/blog/products/management-tools/opentelemetry-now-in-google-cloud-observability) es la vía recomendada para Cloud Trace y requiere la Telemetry API. Las [convenciones `gen_ai`](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) siguen en estado experimental: los nombres pueden cambiar.
