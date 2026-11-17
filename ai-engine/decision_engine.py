"""AI Decision Engine module for the Autonomous DevOps Deployment Platform.

This module acts as the core brain of the platform. It evaluates real-time Prometheus
metrics against operational thresholds and statistical/machine-learning anomaly models,
applies deterministic and heuristic decision rules, manages action cooldowns, and routes
actions to the ActionExecutor and Notifier.
"""

from __future__ import annotations

import collections
import datetime
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Union

import yaml

from action_executor import ActionExecutor, ActionResult
from anomaly_detector import AnomalyDetector
from metrics_collector import MetricsCollector, MetricsCollectorError
from notifier import Notifier

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """Represents an intelligent remediation or scaling decision made by the engine.

    Attributes:
        action: The action to execute ('scale_up', 'scale_down', 'rollback', 'restart', 'none').
        reason: Plain-text explanation of why this decision was reached.
        priority: Urgency level ('critical', 'high', 'medium', 'low', 'info').
        timestamp: ISO 8601 formatted timestamp of when decision was formulated.
        metrics_snapshot: Copy of the metrics that triggered the decision.
        details: Additional contextual parameters (e.g., target replica count, revision).
    """

    action: str
    reason: str
    priority: str
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    metrics_snapshot: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)


class DecisionEngineError(Exception):
    """Custom exception raised for operational errors in the Decision Engine."""
    pass


class DecisionEngine:
    """Core autonomous decision engine driving proactive cluster operations.

    Attributes:
        config_path: Path to configuration YAML file.
        config: Loaded configuration dictionary.
        dry_run: Whether actions should be simulated without cluster modification.
        metrics_collector: MetricsCollector instance for Prometheus queries.
        action_executor: ActionExecutor instance for executing system changes.
        notifier: Notifier instance for Slack and audit log dispatches.
        anomaly_detector: Optional AnomalyDetector instance for ML-based outlier checks.
        last_action_time: Map of action names to epoch timestamp of last execution.
        action_history: Rolling list of past executed decisions and their results.
        metrics_history: Deque of historical metric snapshots for trend analysis.
        running: Flag governing continuous operation loop.
    """

    def __init__(
        self,
        config_path: str = "config.yaml",
        dry_run: bool = False,
    ) -> None:
        """Initializes the DecisionEngine by loading config and initializing sub-components.

        Args:
            config_path: Path to configuration YAML file.
            dry_run: If True, execute actions in simulation mode.

        Raises:
            DecisionEngineError: If config file is missing or invalid.
        """
        self.config_path = config_path
        self.dry_run = dry_run
        self.config = self._load_config(config_path)

        # Extract config sections
        self.prometheus_cfg = self.config.get("prometheus", {})
        self.thresholds_cfg = self.config.get("thresholds", {})
        self.scaling_cfg = self.config.get("scaling", {})
        self.actions_cfg = self.config.get("actions", {})
        self.ml_cfg = self.config.get("ml", {})

        self.namespace = self.actions_cfg.get("namespace", "devops-platform")
        self.deployment = self.actions_cfg.get("deployment", "devops-demo-app")

        # Initialize sub-components
        prom_url = self.prometheus_cfg.get("url", "http://localhost:9090")
        self.metrics_collector = MetricsCollector(prometheus_url=prom_url)
        self.action_executor = ActionExecutor(self.config, dry_run=self.dry_run)
        self.notifier = Notifier(self.config)

        # Initialize ML anomaly detector if configured
        self.ml_enabled = bool(self.ml_cfg.get("enabled", False))
        self.anomaly_detector: Optional[AnomalyDetector] = None
        if self.ml_enabled:
            model_path = self.ml_cfg.get("anomaly_model_path", "models/anomaly_detector.pkl")
            self.anomaly_detector = AnomalyDetector(model_path=model_path)
            logger.info("ML Anomaly Detection is enabled (model_path=%s)", model_path)
        else:
            logger.info("ML Anomaly Detection is disabled by configuration.")

        # Internal state tracking
        self.last_action_time: Dict[str, float] = {}
        self.action_history: List[Dict[str, Any]] = []
        # Store up to 2880 entries (24 hours at 30s query interval)
        self.metrics_history: Deque[Dict[str, Any]] = collections.deque(maxlen=2880)
        self.running: bool = False

        logger.info(
            "DecisionEngine initialized successfully for deployment '%s/%s' (dry_run=%s)",
            self.namespace,
            self.deployment,
            self.dry_run,
        )

    def _load_config(self, path: str) -> Dict[str, Any]:
        """Loads and parses the YAML configuration file.

        Args:
            path: Path to configuration file.

        Returns:
            Dictionary with parsed configuration.

        Raises:
            DecisionEngineError: If file not found or invalid YAML syntax.
        """
        cfg_file = Path(path)
        if not cfg_file.is_file():
            raise DecisionEngineError(f"Configuration file not found: {path}")

        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
            logger.debug("Loaded configuration from %s", path)
            return config_data
        except yaml.YAMLError as exc:
            raise DecisionEngineError(f"Error parsing configuration YAML in {path}: {exc}") from exc
        except OSError as exc:
            raise DecisionEngineError(f"Failed to read configuration file {path}: {exc}") from exc

    def check_cooldown(self, action_type: str) -> bool:
        """Checks whether the specified action type has satisfied its cooldown period.

        Args:
            action_type: Action name ('scale_up', 'scale_down', 'rollback', 'restart').

        Returns:
            True if cooldown has expired and action is permissible, False if in cooldown.
        """
        if action_type in ("none", "no_action"):
            return True

        cooldown_period = float(self.scaling_cfg.get("cooldown_period", 300))
        now = time.time()
        last_time = self.last_action_time.get(action_type, 0.0)
        elapsed = now - last_time

        if elapsed < cooldown_period:
            remaining = int(cooldown_period - elapsed)
            logger.warning(
                "Cooldown active for action '%s': %ds elapsed, %ds remaining (cooldown=%ds)",
                action_type,
                int(elapsed),
                remaining,
                int(cooldown_period),
            )
            return False

        return True

    def analyze(self, metrics: Dict[str, Any]) -> List[Decision]:
        """Analyzes a metrics snapshot against policy rules and generates necessary decisions.

        Rules Evaluated:
        - Rule 1: High CPU > cpu_high threshold for sustained period -> scale_up
        - Rule 2: Low CPU < cpu_low and replicas > min_replicas -> scale_down
        - Rule 3: Memory > memory_high threshold -> scale_up
        - Rule 4: Error rate > error_rate_high threshold -> rollback (critical priority)
        - Rule 5: Pod restarts > pod_restart_max -> restart (critical priority)
        - Rule 6: Latency p95 > latency_p95_max -> scale_up
        - Optional: Machine learning anomaly detection flag

        Args:
            metrics: Current metrics dictionary collected from Prometheus.

        Returns:
            List of Decision objects ordered by priority (critical -> high -> low).
        """
        decisions: List[Decision] = []

        cpu = float(metrics.get("cpu_usage", 0.0))
        mem = float(metrics.get("memory_usage", 0.0))
        err = float(metrics.get("error_rate", 0.0))
        restarts = int(metrics.get("max_pod_restarts", 0))
        lat = float(metrics.get("latency_p95", 0.0))
        pod_count = int(metrics.get("pod_count", 0))

        # Thresholds from configuration
        cpu_high = float(self.thresholds_cfg.get("cpu_high", 80))
        cpu_low = float(self.thresholds_cfg.get("cpu_low", 20))
        mem_high = float(self.thresholds_cfg.get("memory_high", 85))
        err_high = float(self.thresholds_cfg.get("error_rate_high", 10))
        restart_max = int(self.thresholds_cfg.get("pod_restart_max", 3))
        lat_max = float(self.thresholds_cfg.get("latency_p95_max", 1.0))

        # Scaling limits
        min_replicas = int(self.scaling_cfg.get("min_replicas", 2))
        max_replicas = int(self.scaling_cfg.get("max_replicas", 10))
        scale_up_count = int(self.scaling_cfg.get("scale_up_count", 2))
        scale_down_count = int(self.scaling_cfg.get("scale_down_count", 1))

        # -------------------------------------------------------------
        # Rule 4: Critical Error Rate Check (Highest operational priority)
        # -------------------------------------------------------------
        if err > err_high:
            reason = (
                f"High error rate: {err:.1f}% (threshold: {err_high}%). "
                f"Immediate rollback triggered to restore service availability."
            )
            logger.critical("Rule 4 triggered: %s", reason)
            decisions.append(
                Decision(
                    action="rollback",
                    reason=reason,
                    priority="critical",
                    metrics_snapshot=metrics,
                    details={"error_rate": err, "threshold": err_high},
                )
            )

        # -------------------------------------------------------------
        # Rule 5: Pod Crash-Looping Check
        # -------------------------------------------------------------
        if restarts > restart_max:
            reason = (
                f"Pod crash-looping detected: max pod restarts {restarts} "
                f"exceeds threshold of {restart_max}."
            )
            logger.critical("Rule 5 triggered: %s", reason)
            decisions.append(
                Decision(
                    action="restart",
                    reason=reason,
                    priority="critical",
                    metrics_snapshot=metrics,
                    details={"max_restarts": restarts, "threshold": restart_max},
                )
            )

        # -------------------------------------------------------------
        # Scale Up Triggers: Rule 1 (CPU), Rule 3 (Memory), Rule 6 (Latency)
        # -------------------------------------------------------------
        scale_up_reasons: List[str] = []

        # Rule 1: High CPU usage (evaluate sustained load if history available)
        cpu_sustained = False
        if cpu > cpu_high:
            if len(self.metrics_history) >= 2:
                # Check recent metrics to verify sustained nature
                recent_cpus = [
                    float(m.get("cpu_usage", 0.0)) for m in list(self.metrics_history)[-3:]
                ]
                avg_recent_cpu = sum(recent_cpus) / len(recent_cpus)
                if avg_recent_cpu >= (cpu_high * 0.9):
                    cpu_sustained = True
            else:
                cpu_sustained = True

            if cpu_sustained:
                scale_up_reasons.append(f"High CPU usage: {cpu:.1f}% (threshold: {cpu_high}%)")
                logger.warning("Rule 1 triggered: Sustained CPU usage above threshold (%.1f%%)", cpu)

        # Rule 3: High memory usage
        if mem > mem_high:
            scale_up_reasons.append(f"High memory: {mem:.1f}% (threshold: {mem_high}%)")
            logger.warning("Rule 3 triggered: Memory usage above threshold (%.1f%%)", mem)

        # Rule 6: High p95 latency
        if lat > lat_max:
            scale_up_reasons.append(f"High latency: p95 {lat:.2f}s (threshold: {lat_max}s)")
            logger.warning("Rule 6 triggered: p95 latency above threshold (%.2fs)", lat)

        # Consolidate Scale Up triggers to avoid multiple scaling calls in a single turn
        if scale_up_reasons:
            if pod_count < max_replicas:
                combined_reason = " | ".join(scale_up_reasons)
                decisions.append(
                    Decision(
                        action="scale_up",
                        reason=combined_reason,
                        priority="high",
                        metrics_snapshot=metrics,
                        details={
                            "replicas_to_add": scale_up_count,
                            "current_replicas": pod_count,
                            "max_replicas": max_replicas,
                        },
                    )
                )
            else:
                logger.warning(
                    "Scale up needed (%s) but deployment is already at max_replicas (%d)",
                    ", ".join(scale_up_reasons),
                    max_replicas,
                )

        # -------------------------------------------------------------
        # Rule 2: Low CPU Scale Down Check
        # -------------------------------------------------------------
        # Scale down only when no critical issues, no scale up needed, and above min_replicas
        if not scale_up_reasons and not decisions and cpu < cpu_low:
            if pod_count > min_replicas:
                reason = f"Low CPU: {cpu:.1f}% (threshold: {cpu_low}%)"
                logger.info("Rule 2 triggered: %s. Initiating scale down.", reason)
                decisions.append(
                    Decision(
                        action="scale_down",
                        reason=reason,
                        priority="low",
                        metrics_snapshot=metrics,
                        details={
                            "replicas_to_remove": scale_down_count,
                            "current_replicas": pod_count,
                            "min_replicas": min_replicas,
                        },
                    )
                )
            else:
                logger.debug(
                    "Low CPU (%.1f%%) but pod count (%d) is already at or below min_replicas (%d)",
                    cpu,
                    pod_count,
                    min_replicas,
                )

        # -------------------------------------------------------------
        # ML Anomaly Detection Evaluation (Informational / Advisory)
        # -------------------------------------------------------------
        if self.ml_enabled and self.anomaly_detector and self.anomaly_detector.is_trained:
            prev_metrics = self.metrics_history[-1] if self.metrics_history else None
            is_anomaly, score = self.anomaly_detector.detect(
                current_metrics=metrics,
                previous_metrics=prev_metrics,
                window=list(self.metrics_history),
            )
            if is_anomaly:
                logger.warning("Unsupervised anomaly pattern detected with anomaly score: %.4f", score)
                # If no reactive rule triggered, create an advisory notification
                if not decisions:
                    decisions.append(
                        Decision(
                            action="none",
                            reason=f"ML Anomaly Detector flagged unusual traffic/system pattern (score: {score:.2f})",
                            priority="medium",
                            metrics_snapshot=metrics,
                            details={"anomaly_score": score},
                        )
                    )

        # Sort decisions by priority order
        priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        decisions.sort(key=lambda d: priority_map.get(d.priority.lower(), 5))

        return decisions

    def run_once(self) -> List[Decision]:
        """Performs a single evaluation cycle: collect, analyze, execute, and notify.

        Returns:
            List of decisions that were executed (or logged).
        """
        logger.info("Starting decision evaluation cycle for %s/%s", self.namespace, self.deployment)

        try:
            metrics = self.metrics_collector.get_all_metrics(self.namespace, self.deployment)
        except Exception as exc:
            logger.error("Failed to collect metrics during evaluation cycle: %s", exc)
            return []

        # Retain history
        self.metrics_history.append(metrics)

        # Perform periodic ML training if enabled and sufficient data exists
        if self.ml_enabled and self.anomaly_detector and not self.anomaly_detector.is_trained:
            if len(self.metrics_history) >= 5:
                try:
                    self.anomaly_detector.train(list(self.metrics_history))
                except Exception as exc:
                    logger.warning("Background ML training failed: %s", exc)

        decisions = self.analyze(metrics)

        executed_decisions: List[Decision] = []

        if not decisions:
            logger.info("All metrics within operational thresholds. System healthy; no action needed.")
            no_op = Decision(
                action="none",
                reason="All metrics within normal operational thresholds",
                priority="info",
                metrics_snapshot=metrics,
            )
            # Log action for audit completeness
            self.notifier.log_action(
                no_op,
                ActionResult(
                    success=True,
                    action="none",
                    output="System operating within thresholds.",
                ),
            )
            return executed_decisions

        for decision in decisions:
            action = decision.action

            if action in ("none", "no_action"):
                logger.info("Decision '%s' (%s) does not require active cluster execution.", action, decision.reason)
                result = ActionResult(success=True, action=action, output=decision.reason)
                self.notifier.notify(decision, result)
                executed_decisions.append(decision)
                continue

            # Verify cooldown
            if not self.check_cooldown(action):
                logger.warning(
                    "Skipping action '%s' due to active cooldown window. Reason: %s",
                    action,
                    decision.reason,
                )
                continue

            # Execute action
            logger.info("Executing decision: action='%s' (priority='%s', reason='%s')", action, decision.priority, decision.reason)
            result = self.action_executor.execute(decision)

            # Update state
            self.last_action_time[action] = time.time()
            self.action_history.append({
                "decision": decision,
                "result": result,
                "executed_at": time.time(),
            })

            # Send notifications (Slack and log file)
            self.notifier.notify(decision, result)
            executed_decisions.append(decision)

        return executed_decisions

    def run(self, interval: Optional[int] = None) -> None:
        """Starts the main continuous autonomous operational loop.

        Runs run_once() every interval seconds and handles SIGTERM/SIGINT signals
        for graceful termination.

        Args:
            interval: Polling cycle sleep duration in seconds (defaults to config value).
        """
        polling_interval = interval or int(self.prometheus_cfg.get("query_interval", 30))
        self.running = True

        def _signal_handler(signum: int, frame: Any) -> None:
            sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
            logger.info("Received termination signal (%s). Initiating graceful shutdown...", sig_name)
            self.running = False

        # Register signal handlers
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info(
            "AI Decision Engine continuous monitoring active. Polling every %d seconds. (Press Ctrl+C to terminate)",
            polling_interval,
        )

        while self.running:
            start_time = time.time()

            try:
                self.run_once()
            except Exception as exc:
                logger.error("Unhandled error during decision cycle: %s", exc, exc_info=True)

            elapsed = time.time() - start_time
            sleep_duration = max(0.5, polling_interval - elapsed)

            # Responsive sleep loop checking self.running flag
            end_sleep = time.time() + sleep_duration
            while self.running and time.time() < end_sleep:
                time.sleep(0.5)

        logger.info("AI Decision Engine shut down gracefully.")
