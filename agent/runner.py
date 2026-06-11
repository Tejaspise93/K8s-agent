import time
import sys
from agent.watcher import connect_to_cluster, get_pod_snapshot, detect_anomalies
from agent.agent import run_agent, ask_user_confirmation, check_ollama_health
from agent.tools import restart_pod, scale_deployment, rollback_deployment
from agent.cooldown import CooldownTracker
from agent.utils import get_deployment_name
from agent.display import init_display, render_screen, log_event, show_confirmation_screen
from agent.logger import write
from agent.config import cfg

POLL_INTERVAL = cfg.poll_interval
NAMESPACE     = cfg.namespace
cooldown      = CooldownTracker(
    window_seconds=cfg.cooldown_window_seconds,
    max_attempts=cfg.cooldown_max_attempts
)





def handle_decision(anomaly, decision):
    if decision is None:
        log_event("error", f"Agent failed to reach a decision for {anomaly['pod']}")
        return

    confidence = decision.get("confidence", "LOW")
    action     = decision.get("action", "none")
    reasoning  = decision.get("reasoning", "")
    recommendation = decision.get("recommendation", "")

    log_event("decision", f"Decision: {action} (confidence: {confidence})")
    if reasoning:
        log_event("reasoning", reasoning)
    if recommendation:
        log_event("recommendation", recommendation)

    if action == "none":
        log_event("cooldown", "No action taken.")
        return

    if confidence == "HIGH":
        log_event("decision", f"HIGH confidence -- executing {action} automatically")
        execute_decision(decision)
    else:
        show_confirmation_screen(anomaly, decision)
        confirmed = ask_user_confirmation(anomaly, decision)
        if confirmed:
            log_event("decision", f"User confirmed -- executing {action}")
            execute_decision(decision)
        else:
            log_event("cooldown", "User declined -- no action taken")


def execute_decision(decision):
    """Call the appropriate tool based on the agent's decision."""

    action = decision.get("action")
    target = decision.get("target")
    namespace = decision.get("namespace", "default")

    stable_target = get_deployment_name(target)

    allowed, reason = cooldown.is_allowed(stable_target, action)
    if not allowed:
        if reason == "escalated":
            attempts = cooldown.get_attempts(stable_target, action)
            log_event("escalated", f"{action} on {stable_target} has failed {attempts} times -- human review needed")
        else:
            log_event("cooldown", f"Cooldown active for {action} on {stable_target} -- {reason}")
        return

    if action == "restart_pod":
        result = restart_pod(target, namespace)
        log_event("decision", f"restart_pod -> {result.get('result')} -- {result.get('note')}")

    elif action == "scale_deployment":
        replicas = decision.get("replicas", 2)
        result = scale_deployment(target, namespace, replicas=replicas)
        log_event("decision", f"scale_deployment -> {result.get('result')} -- {result.get('note')}")

    elif action == "rollback_deployment":
        result = rollback_deployment(target, namespace)
        log_event("decision", f"rollback_deployment -> {result.get('result')} -- {result.get('note')}")

    cooldown.record(stable_target, action)


def run_app():
    print("K8s Agentic AI Monitor")
    print("Connecting to cluster...\n")

    try:
        core_api = connect_to_cluster()
        check_ollama_health()
        init_display(namespace=NAMESPACE, poll_interval=POLL_INTERVAL)
        write("INFO", f"Monitor started -- watching namespace: {NAMESPACE}, poll interval: {POLL_INTERVAL}s")
        print(f"Connected. Watching namespace: '{NAMESPACE}'")
        print(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")
    except Exception as e:
        write("ERROR", f"Failed to connect to cluster: {e}")
        print(f"Failed to connect: {e}")
        sys.exit(1)

    try:
        while True:
            pods = get_pod_snapshot(core_api, namespace=NAMESPACE)
            anomalies = detect_anomalies(pods)

            render_screen(pods, anomalies)  # redraws everything
            sys.stdout.flush()  #helps with redraw

            for anomaly in anomalies:
                pod = anomaly["pod"]
                atype = anomaly["type"]
                stable_name = get_deployment_name(pod)

                allowed, reason = cooldown.is_allowed(stable_name, f"investigate_{atype}")
                if not allowed:
                    if reason == "escalated":
                        log_event("escalated", f"{stable_name} keeps failing -- human review needed")
                    else:
                        log_event("cooldown", f"{stable_name} already investigated -- {reason}")
                    continue

                cooldown.record(stable_name, f"investigate_{atype}")
                log_event("investigating", f"Sending {pod} to agent")
                decision = run_agent(anomaly, callback=log_event)
                handle_decision(anomaly, decision)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        write("INFO", "Monitor stopped by user")
        print("\nShutting down. Goodbye.")
        sys.exit(0)
