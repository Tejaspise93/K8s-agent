"""
agent.py -- The brain of the system.

When the watcher detects a problem, this module will:
1. Builds a prompt describing the situation
2. Sends it to model set in config.yaml via Ollama
3. The model responds with a tool call -- which tool to run and with what arguments
4. We execute that tool and send the result back to the model
5. Loop continues until the model gives a final decision
6. HIGH confidence -> act automatically
7. LOW confidence -> pause and ask the user [y/n]

The model never touches the cluster. It only reads and decides what actions to take.
We perform everything and sent results to the model.
"""

import json
import time
from openai import OpenAI
from agent.config import cfg
from agent.logger import write

from agent.tools import (
    get_cluster_state,
    get_pod_logs,
    get_pod_events,
    get_resource_usage,
    get_resource_limits,
    get_deployment_status,
    restart_pod,
    scale_deployment,
    rollback_deployment,
)

# ---------------------------------------------------------------------------
# Ollama client setup
# ---------------------------------------------------------------------------

# api_key="ollama" is a dummy value -- Ollama doesn't need auth locally.
ollama_client = OpenAI(
    base_url=cfg.ollama_base_url,
    api_key="ollama",
)
MODEL          = cfg.model
# Maximum number of tool model calls before we give up.
# Stops if the model is stuck in taking decisions
MAX_ITERATIONS = cfg.max_iterations

# ---------------------------------------------------------------------------
# Tool definitions -- setting tools so LLM knows what tools exist
# ---------------------------------------------------------------------------
# When the model wants to call a tool, it returns the tool name and arguments
# as structured data -- we then call the actual Python function ourselves.


# Read-only tools -- the ONLY tools the model can call during investigation mode.
# Action tools (restart, scale, rollback) are NEVER given to the model so it doesn't make changes.
# The model decides WHICH action to take in its final decision.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_cluster_state",
            "description": "Get the current state of all pods in a namespace. Use this first to get an overview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace. Default: default"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "Fetch recent logs from a pod. Use this to see application errors and crash reasons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {"type": "string", "description": "Namespace of the pod"},
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_events",
            "description": "Fetch Kubernetes events for a pod. Use this to diagnose scheduling failures and OOM kills.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {"type": "string", "description": "Namespace of the pod"},
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_resource_usage",
            "description": "Get live CPU and memory usage for a pod from the metrics server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {"type": "string", "description": "Namespace of the pod"},
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_resource_limits",
            "description": "Get configured CPU and memory requests and limits for a pod.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {"type": "string", "description": "Namespace of the pod"},
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deployment_status",
            "description": "Get replica counts and rollout status for a deployment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deployment_name": {"type": "string", "description": "Name of the deployment"},
                    "namespace": {"type": "string", "description": "Namespace of the deployment"},
                },
                "required": ["deployment_name"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# mapping tools names to actual Python functions
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "get_cluster_state": get_cluster_state,
    "get_pod_logs": get_pod_logs,
    "get_pod_events": get_pod_events,
    "get_resource_usage": get_resource_usage,
    "get_resource_limits": get_resource_limits,
    "get_deployment_status": get_deployment_status,
    "restart_pod": restart_pod,
    "scale_deployment": scale_deployment,
    "rollback_deployment": rollback_deployment,
}

# ---------------------------------------------------------------------------
# System prompt -- tells the model its role and how to behave
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a Kubernetes operations agent. Investigate problems and recommend a fix.

YOU ONLY HAVE INVESTIGATION TOOLS. You cannot restart, scale, or rollback anything directly.
A separate system executes the action from your final JSON.

STRICT RULES:
1. Call get_pod_logs AND get_pod_events before deciding. Always both.
2. Never call the same tool twice.
3. After both tools, output ONLY valid JSON. No explanation. No markdown. No extra text.

HOW TO DECIDE:
- CrashLoopBackOff -> action should be "restart_pod", target is the pod name
- OOMKilled -> action should be "restart_pod", target is the pod name
- Pending -> action should be "none", explain in reasoning
- Unclear cause but pod is crashing → action "restart_pod", confidence "LOW"
- Unclear cause and pod is not crashing → action "none", confidence "LOW"
- Clear crash with logs confirming exit code or OOM -> use confidence "HIGH"

TARGET RULES:
- For restart_pod: target must be the exact pod name
- For scale_deployment or rollback_deployment: target must be the deployment name
- If you are not fixing anything: action is "none"

OUTPUT FORMAT -- valid JSON only, nothing else:
{"confidence":"HIGH","action":"restart_pod","target":"exact-pod-name","namespace":"default","reasoning":"specific one sentence explanation based on what logs showed","recommendation":"optional operator tip"}
"""

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def execute_tool(tool_name, tool_args):
    """
    Execute a tool by name with the given arguments.
    Returns the result as a JSON string to send back to the model.

    The model sends arguments as a dict -- we unpack them as keyword
    arguments to the Python function using **tool_args.
    """
    func = TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        result = func(**tool_args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})


def parse_final_decision(text):
    """
    Extract the JSON decision block from the model's response text.

    Returns a dict if parsing succeeds, None if the response isn't a decision yet.
    """
    if not text:
        return None

    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        cleaned = cleaned.split("\n", 1)[-1]
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]

    cleaned = cleaned.strip()

    # Only try to parse if it looks like a JSON object
    if not cleaned.startswith("{"):
        return None

    try:
        data = json.loads(cleaned)
        # Validate it has the fields we expect from a final decision
        if "confidence" in data and "action" in data:
            return data
    except json.JSONDecodeError:
        pass

    return None


def run_agent(anomaly, callback=None):
    """
    The main agentic loop. Takes an anomaly detected by the watcher and
    runs an investigation + decision cycle.

    Args:
        anomaly: dict from watcher.detect_anomalies() -- contains pod name,
                 namespace, anomaly type, and severity
        callback: optional function called with status updates for the display
                  module. Signature: callback(event_type, message)
                  event_type is one of: "investigating", "tool_call",
                  "tool_result", "decision", "error"

    Returns:
        A decision dict:
        {
          "confidence": "HIGH" or "LOW",
          "action": "restart_pod" / "scale_deployment" / "rollback_deployment" / "none",
          "target": "pod-or-deployment-name",
          "namespace": "default",
          "reasoning": "...",
          "recommendation": "..."
        }
        Or None if the agent failed to reach a decision.
    """
    def emit(event_type, message):
        """Send a status update to the display layer if a callback is registered."""
        if callback:
            callback(event_type, message)

    emit("investigating", f"Investigating {anomaly['type']} on {anomaly['pod']}")

    # Build the initial user message describing the problem
    initial_message = (
        f"Problem detected in Kubernetes cluster:\n"
        f"Pod: {anomaly['pod']}\n"
        f"Namespace: {anomaly['namespace']}\n"
        f"Issue type: {anomaly['type']}\n"
        f"Detail: {anomaly['detail']}\n"
        f"Severity: {anomaly['severity']}\n\n"
        f"Please investigate and decide on the best course of action."
    )

    # conversation_history holds the full message thread we send to the model.
    # We append to it after every tool call so the model has full context.
    conversation_history = [
        {"role": "user", "content": initial_message}
    ]


    called_tools = set()

    # ---------------------------------------------------------------------------
    # Agentic loop
    # ---------------------------------------------------------------------------
    for iteration in range(MAX_ITERATIONS):

        emit("investigating", f"Thinking... (round {iteration + 1}/{MAX_ITERATIONS})")

        try:
            response = ollama_client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *conversation_history,
                ],
                tools=TOOL_DEFINITIONS,
                # tool_choice="auto" lets the model decide whether to call a tool
                # or give a final text response
                tool_choice="auto",
                # Keep max_tokens reasonable -- we're on CPU, large responses are slow
                max_tokens=cfg.max_tokens,
            )
        except Exception as e:
            emit("error", f"Ollama API error: {str(e)}")
            write("ERROR", f"Ollama API error: {str(e)}")
            return None

        message = response.choices[0].message

        # --- Check if the model wants to call a tool ---
        if message.tool_calls:
            # Append the assistant's tool call request to history
            conversation_history.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            })

            # Execute each tool the model requested
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args_str = tool_call.function.arguments

                if tool_name in called_tools:
                    conversation_history.append({
                        "role": "user",
                        "content": (
                            f"You already called {tool_name} and have that data. "
                            "Do not repeat tool calls. "
                            "You have enough information -- output the final JSON decision now."
                        ),
                    })
                    continue

                # Mark this tool as called before executing
                called_tools.add(tool_name)

                emit("tool_call", f"Calling {tool_name}...")

                # Parse the arguments -- they come as a JSON string
                try:
                    tool_args = json.loads(tool_args_str)
                except json.JSONDecodeError:
                    tool_args = {}

                # Execute the tool and get the result
                tool_result = execute_tool(tool_name, tool_args)

                emit("tool_result", f"{tool_name} returned data")

                # Add the tool result to history so the model can read it
                # The tool_call_id links this result to the request above
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

            # Continue the loop -- model will read the tool results next iteration
            continue

# --- No tool call -- model gave a text response ---
        response_text = message.content or ""

        decision = parse_final_decision(response_text)

        if decision:
            emit("decision", f"Decision reached: {decision.get('action')} "
                             f"(confidence: {decision.get('confidence')})")
            return decision

        # Model gave plain text instead of a tool call or final JSON.
        # Count how many tool calls have happened so far in this conversation.
        tool_calls_made = sum(
            1 for m in conversation_history
            if isinstance(m.get("tool_calls"), list) and len(m["tool_calls"]) > 0
        )

        if tool_calls_made >= 2:
            # Enough investigation done -- push the model hard to decide now.
            conversation_history.append({
                "role": "assistant",
                "content": response_text,
            })
            conversation_history.append({
                "role": "user",
                "content": (
                    "You have gathered enough information. "
                    "Output ONLY the final JSON decision now. "
                    "No explanation. No text. Just the JSON object."
                ),
            })
        else:
            # Not enough tools called yet -- push the model to keep investigating.
            conversation_history.append({
                "role": "assistant",
                "content": response_text,
            })
            conversation_history.append({
                "role": "user",
                "content": (
                    f"You have only called {tool_calls_made} tool(s). "
                    "You must call at least one more tool before deciding. "
                    "Call the next appropriate tool now."
                ),
            })

    # Reached MAX_ITERATIONS without a decision
    emit("error", f"Agent reached max iterations ({MAX_ITERATIONS}) without deciding")
    write("WARNING", f"Agent reached max iterations on pod: {anomaly['pod']}")
    return None


def ask_user_confirmation(anomaly, decision):
    """
    Pause and ask the user to confirm a LOW confidence action.
    stops everything untill user responds with y or n.

    Returns True if user confirms, False if user declines.
    """
    print("\n" + "="*60)
    print(f"  [?] Agent is unsure -- human confirmation required")
    print(f"      Pod/Resource : {anomaly['pod']}")
    print(f"      Problem      : {anomaly['type']} -- {anomaly['detail']}")
    print(f"      Proposed     : {decision.get('action')} -> {decision.get('target')}")
    print(f"      Reasoning    : {decision.get('reasoning')}")
    print("="*60)

    while True:
        try:
            answer = input("  Proceed? [y/n]: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            elif answer in ("n", "no"):
                return False
            else:
                print("  Please type y or n")
        except (KeyboardInterrupt, EOFError):
            return False