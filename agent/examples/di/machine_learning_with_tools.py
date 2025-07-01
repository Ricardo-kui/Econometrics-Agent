import asyncio

from metagpt.actions import WriteAnalysisCode
from metagpt.roles.di.data_interpreter import DataInterpreter
from metagpt.utils.recovery_util import save_history
from shared_queue import log_execution, reset_user_token_usage, log_total_token_usage


# async def main(requirement: str):
#     role = DataInterpreter(use_reflection=True, tools=["<all>"])
#     # role = DataInterpreter(use_reflection=True)
#     await role.run(requirement)
#     save_history(role=role)



async def main_generator_with_interpreter(interpreter: DataInterpreter, requirement: str, user_id: str):
    await log_execution("#### 🔥Starting main function\n", user_id)
    
    # Initialize/reset token tracking for this user session
    reset_user_token_usage(user_id)
    
    # Set token logging context for the LLM
    print(f"[DEBUG] Setting token logging context for user_id: {user_id}")
    interpreter.llm.set_token_logging_context(user_id, "Econometric Analysis")
    print(f"[DEBUG] Token logging context set. Current user_id: {interpreter.llm._current_user_id}")
    
    # Get initial token state to track session usage
    initial_costs = interpreter.llm.get_costs()
    await log_execution(f"📊 **Starting new session** - Current total tokens: {initial_costs.total_prompt_tokens + initial_costs.total_completion_tokens}\n", user_id)
    
    try:
        role = interpreter  # 假设 'interpreter' 类似于 'role'
        role.set_actions([WriteAnalysisCode])
        role._set_state(0)
        
        await log_execution("🚀 **Starting AI Agent Processing**\n", user_id)
        await role.run(requirement, user_id)
        
        # Get final token state and calculate usage for this session
        final_costs = interpreter.llm.get_costs()
        session_prompt_tokens = final_costs.total_prompt_tokens - initial_costs.total_prompt_tokens
        session_completion_tokens = final_costs.total_completion_tokens - initial_costs.total_completion_tokens
        session_cost = final_costs.total_cost - initial_costs.total_cost
        
        await log_execution(f"\n✅ **AI Agent Processing Complete**\n", user_id)
        
        # Add CLI logging for token usage
        print(f"\n{'='*80}")
        print(f"🔢 TOKEN USAGE SUMMARY")
        print(f"{'='*80}")
        print(f"Session Token Usage:")
        print(f"  - Prompt Tokens: {session_prompt_tokens:,}")
        print(f"  - Completion Tokens: {session_completion_tokens:,}")
        print(f"  - Total Tokens: {session_prompt_tokens + session_completion_tokens:,}")
        print(f"  - Session Cost: ${session_cost:.4f}")
        print(f"")
        print(f"Cumulative Token Usage:")
        print(f"  - Total Prompt Tokens: {final_costs.total_prompt_tokens:,}")
        print(f"  - Total Completion Tokens: {final_costs.total_completion_tokens:,}")
        print(f"  - Total Cost: ${final_costs.total_cost:.4f}")
        print(f"{'='*80}")
        
        if session_prompt_tokens > 0 or session_completion_tokens > 0:
            await log_execution(f"📈 **Session Token Usage**: {session_prompt_tokens} prompt + {session_completion_tokens} completion = {session_prompt_tokens + session_completion_tokens} total tokens | Cost: ${session_cost:.4f}\n", user_id)
        
        # Log the total summary
        await log_total_token_usage(user_id)
        
    finally:
        # Clear the token logging context
        interpreter.llm.clear_token_logging_context()
    
    save_history(role=role)
    await log_execution("#### Finished main function😊\n", user_id)

async def main_generator(requirement1: str):
    await log_execution("#### 🔥Starting main function\n", "1")
    # 创建两个DataInterpreter实例
    role1 = DataInterpreter(use_reflection=True, tools=["<all>"])
    # role2 = DataInterpreter(use_reflection=True, tools=["<all>"])
    # 同时运行两个实例
    await asyncio.gather(
        role1.run(requirement1, user_id="1"),
        # role2.run(requirement2)
    )
    
    # 设置两个实例的actions
    # role1.set_actions([WriteAnalysisCode])
    # role2.set_actions([WriteAnalysisCode])
    
    # # 重置状态
    # role1._set_state(0)
    # role2._set_state(0)
    
    # 保存历史记录
    save_history(role=role1)
    # save_history(role=role2)
    
    await log_execution("#### Finished main function😊\n", "1")


if __name__ == "__main__":
    requirement1 = "The tasks you want the econometric agent to complete"
    asyncio.run(main_generator(requirement1))
