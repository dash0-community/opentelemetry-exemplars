"""A minimal service that records a latency histogram inside active spans.

Every measurement is recorded while a span is current, which is the only way the
SDK can attach trace context to it. Roughly one request in eight is slow, so the
histogram's upper buckets stay populated and have exemplars to point at.
"""

import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import (
    Histogram,
    MeterProvider,
)
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import (
    ExplicitBucketHistogramAggregation,
    View,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "checkout")
ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
EXPORT_INTERVAL_MS = int(os.environ.get("EXPORT_INTERVAL_MS", "5000"))

resource = Resource.create({"service.name": SERVICE_NAME})

trace.set_tracer_provider(TracerProvider(resource=resource))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, insecure=True))
)

# The SDK's default bucket boundaries are sized for milliseconds. This histogram
# is in seconds, so without a View every measurement lands in one bucket and only
# that bucket ever carries an exemplar.
#
# exemplar_reservoir_factory takes the aggregation class and returns a reservoir
# *builder*, not a reservoir. Returning the class is enough: the histogram
# aggregation calls it with boundaries=[...] supplied from the aggregation above.
seconds_buckets = View(
    instrument_type=Histogram,
    instrument_name="http.server.request.duration",
    aggregation=ExplicitBucketHistogramAggregation(
        boundaries=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    ),
)

metrics.set_meter_provider(
    MeterProvider(
        resource=resource,
        views=[seconds_buckets],
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=ENDPOINT, insecure=True),
                export_interval_millis=EXPORT_INTERVAL_MS,
            )
        ],
    )
)

tracer = trace.get_tracer("demo.checkout")
meter = metrics.get_meter("demo.checkout")

# Synchronous instrument. Asynchronous (observable) instruments never produce
# exemplars, because their callbacks do not run inside a span.
latency = meter.create_histogram(
    name="http.server.request.duration",
    unit="s",
    description="Request duration",
)


def handle(route: str) -> float:
    """Serve a request as a small trace, then record the measurement in the root span.

    The measurement is recorded after the child spans have closed but while the
    server span is still current, so the exemplar points at the root of the trace
    rather than at whichever leaf happened to be open.
    """
    started = time.monotonic()

    with tracer.start_as_current_span(
        "GET " + route, kind=trace.SpanKind.SERVER
    ) as root:
        slow = random.random() < 0.125
        root.set_attribute("http.request.method", "GET")
        root.set_attribute("http.route", route)
        root.set_attribute("demo.slow_path", slow)

        with tracer.start_as_current_span("auth.verify_token"):
            time.sleep(random.uniform(0.002, 0.008))

        with tracer.start_as_current_span("cart.load") as cart:
            items = random.randint(1, 7)
            cart.set_attribute("cart.item_count", items)

            with tracer.start_as_current_span(
                "SELECT cart_items", kind=trace.SpanKind.CLIENT
            ) as db:
                db.set_attribute("db.system.name", "postgresql")
                db.set_attribute("db.namespace", "shop")
                db.set_attribute(
                    "db.query.text", "SELECT * FROM cart_items WHERE id = $1"
                )
                # The slow path is a slow query, which is the thing you would
                # actually want the exemplar to take you to.
                time.sleep(
                    random.uniform(1.4, 2.8) if slow else random.uniform(0.004, 0.05)
                )

        with tracer.start_as_current_span("pricing.calculate"):
            time.sleep(random.uniform(0.002, 0.02))

        if route == "/checkout":
            with tracer.start_as_current_span(
                "payment.authorize", kind=trace.SpanKind.CLIENT
            ) as pay:
                pay.set_attribute("peer.service", "payments")
                time.sleep(random.uniform(0.01, 0.06))

        seconds = time.monotonic() - started
        root.set_attribute("http.response.status_code", 200)

        # Recorded while the root span is still current, so trace_based keeps it
        # and the reservoir above marks this span.
        latency.record(seconds, {"http.route": route, "http.response.status_code": 200})
        return seconds


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        seconds = handle(self.path)
        body = f"took {seconds:.3f}s\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def background_load():
    """Keep the histogram populated so the panel has something to show."""
    routes = ["/checkout", "/cart", "/search"]
    while True:
        handle(random.choice(routes))
        time.sleep(0.3)


if __name__ == "__main__":
    threading.Thread(target=background_load, daemon=True).start()
    print(f"{SERVICE_NAME} listening on :8080, exporting to {ENDPOINT}", flush=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
