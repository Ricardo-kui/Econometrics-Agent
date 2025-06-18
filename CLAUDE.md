# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an **Econometrics AI Agent** - a sophisticated full-stack application that combines LLM capabilities with specialized econometric analysis. The system democratizes access to advanced econometric methods through an intelligent agent that can perform comprehensive statistical analysis, generate academic-quality reports, and provide educational explanations.

## Architecture

### Core Components

- **`agent/`**: MetaGPT-based DataInterpreter with specialized econometric tools
- **`chatpilot/`**: FastAPI backend with authentication, session management, and API proxying
- **`web/`**: Svelte/TypeScript frontend with model selection and file upload
- **`config/`**: LLM configuration and API settings

### Key Design Patterns

1. **Agent-Based Architecture**: Built on MetaGPT's DataInterpreter with reflection capabilities for iterative error correction
2. **Session Isolation**: Files and conversations are isolated per session, not per user
3. **Dynamic Model Selection**: Frontend model choices dynamically configure backend LLM usage
4. **Multi-Provider Support**: Supports OpenAI, Azure, Anthropic, Ollama, and custom endpoints

## Development Commands

### Environment Setup
```bash
# Create virtual environment with uv (recommended)
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -r requirements.txt

# Alternative with pip
pip install -r requirements.txt
```

### Running the Application
```bash
# Start backend server
python -m chatpilot.main

# Start frontend (in separate terminal)
cd web
npm install
npm run dev

# Full stack development
# Backend runs on http://localhost:8000
# Frontend runs on http://localhost:5173
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_econometric_tools.py

# Run with coverage
pytest --cov=agent --cov=chatpilot

# Test session isolation
python test_session_files.py
python simple_test_session.py
```

### Configuration

#### Environment Variables (.env)
```bash
# Required
OPENAI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here

# Optional
AGENT_HOME_DIR=./agent  # Agent workspace directory
DATA_DIR=./data         # Data storage directory
UPLOAD_DIR=./db/uploads # File upload directory
```

#### LLM Configuration (config/config2.yaml)
```yaml
llm:
  api_type: "openai"
  model: "gpt-4"
  base_url: "https://api.openai.com/v1"
  api_key: "your_key_here"
```

## Key Features Implementation

### Session-Based File Management
- Files are isolated to conversation sessions, not user accounts
- New conversations start with empty file contexts
- Automatic cleanup of expired sessions (24h default)
- API endpoints: `/session/files/{user_id}`, `/session/info/{user_id}`

### Dynamic Model Selection
- Frontend ModelSelector component allows real-time model switching
- Backend creates custom MetaGPT configurations per model choice
- Preserves conversation continuity when models change
- Function: `create_custom_config_for_model(model_name)`

### Econometric Tool Library
Located in `agent/metagpt/tools/libs/econometric_analysis.py`:
- **OLS Regression**: Basic and robust standard errors
- **Instrumental Variables**: 2SLS, weak instrument diagnostics
- **Difference-in-Differences**: Treatment effect estimation
- **Regression Discontinuity**: Sharp and fuzzy designs
- **Propensity Score Methods**: Matching, weighting, stratification

## Development Workflow

### Adding New Econometric Methods
1. Implement in `agent/metagpt/tools/libs/econometric_analysis.py`
2. Add comprehensive docstrings with mathematical formulations
3. Include diagnostic tests and robustness checks
4. Write pytest tests in `tests/`
5. Update tool registry if needed

### Frontend Model Integration
1. Models fetched via `/models` endpoint
2. Selection sent in chat completion request body
3. Backend extracts model and creates custom configuration
4. DataInterpreter uses selected model for analysis

### Authentication & Authorization
- JWT-based authentication in `chatpilot/apps/auth_utils.py`
- User roles: "admin", "user"
- Rate limiting: configurable RPM/RPD limits
- Session management with FastAPI Depends

## Important File Patterns

### Configuration Priority
1. Environment variables (highest)
2. `config/config2.yaml`
3. Default values in code (lowest)

### Data Flow
1. **Upload**: Files → session directories (`/uploads/{user_id}/session_{session_id}/`)
2. **Processing**: DataInterpreter → Jupyter kernel → econometric analysis
3. **Response**: Streaming results via Server-Sent Events
4. **Storage**: Results in session context, files auto-cleanup

### Error Handling
- MetaGPT reflection for automatic error correction
- Graceful fallbacks for model/config failures
- Comprehensive logging with loguru
- HTTP exception handling with detailed error messages

## Research Context

This system is designed for:
- **Educational Use**: Making econometrics accessible to non-experts
- **Academic Research**: Reproducible analysis and paper validation
- **Professional Analysis**: Robust statistical methods with proper diagnostics
- **Methodology Development**: Testing and comparing econometric approaches

The agent emphasizes statistical rigor, proper interpretation of results, and clear communication of assumptions and limitations in econometric analysis.

## Answer Requirements

- The answer should be in the same language as the question.