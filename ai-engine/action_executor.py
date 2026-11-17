"""Action Executor module for the AI Decision Engine.

This module provides the ActionExecutor class and ActionResult dataclass to execute
remediation and scaling actions across Kubernetes and Ansible, including scaling pods up/down,
rolling back deployments, and restarting crash-looping workloads.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from decision_engine import Decision

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Represents the outcome of an executed action.

    Attributes:
        success: Whether the action completed successfully.
        action: Name of the action executed (e.g., 'scale_up', 'rollback').
        output: Human-readable command output or status message.
        timestamp: ISO 8601 formatted timestamp of execution.
        command: Exact command string executed, if applicable.
        error: Error message if the action failed.
    """

    success: bool
    action: str
    output: str
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    command: str = ""
    error: Optional[str] = None


class ActionExecutorError(Exception):
    """Custom exception raised when action execution encounters an unrecoverable failure."""
    pass


class ActionExecutor:
    """Executes remediation actions on target deployments via kubectl and Ansible.

    Attributes:
        config: Full engine configuration dictionary.
        kubectl_path: Executable path for kubectl.
        ansible_path: Executable path for ansible-playbook.
        ansible_playbooks_dir: Path to Ansible playbooks directory.
        namespace: Target Kubernetes namespace.
        deployment: Target Kubernetes deployment name.
        min_replicas: Configured minimum replica floor.
        max_replicas: Configured maximum replica ceiling.
        scale_up_count: Default number of replicas to add during scale up.
        scale_down_count: Default number of replicas to remove during scale down.
        dry_run: If True, simulate actions without modifying infrastructure.
    """

    def __init__(self, config: Dict[str, Any], dry_run: bool = False) -> None:
        """Initializes ActionExecutor with configuration parameters.

        Args:
            config: Configuration dictionary containing 'actions' and 'scaling' sections.
            dry_run: When True, commands are simulated and not executed.
        """
        self.config = config
        self.dry_run = dry_run

        actions_cfg = config.get("actions", {})
        scaling_cfg = config.get("scaling", {})

        self.kubectl_path = actions_cfg.get("kubectl_path", "kubectl")
        self.ansible_path = actions_cfg.get("ansible_path", "ansible-playbook")
        self.ansible_playbooks_dir = actions_cfg.get("ansible_playbooks_dir", "../ansible/playbooks")
        self.namespace = actions_cfg.get("namespace", "devops-platform")
        self.deployment = actions_cfg.get("deployment", "devops-demo-app")

        self.min_replicas = int(scaling_cfg.get("min_replicas", 2))
        self.max_replicas = int(scaling_cfg.get("max_replicas", 10))
        self.scale_up_count = int(scaling_cfg.get("scale_up_count", 2))
        self.scale_down_count = int(scaling_cfg.get("scale_down_count", 1))

        # Track output of the most recent operation
        self.last_command: str = ""
        self.last_output: str = ""
        self.last_error: Optional[str] = None

        logger.info(
            "ActionExecutor initialized (dry_run=%s, deployment=%s/%s, min=%d, max=%d)",
            self.dry_run,
            self.namespace,
            self.deployment,
            self.min_replicas,
            self.max_replicas,
        )

    def _run_command(self, cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
        """Wrapper around subprocess.run with timeout, safety, and logging.

        Args:
            cmd: Command arguments list to execute.
            timeout: Subprocess execution timeout in seconds.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        cmd_str = " ".join(cmd)
        self.last_command = cmd_str
        logger.debug("Executing command: %s (timeout=%ds)", cmd_str, timeout)

        if self.dry_run:
            msg = f"[DRY-RUN] Simulated execution: {cmd_str}"
            logger.info(msg)
            self.last_output = msg
            self.last_error = None
            return 0, msg, ""

        binary = cmd[0]
        # Check binary availability in PATH if relative
        if not os.path.isabs(binary) and shutil.which(binary) is None:
            err = f"Executable '{binary}' not found on system PATH."
            logger.error(err)
            self.last_output = ""
            self.last_error = err
            return 127, "", err

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            self.last_output = stdout if proc.returncode == 0 else f"{stdout}\n{stderr}".strip()
            self.last_error = stderr if proc.returncode != 0 else None

            if proc.returncode == 0:
                logger.info("Command succeeded: %s | Output: %s", cmd_str, stdout)
            else:
                logger.warning("Command failed (rc=%d): %s | Error: %s", proc.returncode, cmd_str, stderr)

            return proc.returncode, stdout, stderr

        except subprocess.TimeoutExpired as exc:
            err_msg = f"Command timed out after {timeout} seconds: {cmd_str}"
            logger.error(err_msg)
            self.last_output = ""
            self.last_error = err_msg
            return -1, "", err_msg
        except OSError as exc:
            err_msg = f"OS error running command {cmd_str}: {exc}"
            logger.error(err_msg)
            self.last_output = ""
            self.last_error = err_msg
            return 1, "", err_msg

    def get_current_replicas(self) -> int:
        """Retrieves the current number of desired replicas from the deployment spec.

        Returns:
            Current replica count, or min_replicas if inspection fails.
        """
        if self.dry_run:
            return self.min_replicas

        cmd = [
            self.kubectl_path,
            "get",
            f"deployment/{self.deployment}",
            "-n",
            self.namespace,
            "-o",
            "jsonpath={.spec.replicas}",
        ]
        rc, stdout, stderr = self._run_command(cmd, timeout=15)
        if rc == 0 and stdout:
            try:
                return int(stdout.strip("'\""))
            except ValueError:
                pass

        logger.warning(
            "Could not determine current replicas for %s/%s; defaulting to min_replicas=%d",
            self.namespace,
            self.deployment,
            self.min_replicas,
        )
        return self.min_replicas

    def _has_ansible_playbook(self, playbook_name: str) -> Optional[Path]:
        """Checks if a given Ansible playbook exists and ansible-playbook binary is available.

        Args:
            playbook_name: Filename of the playbook (e.g. 'scale.yml').

        Returns:
            Resolved Path to playbook if found and ansible-playbook exists, else None.
        """
        playbook_path = Path(self.ansible_playbooks_dir) / playbook_name
        if playbook_path.is_file() and (os.path.isabs(self.ansible_path) or shutil.which(self.ansible_path)):
            return playbook_path.resolve()
        return None

    def scale_up(self, replicas_to_add: int = 2) -> bool:
        """Scales up the deployment by adding replicas, respecting max_replicas ceiling.

        Args:
            replicas_to_add: Number of replicas to add (defaults to 2).

        Returns:
            True if scale up was successfully executed or simulated, False otherwise.
        """
        current_replicas = self.get_current_replicas()
        if current_replicas >= self.max_replicas:
            msg = f"Scale up skipped: current replicas ({current_replicas}) >= max_replicas ({self.max_replicas})"
            logger.info(msg)
            self.last_output = msg
            self.last_error = None
            return False

        target_replicas = min(current_replicas + replicas_to_add, self.max_replicas)
        logger.info(
            "Scaling up deployment %s/%s from %d to %d replicas (+%d)",
            self.namespace,
            self.deployment,
            current_replicas,
            target_replicas,
            replicas_to_add,
        )

        ansible_pb = self._has_ansible_playbook("scale.yml")
        if ansible_pb:
            cmd = [
                self.ansible_path,
                str(ansible_pb),
                "-e",
                f"replicas={target_replicas} namespace={self.namespace} deployment={self.deployment}",
            ]
        else:
            cmd = [
                self.kubectl_path,
                "scale",
                f"deployment/{self.deployment}",
                f"--replicas={target_replicas}",
                "-n",
                self.namespace,
            ]

        rc, stdout, stderr = self._run_command(cmd)
        return rc == 0

    def scale_down(self, replicas_to_remove: int = 1) -> bool:
        """Scales down the deployment by removing replicas, respecting min_replicas floor.

        Args:
            replicas_to_remove: Number of replicas to remove (defaults to 1).

        Returns:
            True if scale down was successfully executed or simulated, False otherwise.
        """
        current_replicas = self.get_current_replicas()
        if current_replicas <= self.min_replicas:
            msg = f"Scale down skipped: current replicas ({current_replicas}) <= min_replicas ({self.min_replicas})"
            logger.info(msg)
            self.last_output = msg
            self.last_error = None
            return False

        target_replicas = max(current_replicas - replicas_to_remove, self.min_replicas)
        logger.info(
            "Scaling down deployment %s/%s from %d to %d replicas (-%d)",
            self.namespace,
            self.deployment,
            current_replicas,
            target_replicas,
            replicas_to_remove,
        )

        ansible_pb = self._has_ansible_playbook("scale.yml")
        if ansible_pb:
            cmd = [
                self.ansible_path,
                str(ansible_pb),
                "-e",
                f"replicas={target_replicas} namespace={self.namespace} deployment={self.deployment}",
            ]
        else:
            cmd = [
                self.kubectl_path,
                "scale",
                f"deployment/{self.deployment}",
                f"--replicas={target_replicas}",
                "-n",
                self.namespace,
            ]

        rc, stdout, stderr = self._run_command(cmd)
        return rc == 0

    def rollback(self, revision: Optional[int] = None) -> bool:
        """Rolls back the deployment to the previous or specified revision.

        Args:
            revision: Specific revision number to roll back to, or None for immediate previous.

        Returns:
            True if rollback was successfully executed or simulated, False otherwise.
        """
        logger.warning(
            "Executing CRITICAL rollback on deployment %s/%s (revision=%s)",
            self.namespace,
            self.deployment,
            revision if revision is not None else "previous",
        )

        ansible_pb = self._has_ansible_playbook("rollback.yml")
        if ansible_pb:
            extra_vars = f"namespace={self.namespace} deployment={self.deployment}"
            if revision is not None:
                extra_vars += f" revision={revision}"
            cmd = [self.ansible_path, str(ansible_pb), "-e", extra_vars]
        else:
            cmd = [
                self.kubectl_path,
                "rollout",
                "undo",
                f"deployment/{self.deployment}",
                "-n",
                self.namespace,
            ]
            if revision is not None:
                cmd.append(f"--to-revision={revision}")

        rc, stdout, stderr = self._run_command(cmd, timeout=120)
        return rc == 0

    def restart_pods(self) -> bool:
        """Performs a rolling restart of all pods in the deployment.

        Returns:
            True if rollout restart was successfully executed or simulated, False otherwise.
        """
        logger.warning(
            "Executing rolling restart on deployment %s/%s",
            self.namespace,
            self.deployment,
        )

        ansible_pb = self._has_ansible_playbook("restart.yml")
        if ansible_pb:
            cmd = [
                self.ansible_path,
                str(ansible_pb),
                "-e",
                f"namespace={self.namespace} deployment={self.deployment}",
            ]
        else:
            cmd = [
                self.kubectl_path,
                "rollout",
                "restart",
                f"deployment/{self.deployment}",
                "-n",
                self.namespace,
            ]

        rc, stdout, stderr = self._run_command(cmd, timeout=120)
        return rc == 0

    def execute(self, decision: Any) -> ActionResult:
        """Routes a Decision object to the corresponding action handler.

        Args:
            decision: A Decision instance (or object with action attribute).

        Returns:
            ActionResult capturing status, output, command, and timestamp.
        """
        action = getattr(decision, "action", str(decision)).lower().strip()
        details = getattr(decision, "details", {})
        logger.info("Routing decision action '%s' for execution", action)

        success = False
        if action == "scale_up":
            add_count = details.get("replicas_to_add", self.scale_up_count)
            success = self.scale_up(replicas_to_add=add_count)
        elif action == "scale_down":
            rem_count = details.get("replicas_to_remove", self.scale_down_count)
            success = self.scale_down(replicas_to_remove=rem_count)
        elif action == "rollback":
            revision = details.get("revision", None)
            success = self.rollback(revision=revision)
        elif action == "restart":
            success = self.restart_pods()
        elif action in ("none", "no_action"):
            success = True
            self.last_command = ""
            self.last_output = "No action required."
            self.last_error = None
        else:
            err = f"Unsupported or unknown action type: '{action}'"
            logger.error(err)
            self.last_command = ""
            self.last_output = ""
            self.last_error = err
            return ActionResult(
                success=False,
                action=action,
                output="",
                command="",
                error=err,
            )

        result = ActionResult(
            success=success,
            action=action,
            output=self.last_output or ("Action succeeded" if success else "Action failed"),
            command=self.last_command,
            error=self.last_error,
        )
        logger.info("Action '%s' completed with success=%s", action, success)
        return result
