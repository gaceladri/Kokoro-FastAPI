set PYTHONUTF8=1
set USE_GPU=true
set USE_ONNX=false
set PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\api
set MODEL_DIR=src\models
set VOICES_DIR=src\voices\v1_0
set WEB_PLAYER_PATH=%PROJECT_ROOT%\web

call uv pip install -e ".[gpu]"
call uv run uvicorn api.src.main:app --reload --host 0.0.0.0 --port 8880