$PYTHON = "D:\Users\陈独秀\AppData\Local\Programs\Python\Python314\python.exe"
$PROJECT = "C:\Users\陈独秀\ai-trader"

# 等待网络就绪
Start-Sleep -Seconds 10

# 启动知识库
Start-Process $PYTHON -ArgumentList "-m","uvicorn","app.main:app","--port","8766" -WorkingDirectory "C:\Users\陈独秀\knowledge-base\backend" -WindowStyle Hidden

# 启动仪表盘
Start-Process $PYTHON -ArgumentList "-m","uvicorn","dashboard:app","--host","0.0.0.0","--port","8501" -WorkingDirectory $PROJECT -WindowStyle Hidden

# 等服务启动
Start-Sleep -Seconds 5

# 启动监控
Start-Process $PYTHON -ArgumentList "main.py" -WorkingDirectory $PROJECT -WindowStyle Hidden
