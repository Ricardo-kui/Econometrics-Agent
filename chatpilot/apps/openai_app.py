# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
"""
import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

import aiohttp
import requests
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from langchain_core.messages import AIMessage, HumanMessage
from loguru import logger
from pydantic import BaseModel

from agent.examples.di.machine_learning_with_tools import main_generator_with_interpreter
from chatpilot.apps.auth_utils import (
    get_current_user,
    get_admin_user,
)
from chatpilot.apps.web.models.users import Users
from chatpilot.config import (
    OPENAI_API_BASE_URLS,
    OPENAI_API_KEYS,
    CACHE_DIR,
    DEFAULT_MODELS,
    MODEL_FILTER_ENABLED,
    MODEL_FILTER_LIST,
    SERPER_API_KEY,
    UPLOAD_DIR,
    OpenAIClientWrapper,
    RPD,
    RPM,
    MODEL_TYPE,
    AGENT_TYPE,
    FRAMEWORK,
)
from chatpilot.constants import ERROR_MESSAGES
from chatpilot.langchain_assistant import LangchainAssistant
from metagpt.roles.di.data_interpreter import DataInterpreter
# from examples.di.machine_learning_with_tools import main_generator
from shared_queue import cleanup_queue, queue_empty, get_message

# Import metagpt config classes at runtime to avoid path issues
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../../agent'))

try:
    from metagpt.config2 import Config
    from metagpt.configs.llm_config import LLMConfig
    METAGPT_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Failed to import metagpt config classes: {e}")
    METAGPT_AVAILABLE = False

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化 user_files
app.state.user_files = {}

app.state.MODEL_FILTER_ENABLED = MODEL_FILTER_ENABLED
app.state.MODEL_FILTER_LIST = MODEL_FILTER_LIST

app.state.OPENAI_API_KEYS = OPENAI_API_KEYS
app.state.OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS
if app.state.OPENAI_API_KEYS and app.state.OPENAI_API_KEYS[0]:
    # openai audio speech (TTS)
    app.state.CLIENT_MANAGER = OpenAIClientWrapper(
        keys=OPENAI_API_KEYS, base_urls=OPENAI_API_BASE_URLS
    )
else:
    app.state.CLIENT_MANAGER = None

# Get all models
app.state.MODELS = {}

# Agent for Assistant
app.state.AGENT = None
app.state.MODEL_NAME = None
# Key: user_id, Value: { "interpreter": DataInterpreter, "last_active": timestamp, "session_id": str, "session_files": list }
app.state.USER_CONVERSATIONS: Dict[str, Dict] = {}
app.state.conversation_lock = asyncio.Lock()

# User request tracking
user_request_tracker = defaultdict(lambda: {"daily": [], "minute": []})


def generate_session_id() -> str:
    """
    Generate a unique session ID for conversation sessions.
    
    Returns:
        str: A unique session identifier
    """
    timestamp = str(int(time.time()))
    random_part = str(uuid.uuid4()).split('-')[0]
    return f"{timestamp}_{random_part}"


def get_session_file_path(user_id: str, session_id: str, filename: str) -> str:
    """
    Generate the file path for a session-specific file.
    
    Args:
        user_id: The user ID
        session_id: The session ID
        filename: The filename
        
    Returns:
        str: The complete file path for the session file
    """
    return f"{UPLOAD_DIR}/{user_id}/session_{session_id}/{filename}"


def cleanup_expired_sessions(max_age_hours: int = 24):
    """
    Clean up expired sessions and their associated files.
    
    Args:
        max_age_hours: Maximum age in hours before a session is considered expired
    """
    current_time = time.time()
    expired_sessions = []
    
    for user_id, conversation in app.state.USER_CONVERSATIONS.items():
        last_active = conversation.get("last_active", 0)
        age_hours = (current_time - last_active) / 3600
        
        if age_hours > max_age_hours:
            expired_sessions.append(user_id)
            
            # Clean up session files
            session_id = conversation.get("session_id")
            if session_id:
                import shutil
                session_dir = f"{UPLOAD_DIR}/{user_id}/session_{session_id}"
                try:
                    if os.path.exists(session_dir):
                        shutil.rmtree(session_dir)
                        logger.info(f"Cleaned up session directory: {session_dir}")
                except Exception as e:
                    logger.error(f"Failed to clean up session directory {session_dir}: {e}")
    
    # Remove expired sessions from memory
    for user_id in expired_sessions:
        del app.state.USER_CONVERSATIONS[user_id]
        logger.info(f"Removed expired session for user: {user_id}")
    
    return len(expired_sessions)


def create_custom_config_for_model(model_name: str):
    """
    Create a custom Config with the specified model while preserving other settings from config2.yaml.
    
    Args:
        model_name: The model name selected from frontend
        
    Returns:
        Config: Custom configuration with the specified model, or None if metagpt is not available
    """
    if not METAGPT_AVAILABLE:
        logger.warning("MetaGPT config classes not available, cannot create custom config")
        return None
        
    try:
        # Load base configuration from config2.yaml
        base_config = Config.default()
        
        # Create custom LLM config with the selected model
        custom_llm_config = {
            "api_type": base_config.llm.api_type,
            "base_url": base_config.llm.base_url,
            "api_key": base_config.llm.api_key,
            "model": model_name,  # Override with frontend selected model
        }
        
        # Add proxy if configured
        if hasattr(base_config.llm, 'proxy') and base_config.llm.proxy:
            custom_llm_config["proxy"] = base_config.llm.proxy
            
        # Add temperature if configured
        if hasattr(base_config.llm, 'temperature') and base_config.llm.temperature is not None:
            custom_llm_config["temperature"] = base_config.llm.temperature
            
        # Add max_tokens if configured
        if hasattr(base_config.llm, 'max_tokens') and base_config.llm.max_tokens is not None:
            custom_llm_config["max_tokens"] = base_config.llm.max_tokens
        
        # Create new config with custom LLM configuration
        custom_config = Config.from_llm_config(custom_llm_config)
        
        # Preserve other configurations from base config
        custom_config.workspace = base_config.workspace
        custom_config.search = base_config.search
        custom_config.browser = base_config.browser
        custom_config.mermaid = base_config.mermaid
        custom_config.embedding = base_config.embedding
        custom_config.proxy = base_config.proxy
        
        logger.info(f"Created custom config for model: {model_name}, api_type: {custom_config.llm.api_type}, base_url: {custom_config.llm.base_url}")
        return custom_config
        
    except Exception as e:
        logger.error(f"Failed to create custom config for model {model_name}: {e}")
        # Fallback to default config
        if METAGPT_AVAILABLE:
            return Config.default()
        return None


async def request_rate_limiter(
        user=Depends(get_current_user),
        max_daily_requests: int = RPD,
        max_minute_requests: int = RPM
):
    """Unified request rate limiter for both RPD and RPM limits, with support for unlimited requests."""
    if max_daily_requests <= 0 and max_minute_requests <= 0:
        # if RPD and RPM are set to -1, do not limit requests
        return

    now = datetime.now()
    today = now.date()
    current_minute = now.replace(second=0, microsecond=0)

    user_requests = user_request_tracker[user.id]

    # if not unlimited, record and check the requests
    if max_daily_requests > 0:
        # clean up the expired daily requests
        user_requests["daily"] = [dt for dt in user_requests["daily"] if dt.date() == today]
        # check the daily request limit
        if len(user_requests["daily"]) >= max_daily_requests:
            logger.warning(f"Reach request rate limit, user: {user.email}, RPD: {max_daily_requests}")
            raise HTTPException(status_code=429, detail=ERROR_MESSAGES.RPD_LIMIT)

    if max_minute_requests > 0:
        # clean up the expired minute requests
        user_requests["minute"] = [dt for dt in user_requests["minute"] if dt > current_minute - timedelta(minutes=1)]
        # check the minute request limit
        if len(user_requests["minute"]) >= max_minute_requests:
            logger.warning(f"Reach request rate limit, user: {user.email}, RPM: {max_minute_requests}")
            raise HTTPException(status_code=429, detail=ERROR_MESSAGES.RPM_LIMIT)

    # record the new requests
    user_requests["daily"].append(now)
    user_requests["minute"].append(now)


def openai_chat_completion(client, messages, model, stream=True, temperature=0.7, max_tokens=4095):
    response = client.chat.completions.create(
        messages=messages,
        model=model,
        stream=stream,
        temperature=temperature,
        max_tokens=max_tokens)

    return response


async def is_related_conversation(previous_messages: List, new_message: str) -> bool:
    """
    Use LLM to determine if the new message is related to the existing conversation.
    """
    if not previous_messages:
        return False

    # transform previous messages to a string
    previous_message = "\n".join([f"No.{i + 1} message: {message}" for i, message in enumerate(previous_messages)])

    prompt = f"""
    You are very good at determining whether a user's latest input is related to their previous input. If it is related, please output `true`, and if it is not, please output `false`. (Only output the JSON structure).
    
    You can refer to the following examples:
    
    ## Previous input:
    i will give you my outlook email account and password, please help me login in and respond to an email to Lily Wang, the content is about thanks and I have sent an email to MEcon Office, I will wait for their response email.
    my email account: zhoutuo@connect.hku.hk
    password: 123456
    ## Latest input:
    my email account is zhoutuo@connect.hku
    ## Your output:
    {{"is_related": true}}
    
    ## Previous input:
    i will give you my outlook email account and password, please help me login in and respond to an email to Lily Wang, the content is about thanks and I have sent an email to MEcon Office, I will wait for their response email.
    my email account: zhoutuo@connect.hku.hk
    password: 123456
    ## Latest input:
    Please help me conduct a linear regression prediction for the Boston house price dataset, and print out the regression summary statistics table for the estimated coefficients. Discuss the economic results based on regression tables.
    ## Your output:
    {{"is_related": false}}
    
    Alright, let's begin:
    ## Previous input:
    {previous_message}
    ## Latest input:
    {new_message}
    ## Your output:
    """

    try:
        response = openai_chat_completion(
            client=app.state.CLIENT_MANAGER.get_client(),
            model="gpt-4o",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            stream=False
        )
        answer = response.choices[0].message.content.strip()
        bracket_index = answer.find('{')
        bracket_last = answer.rfind('}')
        answer = answer[bracket_index:bracket_last + 1]
        response_dict = json.loads(answer)
        return True if response_dict.get("is_related", False) else False
    except Exception as e:
        logger.error(f"Error when using LLM to determine if the new message is related to the existing conversation: {e}")
        # default to false, start a new conversation
        return False


@app.middleware("http")
async def check_url(request: Request, call_next):
    if len(app.state.MODELS) == 0:
        await get_all_models()

    response = await call_next(request)
    return response


class UrlsUpdateForm(BaseModel):
    urls: List[str]


class KeysUpdateForm(BaseModel):
    keys: List[str]


@app.get("/urls")
async def get_openai_urls(user=Depends(get_admin_user)):
    return {"OPENAI_API_BASE_URLS": app.state.OPENAI_API_BASE_URLS}


@app.post("/urls/update")
async def update_openai_urls(form_data: UrlsUpdateForm, user=Depends(get_admin_user)):
    app.state.OPENAI_API_BASE_URLS = form_data.urls
    logger.info(f"update app.state.OPENAI_API_BASE_URLS: {app.state.OPENAI_API_BASE_URLS}")
    return {"OPENAI_API_BASE_URLS": app.state.OPENAI_API_BASE_URLS}


@app.get("/keys")
async def get_openai_keys(user=Depends(get_admin_user)):
    return {"OPENAI_API_KEYS": app.state.OPENAI_API_KEYS}


@app.post("/keys/update")
async def update_openai_key(form_data: KeysUpdateForm, user=Depends(get_admin_user)):
    app.state.OPENAI_API_KEYS = form_data.keys
    logger.info(f"update app.state.OPENAI_API_KEYS: {app.state.OPENAI_API_KEYS}")
    return {"OPENAI_API_KEYS": app.state.OPENAI_API_KEYS}


@app.post("/audio/speech")
async def speech(
        request: Request,
        user=Depends(get_current_user),
        rate_limit=Depends(request_rate_limiter),
):
    r = None
    try:
        api_key, base_url = app.state.CLIENT_MANAGER.get_next_key_base_url()
        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = Path(CACHE_DIR).joinpath("./audio/speech/")
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
        file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            r = requests.post(
                url=f"{base_url}/audio/speech",
                data=body,
                headers=headers,
                stream=True,
            )
            r.raise_for_status()

            # Save the streaming content to a file
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(file_body_path, "w") as f:
                json.dump(json.loads(body.decode("utf-8")), f)

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            logger.error(e)
            error_detail = "Server Connection Error"
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        error_detail = f"External: {res['error']}"
                except:
                    error_detail = f"External: {e}"

            raise HTTPException(status_code=r.status_code, detail=error_detail)

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def fetch_url(url, key):
    try:
        headers = {"Authorization": f"Bearer {key}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                return await response.json()
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return None


def merge_models_lists(model_lists):
    merged_list = []

    for idx, models in enumerate(model_lists):
        merged_list.extend(
            [
                {**model, "urlIdx": idx}
                for model in models if model["id"]
            ]
        )
    return merged_list


async def get_all_models():
    logger.debug(f"model_type: {MODEL_TYPE}, base urls size: {len(app.state.OPENAI_API_BASE_URLS)}, "
                 f"keys size: {len(app.state.OPENAI_API_KEYS)}")
    if MODEL_TYPE == 'azure':
        models = {"data": [
            {"id": m, "name": m, "urlIdx": i} for i, m in enumerate(DEFAULT_MODELS)
        ]}
    else:
        if len(app.state.OPENAI_API_KEYS) == 1 and app.state.OPENAI_API_KEYS[0] == "":
            models = {"data": []}
        else:
            tasks = [
                fetch_url(f"{url}/models", app.state.OPENAI_API_KEYS[idx])
                for idx, url in enumerate(list(set(app.state.OPENAI_API_BASE_URLS)))
            ]
            responses = await asyncio.gather(*tasks)
            responses = list(
                filter(lambda x: x is not None and "error" not in x, responses)
            )
            models = {
                "data": merge_models_lists(
                    list(map(lambda response: response["data"], responses))
                )
            }
    app.state.MODELS = {model["id"]: model for model in models["data"]}
    logger.debug(f"get_all_models done, size: {len(app.state.MODELS)}, {app.state.MODELS.keys()}")
    return models


@app.get("/models")
@app.get("/models/{url_idx}")
async def get_models(url_idx: Optional[int] = None, user=Depends(get_current_user)):
    r = None
    if url_idx is None:
        models = await get_all_models()
        if app.state.MODEL_FILTER_ENABLED:
            if user.role == "user":
                models["data"] = list(
                    filter(
                        lambda model: model["id"] in app.state.MODEL_FILTER_LIST,
                        models["data"],
                    )
                )
                return models
        return models
    else:
        try:
            logger.debug(f"get_models url_idx: {url_idx}")
            url = app.state.OPENAI_API_BASE_URLS[url_idx]
            r = requests.request(method="GET", url=f"{url}/models")
            r.raise_for_status()

            response_data = r.json()
            if url:
                response_data["data"] = list(
                    filter(lambda model: model["id"], response_data["data"])
                )

            return response_data
        except Exception as e:
            logger.error(e)
            error_detail = "Server Connection Error"
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        error_detail = f"External: {res['error']}"
                except:
                    error_detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=error_detail,
            )


def proxy_other_request(api_key, base_url, path, body, method):
    """Proxy the request to OpenAI API with a modified body for gpt-4-vision-preview model."""
    # Try to decode the body of the request from bytes to a UTF-8 string (Require add max_token to fix gpt-4-vision)
    try:
        body = body.decode("utf-8")
        body = json.loads(body)

        model_idx = app.state.MODELS[body.get("model")]["urlIdx"]

        # Check if the model is "gpt-4-vision-preview" and set "max_tokens" to 4000
        # This is a workaround until OpenAI fixes the issue with this model
        if body.get("model") in ["gpt-4-vision-preview", "gpt-4-turbo", "gpt-4o", "gpt-4o-2024-05-13"]:
            if "max_tokens" not in body:
                body["max_tokens"] = 4000

        # Fix for ChatGPT calls failing because the num_ctx key is in body
        if "num_ctx" in body:
            # If 'num_ctx' is in the dictionary, delete it
            # Leaving it there generates an error with the
            # OpenAI API (Feb 2024)
            del body["num_ctx"]

        # Convert the modified body back to JSON
        body = json.dumps(body)
    except json.JSONDecodeError as e:
        logger.error(f"Error loading request body into a dictionary: {e}")

    target_url = f"{base_url}/{path}"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    r = requests.request(
        method=method,
        url=target_url,
        data=body,
        headers=headers,
        stream=True,
    )
    r.raise_for_status()
    # Check if response is SSE
    if "text/event-stream" in r.headers.get("Content-Type", ""):
        return StreamingResponse(
            r.iter_content(chunk_size=8192),
            status_code=r.status_code,
            headers=dict(r.headers),
        )
    else:
        response_data = r.json()
        return response_data


@app.api_route("/{path:path}", methods=["POST"])
async def proxy(
        path: str,
        request: Request,
        user=Depends(get_current_user),
        rate_limit=Depends(request_rate_limiter),
):
    method = request.method
    logger.debug(f"Proxying request to OpenAI: {path}, method: {method}, "
                 f"user: {user.id} {user.name} {user.email} {user.role}")

    body = await request.body()

    # try:
    body_dict = json.loads(body.decode("utf-8"))

    # logger.warning(f"body_dict: {body_dict}")

    model_name = body_dict.get('model', DEFAULT_MODELS[0] if DEFAULT_MODELS else "gpt-3.5-turbo")
    if app.state.MODEL_NAME is None:
        app.state.MODEL_NAME = model_name
    max_tokens = body_dict.get("max_tokens", 1024)
    temperature = body_dict.get("temperature", 0.7)
    num_ctx = body_dict.get('num_ctx', 1024)
    messages = body_dict.get("messages", [])
    logger.info(
        f"Using model: {model_name}, max_tokens: {max_tokens}, "
        f"num_ctx: {num_ctx}, messages size: {len(messages)}, user: {user.email}"
    )

    # deduct and update user quota
    if user.quota <= 0:
        raise HTTPException(status_code=400, detail="QUOTA_EXCEEDED")

    from chatpilot.apps.web.models.users import Users
    Users.update_user_by_id(
        user.id,
        {"quota": user.quota - 1})
    # if user:
    #     pass
    # else:
    #     raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT())

    # get the latest user input
    if messages:
        new_message = messages[-1].get('content', '')
    else:
        new_message = ""

    print(app.state.user_files)
    # Session-based file handling - check if current session has uploaded files
    conversation = app.state.USER_CONVERSATIONS.get(user.id)
    if conversation and conversation.get("session_files"):
        # Use the latest file from current session
        filename = conversation["session_files"][-1]
        session_id = conversation.get("session_id", "default")
        file_path = get_session_file_path(user.id, session_id, filename)
        suffix_prompt = f"\nIf the user's requirements involve or mention that it contains a dataset or file, this is the path address: {file_path}."
        new_message = f"{new_message} {suffix_prompt}"
        logger.info(f"Using session file: {filename} from session {session_id} for user {user.id}")

    # combine the user information before the latest user into a new list
    if len(messages) > 2:
        previous_messages = [message for message in messages[:-2] if message.get('role') == 'user']
    else:
        previous_messages = []

    print(f"previous_messages: {previous_messages}")
    print(f"new_message: {new_message}")

    if not new_message:
        raise HTTPException(status_code=400, detail="No message content provided.")

    logger.warning(f"new_message: {new_message}")

    async with app.state.conversation_lock:
        # use LLM to determine if it is related
        related = await is_related_conversation(previous_messages, new_message)
        logger.warning(f"question related: {related}")

        if related:
            # use existing conversation
            conversation = app.state.USER_CONVERSATIONS.get(user.id)
            print(f"existing conversation: {conversation}")
            if conversation:
                # Check if the model has changed
                stored_interpreter = conversation["interpreter"]
                stored_model = getattr(stored_interpreter.config.llm, 'model', None) if hasattr(stored_interpreter, 'config') else None
                
                if stored_model == model_name:
                    # Same model, reuse existing interpreter
                    interpreter: DataInterpreter = stored_interpreter
                    logger.warning(f"continue using existing conversation with same model, user_id: {user.id}")
                else:
                    # Model changed, create new interpreter with new config
                    logger.warning(f"model changed from {stored_model} to {model_name}, creating new interpreter")
                    custom_config = create_custom_config_for_model(model_name)
                    if custom_config:
                        interpreter = DataInterpreter(use_reflection=True, tools=["<all>"], config=custom_config)
                        logger.info(f"Created DataInterpreter with model: {interpreter.config.llm.model}")
                    else:
                        # Fallback to default DataInterpreter if custom config creation failed
                        interpreter = DataInterpreter(use_reflection=True, tools=["<all>"])
                        logger.warning(f"Created DataInterpreter with default config due to config creation failure")
                    # Preserve session info when model changes
                    app.state.USER_CONVERSATIONS[user.id] = {
                        "interpreter": interpreter,
                        "last_active": time.time(),
                        "session_id": conversation.get("session_id", generate_session_id()),
                        "session_files": conversation.get("session_files", [])
                    }
                    logger.warning(f"created new conversation with model {model_name}, user_id: {user.id}")
            else:
                # if there is no existing conversation, create a new one with custom config
                custom_config = create_custom_config_for_model(model_name)
                if custom_config:
                    interpreter = DataInterpreter(use_reflection=True, tools=["<all>"], config=custom_config)
                    logger.info(f"Created DataInterpreter with model: {interpreter.config.llm.model}")
                else:
                    # Fallback to default DataInterpreter if custom config creation failed
                    interpreter = DataInterpreter(use_reflection=True, tools=["<all>"])
                    logger.warning(f"Created DataInterpreter with default config due to config creation failure")
                # Create new conversation for related messages
                session_id = generate_session_id()
                app.state.USER_CONVERSATIONS[user.id] = {
                    "interpreter": interpreter,
                    "last_active": time.time(),
                    "session_id": session_id,
                    "session_files": []
                }
                logger.warning(f"start a new conversation with model {model_name}, user_id: {user.id}")
        else:
            # create a new conversation with custom config
            # todo check if we need to terminate the previous Jupyter kernel
            custom_config = create_custom_config_for_model(model_name)
            if custom_config:
                interpreter = DataInterpreter(use_reflection=True, tools=["<all>"], config=custom_config)
                logger.info(f"Created DataInterpreter with model: {interpreter.config.llm.model}")
            else:
                # Fallback to default DataInterpreter if custom config creation failed
                interpreter = DataInterpreter(use_reflection=True, tools=["<all>"])
                logger.warning(f"Created DataInterpreter with default config due to config creation failure")
            # Create new conversation for unrelated messages
            session_id = generate_session_id()
            app.state.USER_CONVERSATIONS[user.id] = {
                "interpreter": interpreter,
                "last_active": time.time(),
                "session_id": session_id,
                "session_files": []
            }
            logger.warning(f"start a new conversation with model {model_name}, user_id: {user.id}")

        # update the last active time of the conversation
        if user.id in app.state.USER_CONVERSATIONS:
            app.state.USER_CONVERSATIONS[user.id]["last_active"] = time.time()

    # process the conversation logic
    async def process_interpreter():
        # assume you need to pass the new message to the DataInterpreter's run method
        return await main_generator_with_interpreter(interpreter, new_message, user.id)
    async def event_generator():
        try:
            main_task = asyncio.create_task(process_interpreter())

            while True:
                if not queue_empty(user.id):
                    message = await get_message(user.id)
                    data_structure = {
                        "id": str(uuid.uuid4()),
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": message},
                                "finish_reason": None
                            }
                        ]
                    }
                    formatted_data = f"data: {json.dumps(data_structure)}\n\n"
                    yield formatted_data.encode()
                elif main_task.done():
                    break
                else:
                    await asyncio.sleep(0.1)

            if main_task.exception():
                raise main_task.exception()

            app.state.USER_CONVERSATIONS[user.id]["interpreter"] = interpreter
        finally:
            # clean up the user's message queue
            cleanup_queue(user.id)
    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get("/interpreter/model/{user_id}")
async def get_interpreter_model(user_id: str, user=Depends(get_current_user)):
    """
    Get the current model being used by the DataInterpreter for a specific user.
    """
    if user.id != user_id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    conversation = app.state.USER_CONVERSATIONS.get(user_id)
    if conversation and "interpreter" in conversation:
        interpreter = conversation["interpreter"]
        try:
            model_name = interpreter.config.llm.model if hasattr(interpreter, 'config') else "unknown"
            return {
                "user_id": user_id,
                "current_model": model_name,
                "last_active": conversation.get("last_active", 0)
            }
        except Exception as e:
            logger.error(f"Error getting interpreter model for user {user_id}: {e}")
            return {
                "user_id": user_id,
                "current_model": "error",
                "error": str(e)
            }
    else:
        return {
            "user_id": user_id,
            "current_model": None,
            "message": "No active conversation"
        }


@app.get("/session/files/{user_id}")
async def get_session_files(user_id: str, user=Depends(get_current_user)):
    """
    Get the list of files uploaded in the current session for a specific user.
    """
    if user.id != user_id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    conversation = app.state.USER_CONVERSATIONS.get(user_id)
    if conversation:
        session_files = conversation.get("session_files", [])
        session_id = conversation.get("session_id", "unknown")
        return {
            "user_id": user_id,
            "session_id": session_id,
            "files": session_files,
            "file_count": len(session_files)
        }
    else:
        return {
            "user_id": user_id,
            "session_id": None,
            "files": [],
            "file_count": 0,
            "message": "No active session"
        }


@app.delete("/session/files/{user_id}/{filename}")
async def delete_session_file(user_id: str, filename: str, user=Depends(get_current_user)):
    """
    Delete a specific file from the current session.
    """
    if user.id != user_id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    conversation = app.state.USER_CONVERSATIONS.get(user_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="No active session found")
        
    session_files = conversation.get("session_files", [])
    if filename not in session_files:
        raise HTTPException(status_code=404, detail="File not found in current session")
        
    session_id = conversation.get("session_id")
    if session_id:
        file_path = get_session_file_path(user_id, session_id, filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted session file: {file_path}")
            
            # Remove from session files list
            session_files.remove(filename)
            conversation["session_files"] = session_files
            
            return {
                "status": True,
                "message": f"File {filename} deleted from session {session_id}",
                "remaining_files": session_files
            }
        except Exception as e:
            logger.error(f"Failed to delete session file {file_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")
    else:
        raise HTTPException(status_code=500, detail="Session ID not found")


@app.post("/session/cleanup")
async def cleanup_sessions(max_age_hours: int = 24, user=Depends(get_current_user)):
    """
    Clean up expired sessions. Admin only endpoint.
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
        
    try:
        cleaned_count = cleanup_expired_sessions(max_age_hours)
        return {
            "status": True,
            "message": f"Cleaned up {cleaned_count} expired sessions",
            "cleaned_sessions": cleaned_count
        }
    except Exception as e:
        logger.error(f"Failed to cleanup sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


@app.get("/session/info/{user_id}")
async def get_session_info(user_id: str, user=Depends(get_current_user)):
    """
    Get detailed information about the current session.
    """
    if user.id != user_id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    conversation = app.state.USER_CONVERSATIONS.get(user_id)
    if conversation:
        session_id = conversation.get("session_id", "unknown")
        session_files = conversation.get("session_files", [])
        last_active = conversation.get("last_active", 0)
        has_interpreter = conversation.get("interpreter") is not None
        
        # Calculate session age
        current_time = time.time()
        age_hours = (current_time - last_active) / 3600 if last_active > 0 else 0
        
        return {
            "user_id": user_id,
            "session_id": session_id,
            "files": session_files,
            "file_count": len(session_files),
            "last_active": last_active,
            "age_hours": round(age_hours, 2),
            "has_interpreter": has_interpreter,
            "is_active": age_hours < 24
        }
    else:
        return {
            "user_id": user_id,
            "session_id": None,
            "files": [],
            "file_count": 0,
            "last_active": 0,
            "age_hours": 0,
            "has_interpreter": False,
            "is_active": False,
            "message": "No active session"
        }