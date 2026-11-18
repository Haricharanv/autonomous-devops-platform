#!/usr/bin/env python3
"""
Intelligent Autonomous DevOps Deployment Platform - Load Testing Engine
========================================================================
Description: High-performance concurrent load testing tool with dynamic
             workload intensity, live terminal progress visualization,
             latency percentile distribution analysis (p50/p95/p99),
             and exportable performance audit reporting.

Usage:
    python3 load_test.py --url http://<host>:<port>/load --duration 30 --concurrency 10
"""

import argparse
import concurrent.futures
import json
import math
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class TerminalColors:
    """ANSI terminal styling codes."""
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    PURPLE = "\033[35m"
    WHITE = "\033[97m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"


class MetricsCollector:
    """Thread-safe collector for request execution metrics and latency samples."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.latencies_ms = []
        self.status_codes = {}
        self.error_messages = {}
        self.bytes_received = 0
        self.start_time = None
        self.end_time = None

    def start(self):
        self.start_time = time.perf_counter()

    def stop(self):
        self.end_time = time.perf_counter()

    def record_success(self, status_code: int, latency_ms: float, response_size: int):
        with self._lock:
            self.total_requests += 1
            self.successful_requests += 1
            self.latencies_ms.append(latency_ms)
            self.bytes_received += response_size
            code_key = str(status_code)
            self.status_codes[code_key] = self.status_codes.get(code_key, 0) + 1

    def record_failure(self, error_type: str, latency_ms: float = 0.0):
        with self._lock:
            self.total_requests += 1
            self.failed_requests += 1
            if latency_ms > 0:
                self.latencies_ms.append(latency_ms)
            self.status_codes[error_type] = self.status_codes.get(error_type, 0) + 1
            self.error_messages[error_type] = self.error_messages.get(error_type, 0) + 1

    def get_snapshot(self):
        with self._lock:
            return {
                "total": self.total_requests,
                "success": self.successful_requests,
                "failed": self.failed_requests,
                "samples": len(self.latencies_ms),
                "latest_latency": self.latencies_ms[-1] if self.latencies_ms else 0.0,
            }

    def compute_statistics(self) -> dict:
        with self._lock:
            latencies = sorted(self.latencies_ms)
            elapsed = (self.end_time - self.start_time) if (self.start_time and self.end_time) else 1.0
            total = self.total_requests
            success = self.successful_requests
            failed = self.failed_requests

        if not latencies:
            return {
                "total_requests": total,
                "successful_requests": success,
                "failed_requests": failed,
                "success_rate_pct": 0.0,
                "throughput_rps": 0.0,
                "latency_min_ms": 0.0,
                "latency_max_ms": 0.0,
                "latency_avg_ms": 0.0,
                "latency_stddev_ms": 0.0,
                "latency_p50_ms": 0.0,
                "latency_p90_ms": 0.0,
                "latency_p95_ms": 0.0,
                "latency_p99_ms": 0.0,
                "elapsed_seconds": round(elapsed, 2),
                "status_codes": self.status_codes,
            }

        def percentile(sorted_list, p):
            k = (len(sorted_list) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_list[int(k)]
            d0 = sorted_list[int(f)] * (c - k)
            d1 = sorted_list[int(c)] * (k - f)
            return d0 + d1

        avg_lat = statistics.mean(latencies)
        std_lat = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

        return {
            "total_requests": total,
            "successful_requests": success,
            "failed_requests": failed,
            "success_rate_pct": round((success / total * 100.0) if total > 0 else 0.0, 2),
            "throughput_rps": round(total / elapsed, 2),
            "latency_min_ms": round(min(latencies), 2),
            "latency_max_ms": round(max(latencies), 2),
            "latency_avg_ms": round(avg_lat, 2),
            "latency_stddev_ms": round(std_lat, 2),
            "latency_p50_ms": round(percentile(latencies, 0.50), 2),
            "latency_p90_ms": round(percentile(latencies, 0.90), 2),
            "latency_p95_ms": round(percentile(latencies, 0.95), 2),
            "latency_p99_ms": round(percentile(latencies, 0.99), 2),
            "elapsed_seconds": round(elapsed, 2),
            "bytes_received": self.bytes_received,
            "status_codes": self.status_codes,
        }


def calculate_dynamic_intensity(elapsed: float, duration: float, min_intensity: int, max_intensity: int) -> int:
    """
    Computes dynamic wave intensity based on elapsed test time.
    Generates sinusoidal peaks to simulate realistic traffic surges and trigger HPA autoscaling.
    """
    if duration <= 0:
        return min_intensity
    progress = min(1.0, elapsed / duration)
    # 2 full oscillation cycles during test
    wave = 0.5 * (1.0 + math.sin(progress * 4.0 * math.pi - (math.pi / 2.0)))
    intensity = min_intensity + (max_intensity - min_intensity) * wave
    return max(1, int(round(intensity)))


def worker_task(base_url: str, stop_event: threading.Event, start_time: float,
                duration: float, min_intensity: int, max_intensity: int,
                timeout: float, collector: MetricsCollector):
    """
    Continuous worker loop that sends HTTP requests with dynamic intensity until stopped.
    """
    parsed_base = urllib.parse.urlparse(base_url)

    while not stop_event.is_set():
        current_time = time.perf_counter()
        elapsed = current_time - start_time
        if elapsed >= duration:
            break

        intensity = calculate_dynamic_intensity(elapsed, duration, min_intensity, max_intensity)

        # Inject intensity parameter into query string
        query_params = urllib.parse.parse_qs(parsed_base.query)
        query_params["intensity"] = [str(intensity)]
        query_params["load"] = ["true"]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        target_url = urllib.parse.urlunparse(parsed_base._replace(query=new_query))

        req = urllib.request.Request(
            target_url,
            headers={
                "User-Agent": "Autonomous-DevOps-LoadTester/1.0",
                "Accept": "application/json, text/plain, */*",
            },
            method="GET",
        )

        req_start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                latency_ms = (time.perf_counter() - req_start) * 1000.0
                status_code = resp.status if hasattr(resp, "status") else 200
                if 200 <= status_code < 400:
                    collector.record_success(status_code, latency_ms, len(data))
                else:
                    collector.record_failure(f"HTTP_{status_code}", latency_ms)
        except urllib.error.HTTPError as e:
            latency_ms = (time.perf_counter() - req_start) * 1000.0
            collector.record_failure(f"HTTP_{e.code}", latency_ms)
        except urllib.error.URLError as e:
            err_reason = type(e.reason).__name__ if hasattr(e, "reason") else "URLError"
            collector.record_failure(f"Network_{err_reason}")
        except TimeoutError:
            collector.record_failure("Timeout")
        except Exception as e:
            collector.record_failure(f"Error_{type(e).__name__}")


def display_live_progress(stop_event: threading.Event, start_time: float,
                          duration: float, collector: MetricsCollector, quiet: bool):
    """
    Renders an in-place animated terminal progress bar with live throughput and latency.
    """
    if quiet:
        return

    bar_width = 24
    while not stop_event.is_set():
        elapsed = time.perf_counter() - start_time
        progress = min(1.0, elapsed / duration) if duration > 0 else 1.0
        filled = int(round(bar_width * progress))
        bar = "█" * filled + "░" * (bar_width - filled)

        snap = collector.get_snapshot()
        rps = snap["total"] / elapsed if elapsed > 0.1 else 0.0
        pct = progress * 100.0
        latest_lat = snap["latest_latency"]

        color = TerminalColors.GREEN if snap["failed"] == 0 else TerminalColors.YELLOW

        # Overwrite terminal line with \r and clear remainder
        sys.stdout.write(
            f"\r{TerminalColors.BOLD}[TESTING]{TerminalColors.RESET} "
            f"|{TerminalColors.CYAN}{bar}{TerminalColors.RESET}| "
            f"{pct:5.1f}% [{elapsed:4.1f}s/{duration:4.1f}s] "
            f"Reqs: {TerminalColors.BOLD}{snap['total']:,}{TerminalColors.RESET} "
            f"({rps:5.1f} r/s) "
            f"{color}✓ {snap['success']:,}{TerminalColors.RESET} "
            f"{TerminalColors.RED}✗ {snap['failed']:,}{TerminalColors.RESET} "
            f"Lat: {latest_lat:5.1f}ms   "
        )
        sys.stdout.flush()
        time.sleep(0.15)

    sys.stdout.write("\n")
    sys.stdout.flush()


def print_summary_report(stats: dict, target_url: str, concurrency: int,
                         duration: float, min_intensity: int, max_intensity: int):
    """
    Renders an ASCII performance summary report with percentiles and verdict.
    """
    c = TerminalColors
    w = 72

    print("\n" + c.BOLD + c.PURPLE + "╔" + "═" * (w - 2) + "╗" + c.RESET)
    title = "AUTONOMOUS DEVOPS PLATFORM - LOAD TESTING REPORT"
    print(c.BOLD + c.PURPLE + "║" + c.CYAN + title.center(w - 2) + c.PURPLE + "║" + c.RESET)
    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)

    def print_row(key: str, val: str, color: str = c.WHITE):
        line = f"  {c.BOLD}{key:<26}{c.RESET} : {color}{val}{c.RESET}"
        pad = w - 4 - len(key) - 3 - len(str(val))
        print(c.PURPLE + "║" + c.RESET + line + (" " * max(0, pad)) + c.PURPLE + "║" + c.RESET)

    print_row("Target Endpoint", target_url, c.CYAN)
    print_row("Test Duration", f"{stats['elapsed_seconds']} seconds (Configured: {duration}s)")
    print_row("Worker Concurrency", f"{concurrency} threads")
    print_row("Dynamic Intensity Range", f"x{min_intensity} to x{max_intensity} (Sinusoidal Peak)")

    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)
    print(c.BOLD + c.PURPLE + "║" + c.YELLOW + "  THROUGHPUT & EXECUTION METRICS".ljust(w - 2) + c.PURPLE + "║" + c.RESET)
    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)

    print_row("Total Requests Sent", f"{stats['total_requests']:,}")
    success_color = c.GREEN if stats['success_rate_pct'] >= 95.0 else c.YELLOW
    print_row("Successful Requests", f"{stats['successful_requests']:,} ({stats['success_rate_pct']}%)", success_color)
    fail_color = c.GREEN if stats['failed_requests'] == 0 else c.RED
    print_row("Failed Requests", f"{stats['failed_requests']:,}", fail_color)
    print_row("Effective Throughput", f"{stats['throughput_rps']:,} req/sec", c.BOLD + c.CYAN)

    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)
    print(c.BOLD + c.PURPLE + "║" + c.YELLOW + "  LATENCY PERCENTILES (Round-Trip Time)".ljust(w - 2) + c.PURPLE + "║" + c.RESET)
    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)

    print_row("Minimum Latency", f"{stats['latency_min_ms']} ms")
    print_row("Average Latency (Mean)", f"{stats['latency_avg_ms']} ms")
    print_row("Median Latency (p50)", f"{stats['latency_p50_ms']} ms", c.CYAN)
    print_row("90th Percentile (p90)", f"{stats['latency_p90_ms']} ms")
    print_row("95th Percentile (p95)", f"{stats['latency_p95_ms']} ms", c.YELLOW)
    print_row("99th Percentile (p99)", f"{stats['latency_p99_ms']} ms", c.RED if stats['latency_p99_ms'] > 500 else c.WHITE)
    print_row("Maximum Latency", f"{stats['latency_max_ms']} ms")
    print_row("Standard Deviation", f"{stats['latency_stddev_ms']} ms")

    if stats.get("status_codes"):
        print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)
        print(c.BOLD + c.PURPLE + "║" + c.YELLOW + "  HTTP STATUS CODE BREAKDOWN".ljust(w - 2) + c.PURPLE + "║" + c.RESET)
        print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)
        for code, count in sorted(stats["status_codes"].items()):
            color = c.GREEN if code.startswith("2") else (c.RED if code.startswith("5") or "Error" in code else c.YELLOW)
            print_row(f"Status [{code}]", f"{count:,} occurrences", color)

    print(c.BOLD + c.PURPLE + "╠" + "═" * (w - 2) + "╣" + c.RESET)

    # Performance Verdict
    if stats["success_rate_pct"] >= 99.0 and stats["latency_p95_ms"] < 250.0:
        verdict = "PASSED: Excellent performance under dynamic concurrency"
        v_color = c.BOLD + c.GREEN
    elif stats["success_rate_pct"] >= 95.0:
        verdict = "PASSED: Acceptable performance; HPA trigger threshold met"
        v_color = c.BOLD + c.YELLOW
    else:
        verdict = "FAILED: Elevated error rate detected; cluster autoscaling recommended"
        v_color = c.BOLD + c.RED

    print(c.PURPLE + "║" + c.RESET + f"  Verdict : {v_color}{verdict}{c.RESET}".ljust(w + 7) + c.PURPLE + "║" + c.RESET)
    print(c.BOLD + c.PURPLE + "╚" + "═" * (w - 2) + "╝" + c.RESET + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous DevOps Platform Concurrent Load Testing Engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url", "-u",
        type=str,
        default="http://localhost:8080/load",
        help="Target HTTP endpoint to stress test",
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=30.0,
        help="Test execution duration in seconds",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=10,
        help="Number of concurrent worker threads",
    )
    parser.add_argument(
        "--intensity-min",
        type=int,
        default=1,
        help="Minimum intensity multiplier sent to /load endpoint",
    )
    parser.add_argument(
        "--intensity-max",
        type=int,
        default=10,
        help="Peak intensity multiplier sent to /load endpoint",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float,
        default=5.0,
        help="HTTP request socket timeout in seconds",
    )
    parser.add_argument(
        "--output-json", "-o",
        type=str,
        default="",
        help="Path to export structured JSON metrics summary",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress animated terminal progress bar",
    )

    args = parser.parse_args()

    # Pre-flight check
    try:
        urllib.parse.urlparse(args.url)
    except Exception as e:
        print(f"{TerminalColors.RED}Error: Invalid target URL '{args.url}': {e}{TerminalColors.RESET}")
        sys.exit(1)

    print(f"{TerminalColors.BOLD}{TerminalColors.CYAN}Starting load test against:{TerminalColors.RESET} {args.url}")
    print(f"Concurrency: {args.concurrency} threads | Duration: {args.duration}s | Intensity: x{args.intensity_min}..x{args.intensity_max}")

    collector = MetricsCollector()
    stop_event = threading.Event()
    start_time = time.perf_counter()
    collector.start()

    # Launch progress display thread
    progress_thread = threading.Thread(
        target=display_live_progress,
        args=(stop_event, start_time, args.duration, collector, args.quiet),
        daemon=True,
    )
    progress_thread.start()

    # Launch worker threads via ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                worker_task,
                args.url,
                stop_event,
                start_time,
                args.duration,
                args.intensity_min,
                args.intensity_max,
                args.timeout,
                collector,
            )
            for _ in range(args.concurrency)
        ]

        # Wait until target duration elapses
        while time.perf_counter() - start_time < args.duration:
            time.sleep(0.1)

        # Signal stop
        stop_event.set()
        concurrent.futures.wait(futures, timeout=args.timeout + 2.0)

    collector.stop()
    progress_thread.join(timeout=1.0)

    # Compute and display report
    stats = collector.compute_statistics()
    print_summary_report(
        stats,
        target_url=args.url,
        concurrency=args.concurrency,
        duration=args.duration,
        min_intensity=args.intensity_min,
        max_intensity=args.intensity_max,
    )

    # Export JSON if requested
    if args.output_json:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
            print(f"{TerminalColors.GREEN}Report exported to: {args.output_json}{TerminalColors.RESET}")
        except Exception as e:
            print(f"{TerminalColors.RED}Failed to export JSON report: {e}{TerminalColors.RESET}")

    # Return non-zero if test experienced high failure rate (> 10%)
    if stats["total_requests"] > 0 and stats["success_rate_pct"] < 90.0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
