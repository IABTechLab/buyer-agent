"""Patch for CrewAI BedrockCompletion — fixes tool execution with Bedrock Converse API.

Addresses two bugs in crewai==1.14.1 (crewai/llms/providers/bedrock/completion.py):

Bug 1: Orphaned toolUse/toolResult blocks cause ValidationException
  - CrewAI's agent executor adds OpenAI-format tool_calls to message history
  - _format_messages_for_converse converts them to Bedrock toolUse blocks
  - But messages from previous executor iterations may have toolUse blocks
    without matching toolResult blocks (the executor ran the tool but the
    result message got separated or dropped during message truncation)
  - Bedrock Converse rejects: "Expected toolResult blocks for Ids: tooluse_..."

Bug 2: ReAct fallback drops tool arguments
  - When the executor falls back to ReAct text parsing (instead of native
    tool calling), it extracts "Action Input:" from the LLM text response
  - The ReAct parser sometimes fails to parse the JSON, defaulting to {}
  - This only happens when tools aren't passed to call(), causing
    supports_function_calling() to route to _invoke_loop_react

Fix: Monkey-patch _handle_converse to sanitize messages before every
Bedrock API call, stripping orphaned toolUse/toolResult blocks.

Usage:
    from patches.crewai_bedrock_fix import apply_patches
    apply_patches()  # Call once at startup

Or in deploy.sh / Dockerfile:
    python -c "from patches.crewai_bedrock_fix import apply_patches; apply_patches()"

Compatible with: crewai>=1.10.0,<=1.14.1
"""

import json
import logging

logger = logging.getLogger(__name__)

_patched = False


def apply_patches():
    """Apply all Bedrock Converse API fixes to CrewAI. Idempotent."""
    global _patched
    if _patched:
        return

    _patch_handle_converse()
    _patched = True
    logger.info("CrewAI Bedrock patches applied successfully")


def _patch_handle_converse():
    """Patch BedrockCompletion._handle_converse to sanitize messages.

    Wraps the original method to strip orphaned toolUse/toolResult blocks
    before every Bedrock Converse API call. This prevents ValidationException
    when the message history has mismatched tool blocks from previous
    executor iterations.
    """
    try:
        from crewai.llms.providers.bedrock.completion import BedrockCompletion
    except ImportError:
        logger.warning("BedrockCompletion not available — skipping patch")
        return

    original_handle = BedrockCompletion._handle_converse

    def _patched_handle_converse(self, messages, body, *args, **kwargs):
        """Sanitize messages before every Bedrock Converse API call."""
        if isinstance(messages, list):
            messages = _sanitize_tool_blocks(messages)
        return original_handle(self, messages, body, *args, **kwargs)

    BedrockCompletion._handle_converse = _patched_handle_converse
    logger.info("Patched BedrockCompletion._handle_converse (message sanitization)")


def _sanitize_tool_blocks(messages: list) -> list:
    """Remove orphaned toolUse and toolResult blocks from message history.

    Bedrock Converse API requires:
    1. Every assistant toolUse block must have a matching user toolResult
    2. Every user toolResult block must have a matching assistant toolUse
    3. toolUse and toolResult must be in consecutive assistant/user pairs

    This function scans the message list and:
    - For each assistant message with toolUse blocks, checks if the next
      user message has matching toolResult blocks (by toolUseId)
    - If not matched, strips the toolUse blocks (keeps text blocks)
    - For each user message with toolResult blocks, checks if the previous
      assistant message has matching toolUse blocks
    - If not matched, strips the toolResult blocks (keeps text blocks)

    This handles both directions of orphaning:
    - Executor added toolUse but result is in a different message format
    - Message truncation removed one half of a toolUse/toolResult pair
    """
    if not messages:
        return messages

    fixed = []
    i = 0

    while i < len(messages):
        msg = messages[i]

        if not isinstance(msg, dict):
            fixed.append(msg)
            i += 1
            continue

        role = msg.get("role")
        content = msg.get("content", [])

        if not isinstance(content, list):
            fixed.append(msg)
            i += 1
            continue

        # --- Assistant messages with toolUse blocks ---
        if role == "assistant":
            tool_use_ids = set()
            for block in content:
                if isinstance(block, dict) and "toolUse" in block:
                    tu = block["toolUse"]
                    if isinstance(tu, dict) and "toolUseId" in tu:
                        tool_use_ids.add(tu["toolUseId"])

            if tool_use_ids:
                # Check if next message is a user message with matching toolResults
                next_msg = messages[i + 1] if i + 1 < len(messages) else None
                matched = False

                if (
                    next_msg
                    and isinstance(next_msg, dict)
                    and next_msg.get("role") == "user"
                ):
                    next_content = next_msg.get("content", [])
                    if isinstance(next_content, list):
                        result_ids = set()
                        for block in next_content:
                            if isinstance(block, dict) and "toolResult" in block:
                                tr = block["toolResult"]
                                if isinstance(tr, dict) and "toolUseId" in tr:
                                    result_ids.add(tr["toolUseId"])
                        matched = tool_use_ids.issubset(result_ids)

                if not matched:
                    # Strip toolUse blocks, keep text blocks
                    text_blocks = [
                        b for b in content
                        if isinstance(b, dict) and "text" in b
                    ]
                    if text_blocks:
                        fixed.append({"role": "assistant", "content": text_blocks})
                    # else: drop the message entirely (was only toolUse)
                    i += 1
                    continue

        # --- User messages with toolResult blocks ---
        elif role == "user":
            result_ids = set()
            for block in content:
                if isinstance(block, dict) and "toolResult" in block:
                    tr = block["toolResult"]
                    if isinstance(tr, dict) and "toolUseId" in tr:
                        result_ids.add(tr["toolUseId"])

            if result_ids:
                # Check if previous message (in fixed list) has matching toolUse
                prev_msg = fixed[-1] if fixed else None
                use_ids = set()

                if (
                    prev_msg
                    and isinstance(prev_msg, dict)
                    and prev_msg.get("role") == "assistant"
                ):
                    prev_content = prev_msg.get("content", [])
                    if isinstance(prev_content, list):
                        for block in prev_content:
                            if isinstance(block, dict) and "toolUse" in block:
                                tu = block["toolUse"]
                                if isinstance(tu, dict) and "toolUseId" in tu:
                                    use_ids.add(tu["toolUseId"])

                if not result_ids.issubset(use_ids):
                    # Only strip toolResult blocks whose toolUseId is NOT in use_ids
                    kept_blocks = [
                        b for b in content
                        if not (
                            isinstance(b, dict)
                            and "toolResult" in b
                            and isinstance(b["toolResult"], dict)
                            and b["toolResult"].get("toolUseId") not in use_ids
                        )
                    ]
                    if kept_blocks:
                        fixed.append({"role": "user", "content": kept_blocks})
                    i += 1
                    continue

        fixed.append(msg)
        i += 1

    return fixed
