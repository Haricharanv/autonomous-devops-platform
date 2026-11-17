"""Metrics Collector module for the AI Decision Engine.

This module provides the MetricsCollector class to query Prometheus for Kubernetes
cluster and application performance metrics including CPU, memory, error rates,
request throughput, pod restarts, replica counts, and latency percentiles.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class MetricsCollectorError(Exception):
    """Custom exception raised for metrics collection failures."""
    pass


class MetricsCollector:
    """Collects system, container, and application metrics from Prometheus.

    Attributes:
        prometheus_url: Base URL of the Prometheus server (e.g., http://localhost:9090).
        timeout: Network request timeout in seconds.
        session: Configured requests.Session instance with retry logic.
    """

    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        """Initializes the MetricsCollector with a Prometheus URL and connection session.

        Args:
            prometheus_url: Base URL of the Prometheus server.
            timeout: HTTP request timeout in seconds.
            max_retries: Maximum number of connection retries for transient failures.
            backoff_factor: Multiplier for exponential backoff on retries.
        """
        self.prometheus_url = prometheus_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

        # Configure resilient HTTP adapter with connection pooling and retries
        # Fail fast on connection refused (connect=0) while retrying transient server errors
        retry_strategy = Retry(
            total=max_retries,
            connect=0,
            read=max_retries,
            status=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        logger.info(
            "MetricsCollector initialized with Prometheus URL: %s (timeout=%ss)",
            self.prometheus_url,
            self.timeout,
        )

    def query(self, promql_query: str) -> Dict[str, Any]:
        """Executes an instant PromQL query against Prometheus.

        Args:
            promql_query: The PromQL query string to evaluate.

        Returns:
            Dictionary containing the parsed JSON response from Prometheus API.

        Raises:
            MetricsCollectorError: If network request fails or Prometheus returns an error status.
        """
        endpoint = f"{self.prometheus_url}/api/v1/query"
        params = {"query": promql_query}

        logger.debug("Executing PromQL instant query: %s", promql_query)
        try:
            response = self.session.get(endpoint, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                error_msg = data.get("error", "Unknown Prometheus server error")
                error_type = data.get("errorType", "ServerError")
                raise MetricsCollectorError(f"Prometheus query error [{error_type}]: {error_msg}")

            return data

        except requests.exceptions.Timeout as exc:
            logger.warning("Prometheus query timed out for query '%s': %s", promql_query, exc)
            raise MetricsCollectorError(f"Prometheus query timed out: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            logger.warning("Prometheus query connection error for query '%s': %s", promql_query, exc)
            raise MetricsCollectorError(f"Prometheus connection error: {exc}") from exc
        except ValueError as exc:
            logger.error("Failed to parse JSON from Prometheus response for query '%s': %s", promql_query, exc)
            raise MetricsCollectorError(f"Invalid JSON response: {exc}") from exc

    def query_range(
        self,
        promql_query: str,
        start: float,
        end: float,
        step: Union[str, int] = "30s",
    ) -> Dict[str, Any]:
        """Executes a range PromQL query against Prometheus.

        Args:
            promql_query: The PromQL query string to evaluate.
            start: Start timestamp in epoch seconds.
            end: End timestamp in epoch seconds.
            step: Query resolution step width (e.g., '30s', 60).

        Returns:
            Dictionary containing the parsed JSON response from Prometheus API.

        Raises:
            MetricsCollectorError: If network request fails or Prometheus returns an error.
        """
        endpoint = f"{self.prometheus_url}/api/v1/query_range"
        params = {
            "query": promql_query,
            "start": str(start),
            "end": str(end),
            "step": str(step),
        }

        logger.debug("Executing PromQL range query: %s (start=%s, end=%s, step=%s)", promql_query, start, end, step)
        try:
            response = self.session.get(endpoint, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                error_msg = data.get("error", "Unknown Prometheus server error")
                error_type = data.get("errorType", "ServerError")
                raise MetricsCollectorError(f"Prometheus range query error [{error_type}]: {error_msg}")

            return data

        except requests.exceptions.RequestException as exc:
            logger.warning("Prometheus range query failed for '%s': %s", promql_query, exc)
            raise MetricsCollectorError(f"Prometheus range query connection error: {exc}") from exc
        except ValueError as exc:
            logger.error("Failed to parse JSON response for range query '%s': %s", promql_query, exc)
            raise MetricsCollectorError(f"Invalid JSON response: {exc}") from exc

    def _extract_scalar_or_vector_value(
        self,
        response_data: Optional[Dict[str, Any]],
        default: float = 0.0,
    ) -> float:
        """Extracts a single numeric float value from Prometheus instant query response.

        Args:
            response_data: The JSON dictionary returned by query().
            default: Default value to return if no data points are present or value is invalid.

        Returns:
            Parsed float value or default if unavailable.
        """
        if not response_data or response_data.get("status") != "success":
            return default

        data = response_data.get("data", {})
        result_type = data.get("resultType")
        results = data.get("result", [])

        if not results:
            return default

        try:
            if result_type == "scalar":
                # scalar result format: [timestamp, "value"]
                val = float(results[1])
            elif result_type == "vector":
                # vector result format: [{"metric": {...}, "value": [timestamp, "value"]}]
                val = float(results[0]["value"][1])
            else:
                return default

            if math.isnan(val) or math.isinf(val):
                return default
            return val
        except (IndexError, KeyError, ValueError, TypeError) as exc:
            logger.debug("Could not parse scalar/vector value from Prometheus result: %s", exc)
            return default

    def get_cpu_usage(self, namespace: str, deployment: str) -> float:
        """Gets average CPU usage percentage across pods in the specified deployment.

        Calculates CPU utilization relative to limits, or falls back to average CPU cores
        rate if limits are not set.

        Args:
            namespace: Kubernetes namespace where the deployment is located.
            deployment: Name of the deployment.

        Returns:
            CPU usage as a percentage (0.0 to 100.0+).
        """
        # Primary query: CPU usage relative to resource limits
        query_with_limit = (
            f'(sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", container!="", container!="POD"}}[2m])) / '
            f'sum(kube_pod_container_resource_limits{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", resource="cpu"}} > 0)) * 100'
        )

        try:
            data = self.query(query_with_limit)
            val = self._extract_scalar_or_vector_value(data, default=-1.0)
            if val >= 0.0:
                return round(val, 2)
        except MetricsCollectorError as exc:
            logger.debug("Primary CPU query with limits failed: %s; trying fallback", exc)

        # Fallback query: Average container CPU usage percentage across pods
        fallback_query = (
            f'avg(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", container!="", container!="POD"}}[2m])) * 100'
        )

        try:
            data = self.query(fallback_query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return round(max(0.0, val), 2)
        except MetricsCollectorError as exc:
            logger.error("Failed to collect CPU usage for %s/%s: %s", namespace, deployment, exc)
            return 0.0

    def get_memory_usage(self, namespace: str, deployment: str) -> float:
        """Gets average memory usage percentage across pods in the specified deployment.

        Calculates working set memory relative to configured memory limits, or falls back
        to allocatable node memory fraction.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            Memory usage as a percentage (0.0 to 100.0).
        """
        # Primary query: Working set memory relative to container limit
        query_with_limit = (
            f'(sum(container_memory_working_set_bytes{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", container!="", container!="POD"}}) / '
            f'sum(kube_pod_container_resource_limits{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", resource="memory"}} > 0)) * 100'
        )

        try:
            data = self.query(query_with_limit)
            val = self._extract_scalar_or_vector_value(data, default=-1.0)
            if val >= 0.0:
                return round(min(100.0, val), 2)
        except MetricsCollectorError as exc:
            logger.debug("Primary memory query with limits failed: %s; trying fallback", exc)

        # Fallback query: Working set memory relative to node allocatable memory
        fallback_query = (
            f'(sum(container_memory_working_set_bytes{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", container!="", container!="POD"}}) / '
            f'sum(kube_node_status_allocatable{{resource="memory"}})) * 100'
        )

        try:
            data = self.query(fallback_query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return round(max(0.0, min(100.0, val)), 2)
        except MetricsCollectorError as exc:
            logger.error("Failed to collect memory usage for %s/%s: %s", namespace, deployment, exc)
            return 0.0

    def get_error_rate(self, namespace: str, deployment: str) -> float:
        """Gets HTTP 5xx error rate percentage for the deployment.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            5xx error rate as a percentage of total requests (0.0 to 100.0).
        """
        # Query 5xx status codes divided by total requests
        query = (
            f'(sum(rate(http_requests_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", status=~"5.." }}[2m])) / '
            f'sum(rate(http_requests_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*"}}[2m]))) * 100'
        )

        try:
            data = self.query(query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return round(max(0.0, min(100.0, val)), 2)
        except MetricsCollectorError as exc:
            # Also try alternative query where label is 'code' instead of 'status'
            alt_query = (
                f'(sum(rate(http_requests_total{{namespace="{namespace}", '
                f'pod=~"{deployment}-.*", code=~"5.." }}[2m])) / '
                f'sum(rate(http_requests_total{{namespace="{namespace}", '
                f'pod=~"{deployment}-.*"}}[2m]))) * 100'
            )
            try:
                data = self.query(alt_query)
                val = self._extract_scalar_or_vector_value(data, default=0.0)
                return round(max(0.0, min(100.0, val)), 2)
            except MetricsCollectorError:
                logger.error("Failed to collect error rate for %s/%s: %s", namespace, deployment, exc)
                return 0.0

    def get_request_rate(self, namespace: str, deployment: str) -> float:
        """Gets the current incoming request rate in requests per second.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            Requests per second (float).
        """
        query = (
            f'sum(rate(http_requests_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*"}}[2m]))'
        )

        try:
            data = self.query(query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return round(max(0.0, val), 2)
        except MetricsCollectorError as exc:
            logger.error("Failed to collect request rate for %s/%s: %s", namespace, deployment, exc)
            return 0.0

    def get_pod_restart_count(self, namespace: str, deployment: str) -> Dict[str, int]:
        """Gets container restart counts grouped per pod for the deployment.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            Dictionary mapping pod names to their respective total restart counts.
        """
        query = (
            f'sum by (pod) (kube_pod_container_status_restarts_total{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*"}})'
        )

        restarts_map: Dict[str, int] = {}
        try:
            data = self.query(query)
            if data.get("status") == "success":
                for item in data.get("data", {}).get("result", []):
                    pod_name = item.get("metric", {}).get("pod")
                    value_pair = item.get("value", [])
                    if pod_name and len(value_pair) > 1:
                        try:
                            restarts_map[pod_name] = int(float(value_pair[1]))
                        except (ValueError, TypeError):
                            continue
            return restarts_map
        except MetricsCollectorError as exc:
            logger.error("Failed to collect pod restart counts for %s/%s: %s", namespace, deployment, exc)
            return restarts_map

    def get_pod_count(self, namespace: str, deployment: str) -> int:
        """Gets the current number of running pods/replicas for the deployment.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            Integer replica count.
        """
        # Primary query: count running pods
        primary_query = (
            f'count(kube_pod_status_phase{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*", phase="Running"}})'
        )

        try:
            data = self.query(primary_query)
            val = self._extract_scalar_or_vector_value(data, default=-1.0)
            if val >= 0:
                return int(val)
        except MetricsCollectorError as exc:
            logger.debug("Primary pod count query failed: %s; trying deployment replicas metric", exc)

        # Fallback query: deployment status replicas
        fallback_query = (
            f'kube_deployment_status_replicas{{namespace="{namespace}", deployment="{deployment}"}}'
        )

        try:
            data = self.query(fallback_query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return max(0, int(val))
        except MetricsCollectorError as exc:
            logger.error("Failed to collect pod count for %s/%s: %s", namespace, deployment, exc)
            return 0

    def get_latency_percentile(
        self,
        namespace: str,
        deployment: str,
        percentile: float = 0.95,
    ) -> float:
        """Gets request latency at the given percentile (e.g. p95) in seconds.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.
            percentile: Percentile fraction between 0.0 and 1.0 (default 0.95).

        Returns:
            Latency in seconds (float).
        """
        query = (
            f'histogram_quantile({percentile}, '
            f'sum(rate(http_request_duration_seconds_bucket{{namespace="{namespace}", '
            f'pod=~"{deployment}-.*"}}[2m])) by (le))'
        )

        try:
            data = self.query(query)
            val = self._extract_scalar_or_vector_value(data, default=0.0)
            return round(max(0.0, val), 4)
        except MetricsCollectorError as exc:
            logger.error("Failed to collect latency percentile for %s/%s: %s", namespace, deployment, exc)
            return 0.0

    def get_all_metrics(self, namespace: str, deployment: str) -> Dict[str, Any]:
        """Collects all performance and health metrics for the deployment in one call.

        Args:
            namespace: Kubernetes namespace.
            deployment: Name of the deployment.

        Returns:
            Dictionary containing a complete snapshot of metrics:
                timestamp: Unix epoch timestamp of collection.
                cpu_usage: Average CPU % across pods.
                memory_usage: Average memory % across pods.
                error_rate: 5xx error rate %.
                request_rate: Incoming requests per second.
                pod_restarts: Dictionary mapping pod names to restart counts.
                max_pod_restarts: Maximum restart count among all pods.
                pod_count: Current count of running replicas.
                latency_p95: 95th percentile request latency in seconds.
        """
        logger.info("Collecting comprehensive metrics for deployment '%s' in namespace '%s'", deployment, namespace)
        timestamp = time.time()

        cpu_usage = self.get_cpu_usage(namespace, deployment)
        memory_usage = self.get_memory_usage(namespace, deployment)
        error_rate = self.get_error_rate(namespace, deployment)
        request_rate = self.get_request_rate(namespace, deployment)
        pod_restarts = self.get_pod_restart_count(namespace, deployment)
        max_restarts = max(pod_restarts.values()) if pod_restarts else 0
        pod_count = self.get_pod_count(namespace, deployment)
        latency_p95 = self.get_latency_percentile(namespace, deployment, percentile=0.95)

        metrics_snapshot = {
            "timestamp": timestamp,
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "error_rate": error_rate,
            "request_rate": request_rate,
            "pod_restarts": pod_restarts,
            "max_pod_restarts": max_restarts,
            "pod_count": pod_count,
            "latency_p95": latency_p95,
        }

        logger.debug("Metrics snapshot collected: %s", metrics_snapshot)
        return metrics_snapshot
