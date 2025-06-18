# Session-Based File Upload Implementation

## Overview
This document describes the implementation that changes file uploads from user-based persistence to session-based isolation in the Econometrics Agent application.

## Problem Statement
Previously, files uploaded by users were stored permanently in their user account and would be included in every new conversation session. This caused:
- **File persistence across unrelated conversations**: Files uploaded for one task would appear in completely different conversations
- **No conversation isolation**: Users couldn't work on different projects simultaneously without file interference
- **Cluttered context**: AI would receive file paths even when not relevant to the current conversation

## Solution Architecture

### 1. Session-Based Storage Structure
**New Directory Structure**:
```
/db/uploads/{user_id}/
├── session_{session_id_1}/
│   ├── file1.csv
│   └── file2.xlsx
└── session_{session_id_2}/
    ├── different_file.pdf
    └── another_file.txt
```

**Previous Structure**:
```
/db/uploads/{user_id}/
├── file1.csv
├── file2.xlsx
├── different_file.pdf
└── another_file.txt  # All files mixed together
```

### 2. Enhanced Conversation State
**Updated USER_CONVERSATIONS Structure**:
```python
app.state.USER_CONVERSATIONS[user_id] = {
    "interpreter": DataInterpreter,
    "last_active": timestamp,
    "session_id": "1734294000_abc123",     # NEW: Unique session identifier
    "session_files": ["file1.csv", ...]   # NEW: Files for this session only
}
```

### 3. Session Management Logic

#### **Session Creation**:
- **New Conversation**: Creates new session with empty file list
- **Related Conversation**: Continues existing session with same files
- **Model Change**: Preserves session and files but creates new interpreter

#### **File Upload Flow**:
1. User uploads file via `/doc` endpoint
2. System checks for existing conversation/session
3. If session exists: Add file to existing session
4. If no session: Create new session for file
5. Store file in session-specific directory

#### **File Access in Conversations**:
```python
# OLD (user-based)
db_user = Users.get_user_by_id(user.id)
if db_user and db_user.uploaded_files:
    filename = db_user.uploaded_files[0]  # Latest user file
    file_path = f"{UPLOAD_DIR}/{user.id}/{filename}"

# NEW (session-based)
conversation = app.state.USER_CONVERSATIONS.get(user.id)
if conversation and conversation.get("session_files"):
    filename = conversation["session_files"][-1]  # Latest session file
    session_id = conversation.get("session_id", "default")
    file_path = get_session_file_path(user.id, session_id, filename)
```

## Implementation Details

### 1. Core Functions Added

#### **Session ID Generation**:
```python
def generate_session_id() -> str:
    timestamp = str(int(time.time()))
    random_part = str(uuid.uuid4()).split('-')[0]
    return f"{timestamp}_{random_part}"
```

#### **Session File Path Generation**:
```python
def get_session_file_path(user_id: str, session_id: str, filename: str) -> str:
    return f"{UPLOAD_DIR}/{user_id}/session_{session_id}/{filename}"
```

#### **Session Cleanup**:
```python
def cleanup_expired_sessions(max_age_hours: int = 24):
    # Removes expired sessions and their files
    # Frees up storage space automatically
```

### 2. Modified File Upload Endpoint

**Enhanced `/doc` Endpoint**:
- Detects current session or creates new one
- Stores files in session-specific directories
- Updates session file list instead of user account
- Returns session information in response

### 3. New Session Management Endpoints

#### **GET `/session/files/{user_id}`**
- Lists files in current session
- Returns session ID and file count

#### **DELETE `/session/files/{user_id}/{filename}`**
- Removes specific file from current session
- Cleans up file system and session state

#### **GET `/session/info/{user_id}`**
- Detailed session information
- Session age, file count, activity status

#### **POST `/session/cleanup`** (Admin only)
- Manually trigger cleanup of expired sessions
- Configurable max age threshold

## Benefits Achieved

### ✅ **Session Isolation**
- Files only available in the session where uploaded
- No cross-contamination between different conversations

### ✅ **Clean New Conversations**
- New sessions start with empty file lists
- Users can work on multiple projects simultaneously

### ✅ **Improved User Experience**
- More predictable file handling behavior
- Better organization of work sessions

### ✅ **Automatic Cleanup**
- Expired sessions and files are automatically removed
- Prevents storage bloat over time

### ✅ **Backward Compatibility**
- Existing conversation logic preserved
- Graceful fallbacks if session data missing

## File Lifecycle

### **Upload Phase**:
1. User uploads file via frontend
2. File stored in session directory
3. Added to session file list
4. Session updated in memory

### **Usage Phase**:
1. User sends chat message
2. System checks current session for files
3. Latest session file path added to AI context
4. AI processes with session-specific file

### **Cleanup Phase**:
1. Session becomes inactive (no messages for 24h)
2. Automatic cleanup removes session directory
3. Session removed from memory
4. Storage space freed

## Testing Results

The implementation has been tested and verified:

```
🧪 Testing Session-Based File Isolation
✓ Session IDs are unique
✓ Files are isolated between sessions  
✓ Files stored in different session directories
✓ New sessions start with empty file lists
```

## Files Modified

### **Primary Changes**:
- **`chatpilot/apps/openai_app.py`**: Session management and conversation logic
- **`chatpilot/apps/rag_app.py`**: File upload endpoint modifications

### **New Test Files**:
- **`test_session_files.py`**: Comprehensive test suite
- **`simple_test_session.py`**: Simple isolation verification

## Migration Strategy

### **For Existing Users**:
- Existing files in user directories remain untouched
- New uploads go to session directories
- Old files can be migrated on first access if needed

### **For New Users**:
- All files use session-based storage from the start
- Clean separation between different conversations

## Future Enhancements

- **Session Export**: Export entire session with files
- **Session Sharing**: Share sessions between users
- **File Versioning**: Track file changes within sessions
- **Session Templates**: Pre-configured session types
- **Advanced Cleanup**: Smart cleanup based on file importance

## API Usage Examples

### **Check Current Session Files**:
```bash
GET /session/files/{user_id}
# Response: {"session_id": "123_abc", "files": ["data.csv"], "file_count": 1}
```

### **Upload File to Session**:
```bash
POST /doc
# File uploaded to current session automatically
# Response: {"session_id": "123_abc", "filename": "data.csv"}
```

### **Delete Session File**:
```bash
DELETE /session/files/{user_id}/data.csv
# Response: {"status": true, "remaining_files": []}
```

This implementation successfully isolates file uploads to specific conversation sessions, providing users with a cleaner and more organized experience when working with the Econometrics Agent.