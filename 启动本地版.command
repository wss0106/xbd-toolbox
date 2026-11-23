#!/bin/bash
cd "$(dirname "$0")"
echo "正在启动 PhotoKit…"
python3 server.py &
sleep 1
open "http://127.0.0.1:8765"
echo "已在浏览器打开 http://127.0.0.1:8765"
echo "关闭此窗口即停止服务（或在终端按 Ctrl+C）"
wait
