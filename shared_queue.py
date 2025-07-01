# shared_queue.py
import asyncio
from typing import Dict

# Using a dictionary to store each user's message queue
message_queues: Dict[str, asyncio.Queue] = {}

# Token tracking for each user
user_token_usage: Dict[str, Dict] = {}

async def get_or_create_queue(user_id: str) -> asyncio.Queue:
    """Get or create a user's message queue"""
    if user_id not in message_queues:
        message_queues[user_id] = asyncio.Queue()
    return message_queues[user_id]

async def log_execution(message: str, user_id: str):
    """Write a message to a user's queue"""
    queue = await get_or_create_queue(user_id)
    await queue.put(message)

async def get_message(user_id: str):
    """Get a message from a user's queue"""
    queue = await get_or_create_queue(user_id)
    return await queue.get()

def queue_empty(user_id: str) -> bool:
    """Check if a user's queue is empty"""
    if user_id not in message_queues:
        return True
    return message_queues[user_id].empty()

def cleanup_queue(user_id: str):
    """Clean up a user's message queue"""
    if user_id in message_queues:
        del message_queues[user_id]
    # Also clean up token usage tracking
    if user_id in user_token_usage:
        del user_token_usage[user_id]

async def log_token_usage(user_id: str, prompt_tokens: int, completion_tokens: int, cost: float, model: str, action_description: str = ""):
    """Log token usage for a specific LLM call"""
    total_tokens = prompt_tokens + completion_tokens
    
    # Initialize user token tracking if needed
    if user_id not in user_token_usage:
        user_token_usage[user_id] = {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cost": 0.0,
            "calls": []
        }
    
    # Update totals
    user_token_usage[user_id]["total_prompt_tokens"] += prompt_tokens
    user_token_usage[user_id]["total_completion_tokens"] += completion_tokens
    user_token_usage[user_id]["total_cost"] += cost
    
    # Record this call
    call_info = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": cost,
        "model": model,
        "action": action_description
    }
    user_token_usage[user_id]["calls"].append(call_info)
    
    # Enhanced CLI logging
    print(f"\n═════════════════════════════════════════════════════════════════════════════════")
    print(f"🔄 LLM API CALL COMPLETED")
    print(f"─────────────────────────────────────────────────────────────────────────────────")
    if action_description:
        print(f"📝 Action: {action_description}")
    print(f"🤖 Model: {model}")
    print(f"📊 This Call:")
    print(f"   📨 Input Tokens:   {prompt_tokens:,}")
    print(f"   📤 Output Tokens:  {completion_tokens:,}")
    print(f"   🔢 Total Tokens:   {total_tokens:,}")
    print(f"   💰 Call Cost:      ${cost:.4f}")
    print(f"📈 Session Totals:")
    print(f"   📨 Total Input:    {user_token_usage[user_id]['total_prompt_tokens']:,}")
    print(f"   📤 Total Output:   {user_token_usage[user_id]['total_completion_tokens']:,}")
    print(f"   🔢 Total Tokens:   {user_token_usage[user_id]['total_prompt_tokens'] + user_token_usage[user_id]['total_completion_tokens']:,}")
    print(f"   💰 Total Cost:     ${user_token_usage[user_id]['total_cost']:.4f}")
    print(f"   📞 Total Calls:    {len(user_token_usage[user_id]['calls'])}")
    print(f"═════════════════════════════════════════════════════════════════════════════════\n")
    
    # Log to user's message queue for frontend
    if action_description:
        message = f"🔢 **Token Usage ({action_description})**: {total_tokens} tokens (prompt: {prompt_tokens}, completion: {completion_tokens}) | Cost: ${cost:.4f} | Model: {model}\n"
    else:
        message = f"🔢 **Token Usage**: {total_tokens} tokens (prompt: {prompt_tokens}, completion: {completion_tokens}) | Cost: ${cost:.4f} | Model: {model}\n"
    
    await log_execution(message, user_id)

async def log_total_token_usage(user_id: str):
    """Log the total token usage summary for a user"""
    if user_id in user_token_usage:
        usage = user_token_usage[user_id]
        total_tokens = usage["total_prompt_tokens"] + usage["total_completion_tokens"]
        total_calls = len(usage["calls"])
        
        summary = f"""
📊 **Total Token Usage Summary**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• **Total API Calls**: {total_calls}
• **Total Tokens**: {total_tokens:,} tokens
  - Prompt Tokens: {usage["total_prompt_tokens"]:,}
  - Completion Tokens: {usage["total_completion_tokens"]:,}
• **Total Cost**: ${usage["total_cost"]:.4f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        await log_execution(summary, user_id)
    else:
        await log_execution("📊 **No token usage recorded for this session**\n", user_id)

def get_user_token_usage(user_id: str) -> Dict:
    """Get the current token usage for a user"""
    return user_token_usage.get(user_id, {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost": 0.0,
        "calls": []
    })

def reset_user_token_usage(user_id: str):
    """Reset token usage tracking for a user (start fresh)"""
    user_token_usage[user_id] = {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost": 0.0,
        "calls": []
    }