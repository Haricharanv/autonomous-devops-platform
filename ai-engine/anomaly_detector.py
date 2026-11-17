"""Anomaly Detector module for the AI Decision Engine.

This module provides the AnomalyDetector class using scikit-learn's IsolationForest
to detect anomalous system and application performance patterns (unusual CPU spikes,
traffic surges, latency anomalies, and compound metric shifts) from Prometheus metrics.
"""

from __future__ import annotations

import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)


class AnomalyDetectorError(Exception):
    """Custom exception raised for anomaly detection or model serialization errors."""
    pass


class AnomalyDetector:
    """Detects multi-metric anomalies using unsupervised Isolation Forest modeling.

    Attributes:
        model_path: File system path where the trained model is serialized.
        contamination: Proportion of expected outliers in the training data (default 0.05).
        model: Scikit-learn IsolationForest instance.
        is_trained: Boolean flag indicating if the model has been fitted.
        feature_names: Ordered list of feature names used for training and inference.
    """

    FEATURE_KEYS = [
        "cpu_norm",
        "memory_norm",
        "error_norm",
        "request_log",
        "latency_norm",
        "restarts_norm",
        "delta_cpu",
        "delta_mem",
        "delta_error",
        "delta_request",
        "delta_latency",
        "rolling_cpu_mean",
        "rolling_cpu_std",
        "rolling_error_mean",
    ]

    def __init__(
        self,
        model_path: str = "models/anomaly_detector.pkl",
        contamination: float = 0.05,
        auto_load: bool = True,
    ) -> None:
        """Initializes the AnomalyDetector with model configuration.

        Args:
            model_path: File path to save or load the model.
            contamination: Expected proportion of outliers in the data.
            auto_load: If True and model_path exists, load the model upon initialization.
        """
        self.model_path = model_path
        self.contamination = contamination
        self.model: Optional[IsolationForest] = None
        self.is_trained: bool = False
        self.feature_names = list(self.FEATURE_KEYS)

        if auto_load and os.path.exists(self.model_path):
            try:
                self.load_model(self.model_path)
            except Exception as exc:
                logger.warning("Could not auto-load existing model from %s: %s", self.model_path, exc)

    def extract_features(
        self,
        current: Dict[str, Any],
        previous: Optional[Dict[str, Any]] = None,
        window: Optional[List[Dict[str, Any]]] = None,
    ) -> np.ndarray:
        """Extracts engineered feature vector from current metrics and historical context.

        Engineered features include:
        - Normalized metric levels (CPU, memory, error rate, pod restarts)
        - Non-linear transformations (log scale request rate, clamped latency)
        - First-order rate of change (deltas from previous metric collection)
        - Rolling statistics (mean and standard deviation over temporal window)

        Args:
            current: Current metrics snapshot dictionary.
            previous: Immediately preceding metrics snapshot dictionary, if available.
            window: List of recent metrics snapshots for rolling statistics.

        Returns:
            1D numpy array representing the extracted feature vector.
        """
        cpu = float(current.get("cpu_usage", 0.0))
        mem = float(current.get("memory_usage", 0.0))
        err = float(current.get("error_rate", 0.0))
        req = float(current.get("request_rate", 0.0))
        lat = float(current.get("latency_p95", 0.0))
        restarts = float(current.get("max_pod_restarts", 0))

        # Base normalized metrics
        cpu_norm = min(1.0, max(0.0, cpu / 100.0))
        mem_norm = min(1.0, max(0.0, mem / 100.0))
        err_norm = min(1.0, max(0.0, err / 100.0))
        req_log = float(np.log1p(max(0.0, req)))
        lat_norm = min(1.0, max(0.0, lat / 5.0))  # Normalize up to 5s scale
        restarts_norm = min(1.0, max(0.0, restarts / 10.0))

        # Rate of change / deltas
        if previous:
            prev_cpu = float(previous.get("cpu_usage", cpu))
            prev_mem = float(previous.get("memory_usage", mem))
            prev_err = float(previous.get("error_rate", err))
            prev_req = float(previous.get("request_rate", req))
            prev_lat = float(previous.get("latency_p95", lat))

            delta_cpu = (cpu - prev_cpu) / 100.0
            delta_mem = (mem - prev_mem) / 100.0
            delta_error = (err - prev_err) / 100.0
            delta_request = float(np.log1p(max(0.0, req)) - np.log1p(max(0.0, prev_req)))
            delta_latency = (lat - prev_lat) / 5.0
        else:
            delta_cpu = 0.0
            delta_mem = 0.0
            delta_error = 0.0
            delta_request = 0.0
            delta_latency = 0.0

        # Rolling window statistics
        if window and len(window) > 1:
            recent_cpus = [float(item.get("cpu_usage", 0.0)) / 100.0 for item in window]
            recent_errs = [float(item.get("error_rate", 0.0)) / 100.0 for item in window]
            rolling_cpu_mean = float(np.mean(recent_cpus))
            rolling_cpu_std = float(np.std(recent_cpus))
            rolling_error_mean = float(np.mean(recent_errs))
        else:
            rolling_cpu_mean = cpu_norm
            rolling_cpu_std = 0.0
            rolling_error_mean = err_norm

        features = [
            cpu_norm,
            mem_norm,
            err_norm,
            req_log,
            lat_norm,
            restarts_norm,
            delta_cpu,
            delta_mem,
            delta_error,
            delta_request,
            delta_latency,
            rolling_cpu_mean,
            rolling_cpu_std,
            rolling_error_mean,
        ]

        vec = np.nan_to_num(np.array(features, dtype=np.float64), nan=0.0, posinf=1.0, neginf=-1.0)
        return vec

    def train(self, metrics_history: List[Dict[str, Any]]) -> bool:
        """Trains the IsolationForest model on a historical collection of metric snapshots.

        Args:
            metrics_history: Chronological list of metrics snapshots.

        Returns:
            True if model training succeeded and model was saved, False otherwise.

        Raises:
            AnomalyDetectorError: If training fails due to invalid data.
        """
        if not metrics_history or len(metrics_history) < 5:
            logger.warning(
                "Insufficient historical data for anomaly training (found %d, need >= 5)",
                len(metrics_history) if metrics_history else 0,
            )
            return False

        logger.info("Training IsolationForest anomaly detector on %d data points...", len(metrics_history))

        # Build feature matrix
        feature_rows: List[np.ndarray] = []
        for i, current in enumerate(metrics_history):
            prev = metrics_history[i - 1] if i > 0 else None
            window_slice = metrics_history[max(0, i - 10) : i + 1]
            vec = self.extract_features(current, previous=prev, window=window_slice)
            feature_rows.append(vec)

        X = np.vstack(feature_rows)

        try:
            model = IsolationForest(
                n_estimators=100,
                contamination=self.contamination,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X)
            self.model = model
            self.is_trained = True
            logger.info("IsolationForest successfully fitted with %d features on %d samples", X.shape[1], X.shape[0])

            # Persist trained model
            self.save_model(self.model_path)
            return True

        except Exception as exc:
            logger.error("Failed to train IsolationForest: %s", exc)
            raise AnomalyDetectorError(f"Training failed: {exc}") from exc

    def detect(
        self,
        current_metrics: Dict[str, Any],
        previous_metrics: Optional[Dict[str, Any]] = None,
        window: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, float]:
        """Detects whether current metrics deviate significantly from expected normal behavior.

        Args:
            current_metrics: Snapshot of current metrics.
            previous_metrics: Immediate previous metrics snapshot.
            window: Recent list of metrics snapshots.

        Returns:
            Tuple of (is_anomaly, anomaly_score) where:
                is_anomaly: True if the point is classified as an outlier/anomaly.
                anomaly_score: Value between 0.0 (strictly normal) and 1.0 (extreme anomaly).
        """
        if not self.is_trained or self.model is None:
            logger.debug("AnomalyDetector is not trained; skipping detection.")
            return False, 0.0

        vec = self.extract_features(current_metrics, previous=previous_metrics, window=window)
        X = vec.reshape(1, -1)

        try:
            # IsolationForest predict returns -1 for outlier/anomaly, +1 for inlier
            prediction = self.model.predict(X)[0]
            is_anomaly = bool(prediction == -1)

            # decision_function yields signed anomaly score:
            # Positive for inliers, negative for outliers
            decision_val = float(self.model.decision_function(X)[0])

            # Map decision value to [0.0, 1.0] anomaly likelihood score
            # A lower decision value indicates higher anomaly severity
            # Sigmoid transformation: 1 / (1 + exp(decision_val * 10))
            score = float(1.0 / (1.0 + math.exp(np.clip(decision_val * 8.0, -20.0, 20.0))))
            score = round(max(0.0, min(1.0, score)), 4)

            if is_anomaly:
                logger.warning("ML Anomaly Detected! Score: %.4f (decision_val: %.4f)", score, decision_val)
            else:
                logger.debug("ML Anomaly check passed: normal (score: %.4f)", score)

            return is_anomaly, score

        except Exception as exc:
            logger.error("Error evaluating anomaly detection: %s", exc)
            return False, 0.0

    def save_model(self, path: Optional[str] = None) -> bool:
        """Serializes the fitted IsolationForest model to disk using joblib.

        Args:
            path: Target file path, defaults to self.model_path.

        Returns:
            True if saving succeeded, False otherwise.

        Raises:
            AnomalyDetectorError: If model cannot be saved.
        """
        target_path = Path(path or self.model_path)
        if not self.is_trained or self.model is None:
            logger.warning("Attempted to save an untrained AnomalyDetector model.")
            return False

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "model": self.model,
                "feature_names": self.feature_names,
                "contamination": self.contamination,
                "saved_at": time.time(),
            }
            joblib.dump(payload, target_path)
            logger.info("Successfully saved anomaly detector model to %s", target_path)
            return True
        except Exception as exc:
            logger.error("Failed to persist model to %s: %s", target_path, exc)
            raise AnomalyDetectorError(f"Model persistence failed: {exc}") from exc

    def load_model(self, path: Optional[str] = None) -> bool:
        """Loads a pre-trained model and metadata from disk using joblib.

        Args:
            path: Source file path, defaults to self.model_path.

        Returns:
            True if loading succeeded, False otherwise.

        Raises:
            AnomalyDetectorError: If file exists but cannot be loaded.
        """
        source_path = Path(path or self.model_path)
        if not source_path.is_file():
            logger.info("Model file %s does not exist; model remains uninitialized.", source_path)
            return False

        try:
            payload = joblib.load(source_path)
            if isinstance(payload, dict) and "model" in payload:
                self.model = payload["model"]
                self.feature_names = payload.get("feature_names", list(self.FEATURE_KEYS))
                self.contamination = payload.get("contamination", self.contamination)
            elif isinstance(payload, IsolationForest):
                self.model = payload
            else:
                raise ValueError("Unrecognized model payload format")

            self.is_trained = True
            logger.info("Successfully loaded anomaly detector model from %s", source_path)
            return True
        except Exception as exc:
            logger.error("Failed to load model from %s: %s", source_path, exc)
            raise AnomalyDetectorError(f"Model load failed: {exc}") from exc
