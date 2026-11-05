# Guion del recorrido por GCP

25 minutos sobre un solo PR, de la entrada a la salida. Proyecto `codesentry-swarm`, región `us-central1`. La regla del recorrido: cada pantalla responde una pregunta que el sistema tuvo que resolver.

## El día de la charla

Ejecuta estos pasos unos 15 minutos antes de empezar.

1. Abre todas las pestañas de la tabla de abajo y comprueba que la sesión de Google sigue activa.
2. Lanza un PR para calentar el job. El primer arranque tardó 1 min 32 s:

```bash
./scripts/open-demo-pr.sh
```

3. Cuando comente, anota su `task_id` y `trace_id` en la libreta, no en pantalla.
4. Abre [Recent Deliveries](https://github.com/listerineh/codesentry-demo-shop/settings/hooks) del webhook en otra pestaña.
5. Haz un ensayo de red: si la consola va lenta, usa las capturas de respaldo.

## Pestañas, en orden

| # | Min | Pantalla | Pregunta que responde |
|---|---|---|---|
| 1 | 2 | [PR en GitHub](https://github.com/listerineh/codesentry-demo-shop/pulls) | ¿Qué disparó todo? |
| 2 | 3 | [Cloud Run: codesentry-api](https://console.cloud.google.com/run/detail/us-central1/codesentry-api/revisions?project=codesentry-swarm) | ¿Por qué responde 202 sin esperar? |
| 3 | 4 | [Pub/Sub: codesentry-run](https://console.cloud.google.com/cloudpubsub/subscription/detail/codesentry-run?project=codesentry-swarm) | ¿Qué pasa si un agente falla? |
| 4 | 4 | [Firestore: tasks](https://console.cloud.google.com/firestore/databases/-default-/data/panel/tasks?project=codesentry-swarm) | ¿Dónde vive el estado y por qué no se repite? |
| 5 | 3 | [Job: codesentry-sandbox](https://console.cloud.google.com/run/jobs/details/us-central1/codesentry-sandbox/executions?project=codesentry-swarm) | ¿Dónde corre el código no confiable? |
| 6 | 2 | [Bucket del sandbox](https://console.cloud.google.com/storage/browser/codesentry-swarm-codesentry-sandbox?project=codesentry-swarm) | ¿Cómo sale el resultado sin credenciales? |
| 7 | 1 | [Eventarc](https://console.cloud.google.com/eventarc/triggers?project=codesentry-swarm) | ¿Quién despierta al juez? |
| 8 | 4 | [Trace Explorer](https://console.cloud.google.com/traces/explorer?project=codesentry-swarm) | ¿Qué pasó, en qué orden y cuánto costó? |
| 9 | 2 | [Dashboard](https://console.cloud.google.com/monitoring/dashboards/builder/36b8e8cb-271e-42f0-8783-19c72524b6be?project=codesentry-swarm) | ¿Cómo se ve con muchos PRs? |

Quedan a mano, por si preguntan: [cuentas de servicio](https://console.cloud.google.com/iam-admin/serviceaccounts?project=codesentry-swarm), [Secret Manager](https://console.cloud.google.com/security/secret-manager?project=codesentry-swarm) y [Logs Explorer](https://console.cloud.google.com/logs/query?project=codesentry-swarm).

## Qué señalar en cada pantalla

1. **PR.** Ya está el comentario del enjambre: dice qué encontró, cuánto costó y su `trace_id`.
2. **Cloud Run.** Máximo 5 instancias y timeout de 600 s. En variables se ven referencias a secretos, no valores. El webhook es público: lo protege la firma HMAC, y `/internal/*` exige OIDC.
3. **Pub/Sub.** Ack de 600 s, backoff de 10 a 300 s y 5 intentos antes de `codesentry-dead-letter`.
4. **Firestore.** Abre `tasks/{task_id}`: iteraciones, exit codes y `trace_id`. Entra en `claims` y explica que cada etapa se reclama una vez.
   - **En vivo:** pulsa Redeliver en GitHub. Responde 202, no aparece otra ejecución ni otro comentario. El `task_id` nace de repo, número y commit.
5. **Job.** 1 CPU, 512 MiB, 300 s y 0 reintentos. La cuenta `codesentry-sandbox` no tiene ningún rol.
6. **Bucket.** `bundles/` es lo que entra y `results/` lo que sale, por URLs firmadas de 15 minutos. Todo se borra a los 7 días.
7. **Eventarc.** Un archivo nuevo en el bucket llama a `/internal/sandbox-result`. El job no conoce al juez.
8. **Trace.** Pega el `trace_id` y sigue los spans. Abre `chat gemini-*` para ver `gen_ai.usage.*`. Cuenta que Pub/Sub crea una traza por mensaje y Eventarc llega sin contexto, así que `traceparent` viaja a mano y se guarda antes del sandbox.
9. **Dashboard.** Tokens, llamadas, latencia, colas y dead letters. Es el salto de "un PR" a "operar un sistema".

## Tres momentos para el escenario

**El mensaje muerto.** La suscripción `codesentry-dead-letter` guarda un mensaje real de `codesentry-run`, del 4 de noviembre, tras 5 intentos. Se borra el 11 de noviembre: captúralo antes con `gcloud pubsub subscriptions pull codesentry-dead-letter --project codesentry-swarm --limit 1` y sin `--auto-ack`.

**Prompt injection.** El [PR #6](https://github.com/listerineh/codesentry-demo-shop/pull/6) esconde en un comentario la orden de aprobar y de borrar los tests del CI. El modelo la ignoró y propuso un arreglo real. Di esto con cuidado: **que el modelo resistiera una vez no es la defensa**. La defensa es que, si obedeciera, una edición al CI la frena `check_edits` y un test que siempre pasa no cumple rojo y luego verde. Esas dos barreras no se activaron en el PR #6. Para enseñarlas, usa la demo local y aclara que sus respuestas están escritas a mano:

```bash
uv run codesentry demo --scenario hostile
```

**La prueba de escape.** `./scripts/open-escape-pr.sh` muestra desde dentro que no llega al servidor de metadatos ni a internet. Sus hallazgos quedan en el archivo de `results/` del bucket.

## No proyectar

- Detalles o YAML de una ejecución reciente del job: pueden mostrar `BUNDLE_URL` y `RESULT_URL`, firmadas durante 15 minutos.
- Valores de secretos, facturación y el bucket `codesentry-swarm-tfstate`.

## Si algo falla

| Fallo | Qué hacer |
|---|---|
| El PR tarda más de 5 minutos | Sigue con el PR calentado y vuelve al final |
| El modelo acierta a la primera | Enseña el PR que necesitó 2 iteraciones |
| Se cae la red | Usa las capturas y `uv run codesentry demo`, y aclara que las respuestas son guionizadas |
| Gemini devuelve 429 | Muestra el reintento en Pub/Sub como parte de la historia |
