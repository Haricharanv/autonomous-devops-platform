"""Notifier module for the AI Decision Engine.

This module provides the Notifier class for dispatching notifications across multiple
channels (Slack webhooks and persistent structured log files) whenever an automated
scaling, remediation, or rollback decision is executed.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from action_executor import ActionResult
    from decision_engine import Decision

logger = logging.getLogger(__name__)


class Notifier:
    """Manages notifications to external platforms and local persistent action logs.

    Attributes:
        config: Full engine configuration dictionary.
        enabled: Global toggle for notification dispatches.
        slack_webhook: Webhook URL for incoming Slack notifications.
        log_file: Path to local text/audit log file for actions.
        session: Reusable HTTP session for webhook requests.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initializes Notifier with configuration.

        Args:
            config: Engine configuration dictionary containing 'notifications' section.
        """
        self.config = config
        notifications_cfg = config.get("notifications", {})

        self.enabled = bool(notifications_cfg.get("enabled", True))
        self.slack_webhook = str(notifications_cfg.get("slack_webhook", "")).strip()
        self.log_file = str(notifications_cfg.get("log_file", "ai_engine.log"))
        self.session = requests.Session()

        # Ensure directory for log file exists
        log_path = Path(self.log_file)
        if log_path.parent and not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Notifier initialized (enabled=%s, slack_configured=%s, log_file=%s)",
            self.enabled,
            bool(self.slack_webhook),
            self.log_file,
        )

    def format_message(self, decision: Any, result: Any) -> str:
        """Formats a human-readable notification message for humans and chat channels.

        Args:
            decision: Decision instance (or object with action, reason, priority, metrics_snapshot).
            result: ActionResult instance (or object with success, action, output, timestamp).

        Returns:
            Formatted multiline string.
        """
        action = getattr(decision, "action", "unknown").upper()
        reason = getattr(decision, "reason", "No reason specified")
        priority = getattr(decision, "priority", "info").upper()
        metrics = getattr(decision, "metrics_snapshot", {})
        timestamp = getattr(result, "timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat())
        success = getattr(result, "success", False)
        output = getattr(result, "output", "")

        status_icon = "✅ SUCCESS" if success else "❌ FAILED"

        lines = [
            f"*{status_icon}* | *AI Decision Engine Alert*",
            f"• *Action:* `{action}`",
            f"• *Priority:* `{priority}`",
            f"• *Reason:* {reason}",
            f"• *Timestamp:* {timestamp}",
        ]

        if metrics:
            cpu = metrics.get("cpu_usage", "N/A")
            mem = metrics.get("memory_usage", "N/A")
            err = metrics.get("error_rate", "N/A")
            req = metrics.get("request_rate", "N/A")
            pods = metrics.get("pod_count", "N/A")
            lat = metrics.get("latency_p95", "N/A")
            lines.append(
                f"• *Metrics Snapshot:* CPU: `{cpu}%` | Mem: `{mem}%` | "
                f"5xx Errors: `{err}%` | Req: `{req} rps` | Pods: `{pods}` | p95 Latency: `{lat}s`"
            )

        if output:
            truncated_output = output[:400] + ("..." if len(output) > 400 else "")
            lines.append(f"• *Execution Output:* ```{truncated_output}```")

        return "\n".join(lines)

    def send_slack(self, message: str, severity: str = "info") -> bool:
        """Dispatches an alert message to the configured Slack webhook URL.

        Args:
            message: Formatted text or markdown content.
            severity: Alert severity level ('critical', 'high', 'warning', 'info', 'low').

        Returns:
            True if delivered successfully (HTTP 200), False otherwise.
        """
        if not self.enabled:
            logger.debug("Notifications are disabled globally; skipping Slack alert.")
            return False

        if not self.slack_webhook:
            logger.debug("Slack webhook URL not configured; skipping Slack dispatch.")
            return False

        # Assign Slack attachment color based on severity
        severity_lower = severity.lower()
        if severity_lower in ("critical", "emergency", "fatal"):
            color = "#E01E5A"  # Red
        elif severity_lower in ("high", "warning"):
            color = "#ECB22E"  # Amber
        elif severity_lower in ("low", "info"):
            color = "#2EB67D"  # Green
        else:
            color = "#4A154B"  # Neutral purple

        payload = {
            "text": f"🤖 *Autonomous AI Decision Engine* — Severity: *{severity.upper()}*",
            "attachments": [
                {
                    "color": color,
                    "text": message,
                    "mrkdwn_in": ["text", "pretext"],
                }
            ],
        }

        try:
            response = self.session.post(
                self.slack_webhook,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            if response.status_code == 200:
                logger.info("Slack notification delivered successfully.")
                return True
            else:
                logger.warning(
                    "Slack webhook responded with status %d: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to deliver Slack notification: %s", exc)
            return False

    def log_action(self, decision: Any, result: Any) -> None:
        """Appends a structured log entry of the decision and action result to file.

        Args:
            decision: The Decision object.
            result: The ActionResult object.
        """
        entry = {
            "timestamp": getattr(result, "timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()),
            "action": getattr(decision, "action", "none"),
            "priority": getattr(decision, "priority", "info"),
            "reason": getattr(decision, "reason", ""),
            "success": getattr(result, "success", False),
            "output": getattr(result, "output", ""),
            "command": getattr(result, "command", ""),
            "error": getattr(result, "error", None),
            "metrics": getattr(decision, "metrics_snapshot", {}),
        }

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            logger.debug("Logged action entry to %s", self.log_file)
        except OSError as exc:
            logger.error("Failed to write to action log file %s: %s", self.log_file, exc)

    def notify(self, decision: Any, result: Any) -> None:
        """Processes and sends all configured notifications for a decision and result.

        Args:
            decision: The Decision object.
            result: The ActionResult object.
        """
        # Always record in local structured audit log
        self.log_action(decision, result)

        # Do not spam external Slack notifications for no-action cycles
        action = getattr(decision, "action", "none").lower()
        if action in ("none", "no_action"):
            return

        message = self.format_message(decision, result)
        priority = getattr(decision, "priority", "info")
        self.send_slack(message, severity=priority)
