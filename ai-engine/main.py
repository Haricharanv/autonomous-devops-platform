"""Main entrypoint for the AI Decision Engine in the Autonomous DevOps Deployment Platform.

Parses command-line arguments, sets up structured rotating file and colored console logging,
initializes the DecisionEngine, and coordinates single-cycle or continuous operational loops
with graceful shutdown handling.
"""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from typing import Optional

# Ensure the module directory is in the import search path
ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import yaml
from decision_engine import DecisionEngine, DecisionEngineError

logger = logging.getLogger("ai_engine")


def setup_logging(
    log_file: str = "ai_engine_system.log",
    verbose: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configures file and console logging with optional colorlog support and log rotation.

    Args:
        log_file: Path to output log file for rotating handler.
        verbose: If True, set logging level to DEBUG, otherwise INFO.
        max_bytes: Maximum size per log file in bytes before rotation (default 10 MB).
        backup_count: Number of rotated backup log files to retain.
    """
    log_level = logging.DEBUG if verbose else logging.INFO

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to prevent duplicate lines
    while root_logger.handlers:
        root_logger.handlers.pop()

    standard_format = "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Setup Console Handler with colorlog if installed
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    try:
        import colorlog

        colored_format = (
            "%(log_color)s%(asctime)s [%(levelname)-8s]%(reset)s "
            "%(cyan)s[%(name)s]%(reset)s %(message_log_color)s%(message)s"
        )
        formatter = colorlog.ColoredFormatter(
            colored_format,
            datefmt=date_format,
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
            secondary_log_colors={
                "message": {
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                    "WARNING": "yellow",
                }
            },
            style="%",
        )
        console_handler.setFormatter(formatter)
    except ImportError:
        formatter = logging.Formatter(standard_format, datefmt=date_format)
        console_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)

    # Setup Rotating File Handler
    try:
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = ENGINE_DIR / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(standard_format, datefmt=date_format))
        root_logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Could not initialize file log handler at %s: %s", log_file, exc)


def parse_arguments(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parses command line arguments.

    Args:
        args: Argument list (defaults to sys.argv[1:]).

    Returns:
        Populated argparse.Namespace.
    """
    parser = argparse.ArgumentParser(
        prog="ai-engine",
        description="Autonomous AI Decision Engine for Kubernetes DevOps Platform.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=str(ENGINE_DIR / "config.yaml"),
        help="Path to YAML configuration file.",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate actions without executing commands against cluster infrastructure.",
    )
    parser.add_argument(
        "-o",
        "--once",
        action="store_true",
        default=False,
        help="Execute a single evaluation cycle and exit immediately.",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=None,
        help="Override the polling interval in seconds.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable detailed debug logging output.",
    )

    return parser.parse_args(args)


def main() -> int:
    """Main CLI entry point function.

    Returns:
        Exit code (0 for clean execution, non-zero for failure).
    """
    cli_args = parse_arguments()

    # Configure system runtime logging
    setup_logging(log_file="ai_engine_system.log", verbose=cli_args.verbose)

    logger.info("Starting AI Decision Engine...")
    config_path = Path(cli_args.config)
    logger.info("Configuration: %s", config_path)
    logger.info("Mode: %s | Dry-Run: %s", "Single-Run" if cli_args.once else "Continuous", cli_args.dry_run)

    try:
        engine = DecisionEngine(config_path=str(config_path), dry_run=cli_args.dry_run)

        if cli_args.once:
            logger.info("Executing single decision cycle (--once)...")
            decisions = engine.run_once()
            logger.info("Cycle completed. Decisions executed: %d", len(decisions))
            for i, dec in enumerate(decisions, start=1):
                logger.info(" [%d] Action: %s | Priority: %s | Reason: %s", i, dec.action, dec.priority, dec.reason)
            return 0
        else:
            engine.run(interval=cli_args.interval)
            return 0

    except DecisionEngineError as exc:
        logger.critical("Engine initialization or configuration failure: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt. Exiting...")
        return 0
    except Exception as exc:
        logger.critical("Fatal unhandled exception in AI Decision Engine: %s", exc, exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
