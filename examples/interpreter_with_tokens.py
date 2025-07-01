#!/usr/bin/env python3
"""
DataInterpreter with Token Tracking Example

This example demonstrates how to use DataInterpreter directly and get token usage statistics.
"""
import asyncio
import sys
import os
import uuid
from pathlib import Path

# Setup paths
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'agent'))
sys.path.insert(0, project_root)  # For shared_queue

# Set environment for config
os.environ['METAGPT_ROOT'] = project_root

from metagpt.roles.di.data_interpreter import DataInterpreter
from metagpt.context import Context
from metagpt.config2 import Config
from shared_queue import get_user_token_usage, reset_user_token_usage

async def use_interpreter_with_tokens(query: str, task_description: str = "DataInterpreter Task"):
    """
    Use DataInterpreter with token tracking
    
    Args:
        query: The task/query to run
        task_description: Description for token tracking
    
    Returns:
        dict: Token usage statistics or None if failed
    """
    # 1. 创建唯一会话ID
    user_id = str(uuid.uuid4())
    reset_user_token_usage(user_id)
    
    print(f"🚀 Starting DataInterpreter with Token Tracking")
    print(f"Session ID: {user_id[:8]}...")
    print(f"Task: {task_description}")
    print("=" * 60)
    
    try:
        # 2. 创建DataInterpreter
        config = Config.default()
        context = Context(config=config)
        interpreter = DataInterpreter(
            use_reflection=True, 
            tools=["<all>"], 
            context=context
        )
        
        # 3. 启用Token跟踪
        interpreter.current_user_id = user_id
        if hasattr(interpreter, 'llm') and interpreter.llm:
            interpreter.llm.set_token_logging_context(user_id, task_description)
            print(f"✅ Token tracking enabled")
            print(f"   Model: {interpreter.llm.model}")
        else:
            print(f"⚠️ Warning: LLM not found, token tracking may not work")
        
        # Set working directory
        work_dir = Path("data") / "interpreter_output"
        work_dir.mkdir(parents=True, exist_ok=True)
        interpreter.set_workspace(str(work_dir))
        print(f"📁 Working directory: {work_dir}")
        
        print(f"\n🔄 Running task...")
        print(f"Query: {query}")
        print("-" * 60)
        
        # 4. 运行任务
        result = await interpreter.run(query)
        
        print(f"\n✅ Task completed!")
        
        # 5. 获取Token统计
        usage_stats = get_user_token_usage(user_id)
        
        if usage_stats and usage_stats['total_prompt_tokens'] > 0:
            print(f"\n📊 FINAL TOKEN STATISTICS:")
            print(f"=" * 60)
            print(f"  - Prompt Tokens: {usage_stats['total_prompt_tokens']:,}")
            print(f"  - Completion Tokens: {usage_stats['total_completion_tokens']:,}")
            print(f"  - Total Tokens: {usage_stats['total_prompt_tokens'] + usage_stats['total_completion_tokens']:,}")
            print(f"  - Session Cost: ${usage_stats['total_cost']:.4f}")
            print(f"  - Total API Calls: {len(usage_stats['calls'])}")
            print(f"=" * 60)
            
            return {
                'prompt_tokens': usage_stats['total_prompt_tokens'],
                'completion_tokens': usage_stats['total_completion_tokens'],
                'total_tokens': usage_stats['total_prompt_tokens'] + usage_stats['total_completion_tokens'],
                'total_cost': usage_stats['total_cost'],
                'calls_count': len(usage_stats['calls']),
                'result': result
            }
        else:
            print(f"\n⚠️ No token usage data found")
            return None
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

async def example_data_analysis():
    """Example: Data analysis task"""
    query = """
    请帮我完成以下数据分析任务：
    1. 生成一个包含100个数据点的样本数据集，包含年龄(20-65岁)、收入(30000-120000)、支出(15000-80000)三个变量
    2. 计算基本统计信息（均值、中位数、标准差）
    3. 创建收入和支出的散点图，并添加回归线
    4. 分析年龄与收入的相关性
    5. 总结分析结果
    """
    
    stats = await use_interpreter_with_tokens(query, "Data Analysis Example")
    
    if stats:
        print(f"\n🎯 Example Results:")
        print(f"   Prompt Tokens: {stats['prompt_tokens']:,}")
        print(f"   Completion Tokens: {stats['completion_tokens']:,}")
        print(f"   Total Tokens: {stats['total_tokens']:,}")
        print(f"   Session Cost: ${stats['total_cost']:.4f}")
    
    return stats

async def example_simple_task():
    """Example: Simple task"""
    query = "生成10个随机数字，计算平均值，并创建一个柱状图"
    
    stats = await use_interpreter_with_tokens(query, "Simple Task Example")
    
    if stats:
        print(f"\n🎯 Simple Task Results:")
        print(f"   Total Tokens: {stats['total_tokens']:,}")
        print(f"   Cost: ${stats['total_cost']:.4f}")
    
    return stats

async def custom_task_example():
    """Run a custom task with user input"""
    print("📝 Custom Task Mode")
    print("Enter your data analysis task (or press Enter for default):")
    
    user_query = input().strip()
    if not user_query:
        user_query = "分析一下股票价格数据，生成一些基本的可视化图表"
        print(f"Using default query: {user_query}")
    
    stats = await use_interpreter_with_tokens(user_query, "Custom User Task")
    
    if stats:
        print(f"\n🎯 Your Task Results:")
        print(f"   Prompt Tokens: {stats['prompt_tokens']:,}")
        print(f"   Completion Tokens: {stats['completion_tokens']:,}")
        print(f"   Total Tokens: {stats['total_tokens']:,}")
        print(f"   Session Cost: ${stats['total_cost']:.4f}")
    
    return stats

def main():
    """Main function with example selection"""
    print("🔬 DataInterpreter Token Tracking Examples")
    print("=" * 50)
    print("Choose an example to run:")
    print("1. Data Analysis Example (comprehensive)")
    print("2. Simple Task Example (quick)")
    print("3. Custom Task (enter your own)")
    print("4. Exit")
    
    while True:
        choice = input("\nEnter your choice (1-4): ").strip()
        
        if choice == "1":
            print("\n🔬 Running Data Analysis Example...")
            stats = asyncio.run(example_data_analysis())
            break
        elif choice == "2":
            print("\n⚡ Running Simple Task Example...")
            stats = asyncio.run(example_simple_task())
            break
        elif choice == "3":
            print("\n✏️ Running Custom Task...")
            stats = asyncio.run(custom_task_example())
            break
        elif choice == "4":
            print("👋 Goodbye!")
            return
        else:
            print("❌ Invalid choice. Please enter 1, 2, 3, or 4.")
    
    if stats:
        print(f"\n📋 Final Summary:")
        print(f"   You used {stats['total_tokens']:,} tokens")
        print(f"   This cost ${stats['total_cost']:.4f}")
        print(f"   Across {stats['calls_count']} API calls")

if __name__ == "__main__":
    main()