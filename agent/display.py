"""
display.py -- Terminal display for the K8s Agentic AI Monitor.

Manages a fixed-layout screen that redraws on every poll:
  - header with namespace, poll interval, current time
  - Pod table (redrawn each poll -- healthy=green, unhealthy=red)
  - Anomalies section (redrawn each poll)
  - Agent log tail (last 10 lines, live updates during investigation)
  - Static footer with key instructions

Uses colorama for terminal colors and cursor control.
log tail updates live as each round completes.
"""

import os
import time
from collections import deque
from colorama import Fore, Back, Style, init
from agent.config import cfg
from agent.logger import write_event

# Initialize colorama -- on Windows this is required to enable ANSI codes
init(autoreset=True)

# ---------------------------------------------------------------------------
# Color constants -- single place to change the whole color scheme
# ---------------------------------------------------------------------------

C_HEADER     = Fore.WHITE + Style.BRIGHT
C_BORDER     = Fore.WHITE + Style.DIM
C_HEALTHY    = Fore.GREEN
C_UNHEALTHY  = Fore.RED
C_WARNING    = Fore.YELLOW
C_TOOL       = Fore.CYAN
C_DECISION   = Fore.GREEN + Style.BRIGHT
C_ESCALATED  = Fore.RED + Style.BRIGHT
C_COOLDOWN   = Style.DIM
C_TIMESTAMP  = Style.DIM
C_SEPARATOR  = Fore.WHITE + Style.DIM
C_FOOTER     = Fore.WHITE + Style.DIM
C_ERROR      = Fore.RED + Style.BRIGHT
C_RESET      = Style.RESET_ALL

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

LOG_TAIL_SIZE  = cfg.log_tail_size       # number of agent log lines to keep visible
TABLE_COL_NAME = 45       # width of pod name column
SCREEN_WIDTH   = 64       # total width for borders and separators

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Rolling buffer of the last LOG_TAIL_SIZE agent log lines
# Each entry is a pre-colored string ready to print
_log_tail: deque = deque(maxlen=LOG_TAIL_SIZE)

# Stored for redraw -- set once at startup
_namespace   = "default"
_poll_interval = 10

# Stored for redraw during investigation
_last_pods:      list = []
_last_anomalies: list = []


# ---------------------------------------------------------------------------
# Public interface -- these are the only functions main.py needs to call
# ---------------------------------------------------------------------------

def init_display(namespace: str = "default", poll_interval: int = 10) -> None:
    """
    Call once at startup before the polling loop begins.
    Stores config and does the initial screen clear.
    """
    global _namespace, _poll_interval
    _namespace     = namespace
    _poll_interval = poll_interval
    clear_screen()


def render_screen(pods: list, anomalies: list) -> None:
    """
    Call every poll cycle. Redraws the entire screen:
      header -> pod table -> anomalies -> log tail -> footer

    During an active investigation the screen is frozen here
    (run_agent blocks) and log_event() appends to the tail live.
    After run_agent returns, the next poll redraws everything fresh.
    """
    global _last_pods, _last_anomalies
    _last_pods     = pods
    _last_anomalies = anomalies
    clear_screen()
    _print_header()
    _print_pod_table(pods)
    _print_anomalies(anomalies)
    _print_log_tail()
    _print_footer()

def log_event(event_type: str, message: str) -> None:
    """
    Append a new line to the agent log tail and immediately redraw
    just the log tail section so the user sees live progress.

    Called by the display_callback in main.py on every agent event.

    event_type is one of:
        "investigating"  -- starting an investigation
        "thinking"       -- round N of N
        "tool_call"      -- calling a tool
        "tool_result"    -- tool returned data
        "decision"       -- final decision reached
        "reasoning"      -- reasoning text from decision
        "recommendation" -- recommendation text from decision
        "cooldown"       -- cooldown blocked an action
        "escalated"      -- escalation triggered
        "error"          -- agent or API error
    """
    timestamp = time.strftime("%H:%M:%S")
    colored_line = _format_log_line(event_type, timestamp, message)
    _log_tail.append(colored_line)
    write_event(event_type, message)
    # Redraw just the log tail in place so the user sees it immediately
    # without waiting for the next full poll render
    _redraw_log_tail_inplace()


def show_confirmation_screen(anomaly: dict, decision: dict) -> None:
    """
    Clear the screen and show a dedicated confirmation prompt for
    LOW confidence decisions. Returns only after user types y or n.
    Called by main.py before ask_user_confirmation().
    """
    clear_screen()
    sep = C_SEPARATOR + "─" * SCREEN_WIDTH + C_RESET

    print()
    print(C_ESCALATED + "  (!!)  LOW CONFIDENCE -- HUMAN REVIEW REQUIRED" + C_RESET)
    print()
    print(sep)
    print(f"  {C_WARNING}Pod       :{C_RESET} {anomaly.get('pod', '?')}")
    print(f"  {C_WARNING}Problem   :{C_RESET} {anomaly.get('type', '?')} -- {anomaly.get('detail', '?')}")
    print(f"  {C_WARNING}Action    :{C_RESET} {decision.get('action', '?')} -> {decision.get('target', '?')}")
    print(f"  {C_WARNING}Reasoning :{C_RESET} {decision.get('reasoning', '?')}")
    if decision.get('recommendation'):
        print(f"  {C_WARNING}Tip       :{C_RESET} {decision.get('recommendation')}")
    print(sep)
    print()


def clear_screen() -> None:
    """Hard clear the terminal."""
    os.system("cls" if os.name == "nt" else "clear")


# ---------------------------------------------------------------------------
# rendering helpers
# ---------------------------------------------------------------------------

def _print_header() -> None:
    timestamp = time.strftime("%H:%M:%S")
    top    = "╔" + "═" * (SCREEN_WIDTH - 2) + "╗"
    bot    = "╚" + "═" * (SCREEN_WIDTH - 2) + "╝"
    title  = "K8s Agentic AI Monitor"
    info   = f"Watching: {_namespace}  |  Poll: {_poll_interval}s  |  {timestamp}"

    print(C_HEADER + top)
    print(C_HEADER + f"║  {title:<{SCREEN_WIDTH - 4}}║")
    print(C_HEADER + f"║  {info:<{SCREEN_WIDTH - 4}}║")
    print(C_HEADER + bot + C_RESET)
    print()


def _print_pod_table(pods: list) -> None:
    # Column headers
    print(
        f"  {Style.BRIGHT}{'NAME':<{TABLE_COL_NAME}} {'PHASE':<12} {'READY':<6} "
        f"{'RESTARTS':<10} {'REASON'}{C_RESET}"
    )
    print(
        C_BORDER +
        f"  {'-'*TABLE_COL_NAME} {'-'*12} {'-'*6} {'-'*10} {'-'*20}" +
        C_RESET
    )

    if not pods:
        print(f"  {C_COOLDOWN}(no pods found){C_RESET}")
    else:
        for pod in pods:
            ready_str  = "Yes" if pod["ready"] else "No"
            reason_str = pod["reason"] or "-"
            is_healthy = pod["ready"] and pod["phase"] == "Running"
            color      = C_HEALTHY if is_healthy else C_UNHEALTHY

            print(
                color +
                f"  {pod['name']:<{TABLE_COL_NAME}} {pod['phase']:<12} "
                f"{ready_str:<6} {pod['restarts']:<10} {reason_str}" +
                C_RESET
            )
    print()


def _print_anomalies(anomalies: list) -> None:
    sep = C_SEPARATOR + "  " + "─" * (SCREEN_WIDTH - 4) + C_RESET
    print(sep)

    if not anomalies:
        print(f"  {C_HEALTHY}:) No anomalies detected.{C_RESET}")
    else:
        count = len(anomalies)
        print(f"  {C_UNHEALTHY}(!!) {count} anomaly{'s' if count > 1 else ''} detected:{C_RESET}")
        for a in anomalies:
            severity_color = C_UNHEALTHY if a["severity"] == "HIGH" else C_WARNING
            print(
                f"    {severity_color}[{a['severity']}]{C_RESET} "
                f"{a['pod']} -- {a['type']}: {a['detail']}"
            )

    print(sep)
    print()


def _print_log_tail() -> None:
    if not _log_tail:
        print(f"  {C_COOLDOWN}No agent activity yet.{C_RESET}")
    else:
        for line in _log_tail:
            print(line)
    print()


def _print_footer() -> None:
    sep = C_SEPARATOR + "  " + "─" * (SCREEN_WIDTH - 4) + C_RESET
    print(sep)
    print(f"  {C_FOOTER}Ctrl+C to stop  |  y/n for LOW confidence prompts{C_RESET}")


def _redraw_log_tail_inplace() -> None:
    """
    Redraws only the log tail section without clearing the whole screen.
    Called after every log_event() so the user sees live agent progress
    while the table above stays frozen.

    Moves cursor up by (LOG_TAIL_SIZE + 3) lines (tail + blank + footer sep + footer),
    redraws the tail, then redraws the footer.
    """
    clear_screen()
    _print_header()
    _print_pod_table(_last_pods)
    _print_anomalies(_last_anomalies)
    _print_log_tail()
    _print_footer()


def _format_log_line(event_type: str, timestamp: str, message: str) -> str:
    """
    Format and colorize a single log line based on event type.
    Returns a complete colored string ready to print.
    """
    ts = C_TIMESTAMP + f"[{timestamp}]" + C_RESET

    if event_type == "investigating":
        return f"  {ts} {C_WARNING}~ {message}{C_RESET}"

    elif event_type == "thinking":
        return f"  {ts} {C_WARNING}~ {message}{C_RESET}"

    elif event_type == "tool_call":
        return f"  {ts} {C_TOOL}> {message}{C_RESET}"

    elif event_type == "tool_result":
        return f"  {ts} {C_TOOL}< {message}{C_RESET}"

    elif event_type == "decision":
        return f"  {ts} {C_DECISION}* {message}{C_RESET}"

    elif event_type == "reasoning":
        return f"  {ts} {C_COOLDOWN}  {message}{C_RESET}"

    elif event_type == "recommendation":
        return f"  {ts} {C_COOLDOWN}  tip: {message}{C_RESET}"

    elif event_type == "cooldown":
        return f"  {ts} {C_COOLDOWN}~ {message}{C_RESET}"

    elif event_type == "escalated":
        return f"  {ts} {C_ESCALATED}! {message}{C_RESET}"

    elif event_type == "error":
        return f"  {ts} {C_ERROR}! {message}{C_RESET}"

    else:
        return f"  {ts} {C_COOLDOWN}- {message}{C_RESET}"