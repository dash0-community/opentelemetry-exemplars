# OpenTelemetry exemplars demo with Prometheus and Jaeger

A runnable pipeline that carries an exemplar from an OpenTelemetry SDK all the
way to the trace that produced it: **Python app → OpenTelemetry Collector →
Prometheus → Jaeger**.

Prometheus shows you the exemplar on the graph. Jaeger shows you the request
behind it.

Companion to [Dash0's guide to OpenTelemetry exemplars](https://www.dash0.com/guides/opentelemetry-exemplars).

## Prerequisites

- Docker and Docker Compose
- Ports 8080, 8889, 9090 and 16686 free

No account, API key, or vendor sign-up is needed.

## Run it

```bash
docker compose up -d --build
```

Give it about a minute to build the image and accumulate data. The app generates
its own traffic, with roughly one request in eight made deliberately slow.

Then:

1. Open the Prometheus graph view at <http://localhost:9090/graph>.
2. Query `http_server_request_duration_seconds_bucket` and switch to the
   **Graph** tab.
3. Click **Show exemplars**. Diamond markers appear beneath the series.
4. Click a marker. The **Trace exemplar:** panel shows its `trace_id` and
   `span_id`, alongside the series the exemplar belongs to.
5. Open that trace in Jaeger: `http://localhost:16686/trace/<trace_id>`.

You're now looking at the single request responsible for one measurement in an
aggregate.

Generate an extra request yourself at any time:

```bash
curl http://localhost:8080/checkout
```

## Stop it

```bash
docker compose down
```

## License

Apache 2.0
