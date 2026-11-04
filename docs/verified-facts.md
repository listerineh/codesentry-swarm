# Hechos verificados

Revisado el 3 de noviembre de 2026 en documentación oficial. Repásalo la semana del evento: varios puntos cambian rápido.

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
