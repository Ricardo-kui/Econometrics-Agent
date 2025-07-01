#!/usr/bin/env python3
"""
Simple DataInterpreter Usage with Token Tracking

A minimal example showing how to use DataInterpreter and get token statistics.
"""
import asyncio
import sys
import os
import uuid

# Setup paths
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'agent'))
sys.path.insert(0, project_root)  # For shared_queue
os.environ['METAGPT_ROOT'] = project_root

from metagpt.roles.di.data_interpreter import DataInterpreter
from metagpt.context import Context
from metagpt.config2 import Config
from shared_queue import get_user_token_usage, reset_user_token_usage

async def run_interpreter_with_tokens(query: str):
    """
    Run DataInterpreter with token tracking - simplified version
    
    Args:
        query: The task to run
        
    Returns:
        dict: Token usage statistics
    """
    # Create session
    user_id = str(uuid.uuid4())
    reset_user_token_usage(user_id)
    
    # Create interpreter
    config = Config.default()
    context = Context(config=config)
    interpreter = DataInterpreter(use_reflection=True, tools=["<all>"], context=context)
    
    # Enable token tracking
    interpreter.current_user_id = user_id
    if hasattr(interpreter, 'llm') and interpreter.llm:
        interpreter.llm.set_token_logging_context(user_id, "DataInterpreter Task")
    
    # Run task
    result = await interpreter.run(query)
    
    # Get token statistics
    usage_stats = get_user_token_usage(user_id)
    
    if usage_stats and usage_stats['total_prompt_tokens'] > 0:
        return {
            'prompt_tokens': usage_stats['total_prompt_tokens'],
            'completion_tokens': usage_stats['total_completion_tokens'],
            'total_tokens': usage_stats['total_prompt_tokens'] + usage_stats['total_completion_tokens'],
            'total_cost': usage_stats['total_cost'],
            'calls_count': len(usage_stats['calls']),
            'result': result
        }
    
    return None

# 使用示例
async def main():
    query = "生成5个随机数字并计算它们的平均值，然后创建一个简单的图表"
    
    print("🚀 Running DataInterpreter...")
    stats = await run_interpreter_with_tokens(query)
    
    if stats:
        print(f"\n📊 Token Usage Results:")
        print(f"  - Prompt Tokens: {stats['prompt_tokens']:,}")
        print(f"  - Completion Tokens: {stats['completion_tokens']:,}")
        print(f"  - Total Tokens: {stats['total_tokens']:,}")
        print(f"  - Session Cost: ${stats['total_cost']:.4f}")
    else:
        print("❌ No token data available")

if __name__ == "__main__":
    asyncio.run(main())