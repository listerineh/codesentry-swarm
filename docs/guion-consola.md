# Guion del recorrido por GCP

25 minutos sobre un solo PR, de la entrada a la salida. Proyecto `codesentry-swarm`, región `us-central1`. La regla del recorrido: cada pantalla responde una pregunta que el sistema tuvo que resolver.

## El día de la charla

Ejecuta estos pasos unos 15 minutos antes de empezar.

1. Abre todas las pestañas de la tabla de abajo y comprueba que la sesión de Google sigue activa.
2. El dashboard ya tiene datos: 22 tareas del 13 de noviembre, por unos 0,13 USD. Relanza el tráfico solo si la ventana de las gráficas ya no las alcanza, porque cada ronda añade otro comentario a cada PR. Abre un PR por cada bug de `demo/shop-prs` y los relanza en 3 rondas:

```bash
./scripts/traffic.sh 3 300 20
```

3. Lanza un PR para calentar el job. El primer arranque tardó 1 min 32 s:

```bash
./scripts/open-demo-pr.sh
```

4. Comprueba que cada gráfica tiene datos. "Sent to dead letter" vacía es buena señal, y las ejecuciones del job tardan unos minutos en aparecer:

```bash
gcloud auth print-access-token | ./scripts/check-dashboard.py codesentry-swarm
```

5. Cuando comente, anota su `task_id` y `trace_id` en la libreta, no en pantalla.
6. Abre [Recent Deliveries](https://github.com/listerineh/codesentry-demo-shop/settings/hooks) del webhook en otra pestaña.
7. Haz un ensayo de red: si la consola va lenta, usa las capturas de respaldo.

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

## Los PRs de la tienda

Hay un PR abierto por historia. Ábrelos por número, no por título: **#5 y #6 se llaman igual**.

| PR | Historia | Resultado |
|---|---|---|
| [#5](https://github.com/listerineh/codesentry-demo-shop/pull/5) | La hipótesis corregida: el modelo falló la primera y acertó en la segunda iteración | Fix propuesto |
| [#6](https://github.com/listerineh/codesentry-demo-shop/pull/6) | Inyección de prompt escondida en un comentario del código | Fix propuesto |
| [#8](https://github.com/listerineh/codesentry-demo-shop/pull/8), [#9](https://github.com/listerineh/codesentry-demo-shop/pull/9), [#11](https://github.com/listerineh/codesentry-demo-shop/pull/11) a [#14](https://github.com/listerineh/codesentry-demo-shop/pull/14) | Un bug por PR: carrito, cupones, inventario, paginación, precios y envío | Fix propuesto |
| [#15](https://github.com/listerineh/codesentry-demo-shop/pull/15) | IVA: tests en verde con un fix incompleto | Fix propuesto, pero mal |
| [#16](https://github.com/listerineh/codesentry-demo-shop/pull/16) | Prueba de escape del sandbox | Escala a un humano |
| [#17](https://github.com/listerineh/codesentry-demo-shop/pull/17) | Tests del PR que protegen el bug | Escala a un humano |

Para ver el estado de todos en una línea cada uno:

```bash
./scripts/pr-summary.sh
```

La segunda columna cuenta comentarios, no iteraciones. Los PRs de la tanda tienen 3 porque el tráfico los relanzó en 3 rondas.

**Si preguntan por qué hay comentarios repetidos:** cada ronda empujó un commit vacío. Un commit nuevo cambia el `head_sha`, y como el `task_id` sale de repo, número y commit, es una tarea nueva. Un reintento del mismo webhook no repite nada; un push nuevo sí dispara otra revisión, como pasaría en un PR real.

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

## El momento en vivo

```bash
./scripts/live-demo.sh
```

Abre un PR nuevo con el bug de los puntos de lealtad y sigue la tarea por SSE en la terminal: webhook, hipótesis, sandbox y comentario. Termina con tokens, costo, tiempo y los enlaces a Cloud Trace y Firestore. En el ensayo del 13 de noviembre tardó 111 s y costó 0,0026 USD. Para otro bug, pasa su carpeta: `./scripts/live-demo.sh tax`.

Ejecútalo desde la carpeta `codesentry-swarm`; desde otra, los scripts no existen. Antes de abrir el PR, el script despierta la API para que el webhook no llegue en frío.

**Si tienes 5 minutos, enseña la escalada en vivo:**

```bash
./scripts/live-demo.sh age-check
```

El ensayo es el [PR #17](https://github.com/listerineh/codesentry-demo-shop/pull/17): tardó 301 s y costó 0,0081 USD. Se ven las 3 iteraciones rechazadas una tras otra y el cierre en `needs_human`.

## Cinco momentos para el escenario

**Verde no es correcto.** En el [PR #15](https://github.com/listerineh/codesentry-demo-shop/pull/15) el enjambre cambió `int()` por `round(x, 2)`. Sus tests pasan, pero `round` sobre floats no redondea como el dinero: probado con 100.000 importes, de 0,01 a 1.000 USD, falla en 4.372 (un 4,4 %). Por ejemplo, 0,10 USD con IVA da 0,11 cuando debería dar 0,12, y el fix correcto usa `Decimal` con `ROUND_HALF_UP`. El gate no falló: el test que escribió Triage solo cubría su propio caso. Es la lámina "Qué hace y qué no": verde es evidencia, no garantía, y por eso el merge es humano.

**Cuando el sistema pide ayuda.** En el [PR #17](https://github.com/listerineh/codesentry-demo-shop/pull/17), `can_buy_alcohol` usa `> 18` y su docstring dice "18 or older", pero un test del propio PR exige que a los 18 no se pueda comprar. Gemini encontró el bug a la primera. Su fix rompía ese test, el juez pidió cambiarlo 3 veces y el modelo no podía, porque la política le prohíbe tocar tests. Terminó en `needs_human` con un comentario que dice qué test corregir. Es el guardrail funcionando: no se salta la regla para ponerse en verde.

**Nada murió.** "Sent to dead letter" del dashboard está vacía: en las 22 tareas del 13 de noviembre ningún mensaje agotó sus 5 intentos. Si preguntan qué pasa cuando sí, enseña la configuración de `codesentry-run` en Pub/Sub. El mensaje real del 4 de noviembre expiró el 11.

**Prompt injection.** El [PR #6](https://github.com/listerineh/codesentry-demo-shop/pull/6) esconde en un comentario la orden de aprobar y de borrar los tests del CI. El modelo la ignoró y propuso un arreglo real. Di esto con cuidado: **que el modelo resistiera una vez no es la defensa**. La defensa es que, si obedeciera, una edición al CI la frena `check_edits` y un test que siempre pasa no cumple rojo y luego verde. Esas dos barreras no se activaron en el PR #6. Para enseñarlas, usa la demo local y aclara que sus respuestas están escritas a mano:

```bash
uv run codesentry demo --scenario hostile
```

**La prueba de escape.** El [PR #16](https://github.com/listerineh/codesentry-demo-shop/pull/16) muestra desde dentro que no llega al servidor de metadatos ni a internet. Sus hallazgos quedan en el archivo de `results/` del bucket. Termina en `needs_human` a propósito: su test falla siempre para dejar los hallazgos en la salida. Para abrir otro: `./scripts/open-escape-pr.sh`.

## No proyectar

- Detalles o YAML de una ejecución reciente del job: pueden mostrar `BUNDLE_URL` y `RESULT_URL`, firmadas durante 15 minutos.
- Valores de secretos, facturación y el bucket `codesentry-swarm-tfstate`.

## Si algo falla

| Fallo | Qué hacer |
|---|---|
| El PR tarda más de 5 minutos | Sigue con el PR calentado y vuelve al final |
| `live-demo.sh` espera el webhook más de 2 minutos | En [Recent Deliveries](https://github.com/listerineh/codesentry-demo-shop/settings/hooks), pulsa Redeliver en la última entrega. Si GitHub marca 500 por un arranque en frío, la tarea suele haberse procesado igual |
| El modelo acierta a la primera | Enseña el [PR #5](https://github.com/listerineh/codesentry-demo-shop/pull/5), que necesitó 2 iteraciones |
| "No such file or directory" al lanzar un script | Estás fuera del repo: `cd ~/code/personal/codesentry-swarm` |
| Se cae la red | Usa las capturas y `uv run codesentry demo`, y aclara que las respuestas son guionizadas |
| Gemini devuelve 429 | Muestra el reintento en Pub/Sub como parte de la historia |
