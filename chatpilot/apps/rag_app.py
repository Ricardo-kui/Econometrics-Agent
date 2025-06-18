# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
"""
import os
import time
import uuid
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from typing import Optional
import json  # 添加这行到文件顶部的导入部分

from chatpilot.apps.auth_utils import get_current_user
from chatpilot.config import UPLOAD_DIR
from chatpilot.apps.web.models.users import Users

app = FastAPI()

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化app状态
if not hasattr(app.state, 'user_files'):
    app.state.user_files = {}

@app.post("/doc")
def store_doc(
        collection_name: Optional[str] = Form(None),
        file: UploadFile = File(...),
        user=Depends(get_current_user),
):
    """接收上传文件并存储到当前会话"""
    logger.debug(f"接收文件, 文件类型: {file.content_type}")
    try:
        # Import here to avoid circular imports
        from chatpilot.apps.openai_app import app as openai_app
        
        # 检查文件大小
        file_size = 0
        contents = bytearray()
        while chunk := file.file.read(8192):
            contents.extend(chunk)
            file_size += len(chunk)
            # 检查是否超过3MB
            if file_size > 3 * 1024 * 1024:  # 3MB in bytes
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File is too large. Maximum allowed size is 3MB. "
                )
        
        # 获取文件名
        filename = file.filename
        
        # 获取或创建当前用户的会话信息
        conversation = openai_app.state.USER_CONVERSATIONS.get(user.id)
        if conversation:
            session_id = conversation.get("session_id", "default")
        else:
            # 如果还没有会话，创建一个新的session_id（会在第一次对话时正式创建会话）
            timestamp = str(int(time.time()))
            random_part = str(uuid.uuid4()).split('-')[0]
            session_id = f"{timestamp}_{random_part}"
        
        # 创建会话专属目录
        session_upload_dir = f"{UPLOAD_DIR}/{user.id}/session_{session_id}"
        if not os.path.exists(session_upload_dir):
            os.makedirs(session_upload_dir, exist_ok=True)
            
        # 设置文件存储路径
        file_path = f"{session_upload_dir}/{filename}"
        
        # 保存文件
        with open(file_path, "wb") as f:
            f.write(contents)
            
        # 更新会话的文件列表
        if conversation:
            # 会话已存在，更新文件列表
            session_files = conversation.get("session_files", [])
            session_files.append(filename)
            conversation["session_files"] = session_files
            conversation["session_id"] = session_id  # 确保session_id存在
            logger.info(f"Added file {filename} to existing session {session_id} for user {user.id}")
        else:
            # 会话不存在，创建临时会话记录用于文件管理
            openai_app.state.USER_CONVERSATIONS[user.id] = {
                "interpreter": None,  # 会在第一次对话时创建
                "last_active": time.time(),
                "session_id": session_id,
                "session_files": [filename]
            }
            logger.info(f"Created new session {session_id} with file {filename} for user {user.id}")
        
        return {
            "status": True,
            "filename": filename,
            "session_id": session_id,
            "message": f"File uploaded to session {session_id}"
        }
        
    except HTTPException as he:
        logger.error(he)
        raise he
    except Exception as e:
        logger.error(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
