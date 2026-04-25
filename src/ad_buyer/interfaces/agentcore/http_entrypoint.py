"""AgentCore HTTP entrypoint for the IAB AAMP Buyer Agent.

Location: src/ad_buyer/interfaces/agentcore/http_entrypoint.py

Uses the BedrockAgentCoreApp wrapper required by Amazon Bedrock AgentCore.
Deploy via the ``agentcore`` CLI — see ``infra/aws/agentcore/deploy.sh``.

When SELLER_AGENT_URL is an AgentCore runtime ARN (starts with "arn:"),
the buyer routes prompts to the seller via InvokeAgentRuntime instead of
HTTP. This enables same-account agent-to-agent communication on AgentCore
without requiring the seller to expose an HTTP endpoint.

Local testing::

    pip install bedrock-agentcore
    python src/ad_buyer/interfaces/agentcore/http_entrypoint.py
    # In another terminal:
    curl -X POST http://localhost:8080/invocations \\
      -H "Content-Type: application/json" \\
      -d '{"prompt": "hello"}'
"""

import json
import logging
import os
import sys

# Add the src directory to Python path so ad_buyer is importable
# Three levels up: agentcore/ -> interfaces/ -> ad_buyer/ -> src/
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")

from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Lazy-initialized components
_chat = None
_seller_proxy = None


class AgentCoreSellerProxy:
    """Routes prompts to the seller agent via AgentCore InvokeAgentRuntime.

    Used when SELLER_AGENT_URL is an ARN instead of an HTTP URL.
    This enables agent-to-agent communication within AgentCore without
    requiring the seller to expose a separate HTTP endpoint.
    """

    def __init__(self, seller_arn: str, region: str = None):
        self.seller_arn = seller_arn
        self.region = region or os.environ.get("AWS_REGION", "us-west-2")
        self._client = None
        self._session_id = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self.region,
            )
        return self._client

    def invoke(self, prompt: str) -> str:
        """Send a prompt to the seller agent and return the response text."""
        import uuid

        client = self._get_client()

        # Reuse session for multi-turn context (negotiation steps).
        # The same session_id is passed both as runtimeSessionId (API-level)
        # and inside the payload so the seller can extract it regardless of
        # how AgentCore delivers it.
        if self._session_id is None:
            self._session_id = str(uuid.uuid4())

        payload = json.dumps({
            "prompt": prompt,
            "session_id": self._session_id,
            "buyer_tier": "preferred_agency",
        })

        try:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=self.seller_arn,
                qualifier="DEFAULT",
                payload=payload,
                runtimeSessionId=self._session_id,
            )

            # Handle the response — may be StreamingBody, list, str, or bytes
            response_body = response.get("response", "")

            # StreamingBody — read it
            if hasattr(response_body, "read"):
                raw = response_body.read()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                text = raw
            elif isinstance(response_body, list):
                parts = []
                for part in response_body:
                    if hasattr(part, "read"):
                        chunk = part.read()
                        parts.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
                    elif isinstance(part, bytes):
                        parts.append(part.decode("utf-8"))
                    elif isinstance(part, str):
                        if part.startswith("b'") and part.endswith("'"):
                            part = part[2:-1]
                        parts.append(part)
                text = "".join(parts)
            elif isinstance(response_body, bytes):
                text = response_body.decode("utf-8")
            else:
                text = str(response_body)

            # Try to parse as JSON and extract the response field
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    resp = data.get("response", data)
                    # Response might be a nested object with a "text" field
                    if isinstance(resp, dict) and "text" in resp:
                        return resp["text"]
                    elif isinstance(resp, str):
                        return resp
                    else:
                        return json.dumps(resp, indent=2)
                return text
            except (json.JSONDecodeError, TypeError):
                return text

        except Exception as exc:
            logger.error("Seller invocation failed: %s", exc)
            return f"Error contacting seller agent: {exc}"


def _get_seller_proxy():
    """Get or create the seller proxy if SELLER_AGENT_URL is an ARN."""
    global _seller_proxy
    if _seller_proxy is None:
        seller_url = os.environ.get("SELLER_AGENT_URL", "")
        if seller_url.startswith("arn:"):
            _seller_proxy = AgentCoreSellerProxy(seller_url)
            logger.info("Seller proxy initialized — ARN: %s", seller_url)
        else:
            logger.info("Seller URL is HTTP (%s) — proxy not needed", seller_url)
    return _seller_proxy


def _get_chat():
    """Get or create the ChatInterface."""
    global _chat
    if _chat is None:
        try:
            from ad_buyer.interfaces.chat.main import ChatInterface
            _chat = ChatInterface()
            sellers = _chat.get_connected_sellers()
            logger.info("Buyer ChatInterface ready — %d seller(s)", len(sellers))
        except Exception as exc:
            logger.warning("ChatInterface init failed (non-fatal): %s", exc)
            _chat = None
    return _chat


def _is_seller_query(prompt: str) -> bool:
    """Detect if a prompt should be routed to the seller agent."""
    seller_keywords = [
        "inventory", "products", "media kit", "rate card", "pricing",
        "negotiate", "deal", "book", "order", "distribute", "export",
        "list_products", "get_media_kit", "get_rate_card", "get_pricing",
        "request_quote", "transition_order", "export_deals",
        "distribute_deal", "list_packages", "create_deal",
        "ctv", "linear", "digital", "audio", "display",
        "cpm", "publisher", "seller",
    ]
    prompt_lower = prompt.lower()
    return any(kw in prompt_lower for kw in seller_keywords)


@app.entrypoint
def invoke(payload, context):
    """Handle an AgentCore invocation.

    Routing logic:
    1. If SELLER_AGENT_URL is an ARN and the prompt is seller-related,
       route directly to the seller via InvokeAgentRuntime.
    2. Otherwise, route through the buyer's ChatInterface (CrewAI flow).

    Args:
        payload: dict with ``prompt``, ``message``, or ``input`` field.
        context: AgentCore runtime context.

    Returns:
        dict with ``response`` (str) and ``metadata`` (dict).
    """
    prompt = (
        payload.get("prompt")
        or payload.get("message")
        or payload.get("input", "")
    )

    if not prompt:
        return {"error": "Missing 'prompt', 'message', or 'input' field"}

    # Check if we should route to seller via AgentCore proxy
    proxy = _get_seller_proxy()
    if proxy and _is_seller_query(prompt):
        logger.info("Routing to seller via AgentCore proxy: %s", prompt[:80])
        result = proxy.invoke(prompt)
        # Return as plain string — BedrockAgentCoreApp handles serialization
        # and AgentCore streams it in a format the UI can parse
        return result

    # Route through buyer's ChatInterface (CrewAI flow)
    chat = _get_chat()
    if chat is None:
        # Fallback: if ChatInterface fails but we have a seller proxy,
        # try routing to seller anyway
        if proxy:
            logger.info("ChatInterface unavailable — falling back to seller proxy")
            result = proxy.invoke(prompt)
            return result
        return {"error": "Agent not ready — ChatInterface failed to initialize"}

    try:
        result = chat.process_message(prompt)
        return {
            "response": result,
            "metadata": {"type": "portfolio_manager_response"},
        }
    except Exception as exc:
        logger.exception("Invocation failed: %s", exc)
        return {"error": "Invocation failed", "detail": str(exc)}


if __name__ == "__main__":
    app.run()
