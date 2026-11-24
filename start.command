#!/bin/bash
set -u

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PORT=8765
URL="http://127.0.0.1:${PORT}"
LOG_FILE="$ROOT/.server.log"

echo "PhotoKit · 启动 / 重启脚本"
echo "项目目录：$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：未找到 python3，请先安装 Python 3。"
  sleep 3
  exit 1
fi
if ! command -v lsof >/dev/null 2>&1; then
  echo "错误：未找到 lsof，无法检查端口占用。"
  sleep 3
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "错误：未找到 curl，无法确认服务是否启动成功。"
  sleep 3
  exit 1
fi

PIDS="$(lsof -ti tcp:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
  echo "检测到端口 ${PORT} 已被占用，准备重启项目服务…"
  for pid in $PIDS; do
    CMD="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$CMD" in
      *server.py*)
        echo "停止旧服务 PID $pid"
        kill "$pid" 2>/dev/null || true
        ;;
      *)
        echo "错误：端口 ${PORT} 被其他程序占用：PID $pid - $CMD"
        echo "请先关闭该程序后再运行本脚本。"
        sleep 3
        exit 1
        ;;
    esac
  done

  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! lsof -ti tcp:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if lsof -ti tcp:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
    echo "错误：端口 ${PORT} 仍被占用，重启失败。"
    sleep 3
    exit 1
  fi
else
  echo "端口 ${PORT} 空闲，准备启动项目服务…"
fi

nohup python3 "$ROOT/server.py" > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "服务进程已拉起 PID $NEW_PID"

READY=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS "$URL/api/status" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "错误：服务启动失败，最近日志如下："
  tail -n 30 "$LOG_FILE" 2>/dev/null || true
  sleep 5
  exit 1
fi

open "$URL" >/dev/null 2>&1 || true
echo "已在浏览器打开：$URL"
echo "日志文件：$LOG_FILE"
echo "再次运行或双击本脚本会自动重启服务。"

if [ -t 1 ]; then
  echo "3 秒后自动关闭本窗口。"
  sleep 3
fi
