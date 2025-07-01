# -*- encoding: utf-8 -*-
"""
@Date    :   2023/11/20 13:19:39
@Author  :   orange-crow
@File    :   write_analysis_code.py
"""
from __future__ import annotations

import json

from metagpt.actions import Action
from metagpt.prompts.di.write_analysis_code import (
    CHECK_DATA_PROMPT,
    DEBUG_REFLECTION_EXAMPLE,
    INTERPRETER_SYSTEM_MSG,
    REFLECTION_PROMPT,
    REFLECTION_SYSTEM_MSG,
    STRUCTUAL_PROMPT,
)
from metagpt.schema import Message, Plan
from metagpt.utils.common import CodeParser, NoMoneyException, remove_comments
from shared_queue import log_execution

class WriteAnalysisCode(Action):
    async def _debug_with_reflection(self, context: list[Message], working_memory: list[Message], user_id: str):
        reflection_prompt = REFLECTION_PROMPT.format(
            debug_example=DEBUG_REFLECTION_EXAMPLE,
            context=context,
            previous_impl=working_memory,
        )
        for i in range(2):
            try:
                rsp = await self._aask(reflection_prompt, system_msgs=[REFLECTION_SYSTEM_MSG], user_id=user_id, action_description="Code reflection")
                print("===========Reflection Response===========")
                print(CodeParser.parse_code(block=None, text=rsp))
                reflection = json.loads(CodeParser.parse_code(block=None, text=rsp))
                break
            except:
                print("Reflection Collection Error. Will Try Again.")
                continue

        try:
            await log_execution("### Code Failed Reason\n", user_id)
            await log_execution("🤔" + reflection["reflection"] + "\n", user_id)
            await log_execution("\n", user_id)
            await log_execution("---\n", user_id)
        except:
            pass

        return reflection["improved_impl"]

    async def run(
        self,
        user_requirement: str,
        plan_status: str = "",
        tool_info: str = "",
        working_memory: list[Message] = None,
        use_reflection: bool = False,
        user_id: str = "",
        **kwargs,
    ) -> str:
        print(f"[DEBUG] WriteAnalysisCode.run - user_id: {user_id}")

        if self.llm.cost_manager.total_cost >= self.llm.cost_manager.max_budget:
            await log_execution("### ❗ ❗ ❗ You have exceeded the single task token budget, please restart a conversation session\n", user_id)
            raise NoMoneyException(self.llm.cost_manager.total_cost, f"Insufficient funds: {self.llm.cost_manager.max_budget}")
            
        
        structual_prompt = STRUCTUAL_PROMPT.format(
            user_requirement=user_requirement,
            plan_status=plan_status,
            tool_info=tool_info,
        )

        working_memory = working_memory or []
        context = self.llm.format_msg([Message(content=structual_prompt, role="user")] + working_memory)

        # LLM call
        if use_reflection:
            code = await self._debug_with_reflection(context=context, working_memory=working_memory, user_id=user_id)
        else:
            rsp = await self.llm.aask(context, system_msgs=[INTERPRETER_SYSTEM_MSG], user_id=user_id, action_description="Generate analysis code", **kwargs)
            code = CodeParser.parse_code(block=None, text=rsp)

        return code

class CheckData(Action):
    async def run(self, plan: Plan) -> dict:
        finished_tasks = plan.get_finished_tasks()
        code_written = [remove_comments(task.code) for task in finished_tasks]
        code_written = "\n\n".join(code_written)
        prompt = CHECK_DATA_PROMPT.format(code_written=code_written)
        rsp = await self._aask(prompt)
        code = CodeParser.parse_code(block=None, text=rsp)
        return code