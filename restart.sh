#!/bin/bash
# =============================================================================
# TradingAgents-CN 本地一键重启脚本
# =============================================================================
# 适用于 macOS / Linux 本地开发环境，不依赖 Docker
#
# 用法：
#   ./restart.sh                 # 停止已有本地服务，重新启动 MongoDB/Redis/后端/前端
#   ./restart.sh --init          # 启动前执行数据库初始化（首次部署推荐）
#   ./restart.sh --stop          # 仅停止本地服务
#   ./restart.sh --status        # 查看本地服务运行状态
#   ./restart.sh --logs          # 启动后跟踪后端+前端日志
#   ./restart.sh --no-services   # 不启动/检查 MongoDB 和 Redis（假设已在外部启动）
#   ./restart.sh -h, --help      # 显示帮助
#
# 注意：
#   - 需要 Python 3.10+ 虚拟环境（默认查找 env/ 或 venv/）
#   - macOS 下通过 brew services 管理 MongoDB / Redis
#   - 后端日志：logs/backend.log
#   - 前端日志：logs/frontend.log
# =============================================================================

set -euo pipefail

# --------------------------- 颜色与日志工具 ---------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $1"; }

# --------------------------- 配置 ---------------------------
PID_FILE=".restart_pids"
BACKEND_LOG="logs/backend.log"
FRONTEND_LOG="logs/frontend.log"
BACKEND_PORT=8000
FRONTEND_PORT=5173   # Vite 默认端口；也可能是 3000

RUN_INIT=false
ONLY_STOP=false
SHOW_STATUS=false
FOLLOW_LOGS=false
MANAGE_SERVICES=true

# --------------------------- 参数解析 ---------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --init)
            RUN_INIT=true
            shift
            ;;
        --stop)
            ONLY_STOP=true
            shift
            ;;
        --status)
            SHOW_STATUS=true
            shift
            ;;
        --logs)
            FOLLOW_LOGS=true
            shift
            ;;
        --no-services)
            MANAGE_SERVICES=false
            shift
            ;;
        -h|--help)
            echo "TradingAgents-CN 本地一键重启脚本"
            echo ""
            echo "用法: ./restart.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --init         启动前执行数据库初始化（首次部署推荐）"
            echo "  --stop         仅停止本地服务"
            echo "  --status       查看本地服务运行状态"
            echo "  --logs         启动后跟踪后端+前端日志"
            echo "  --no-services  不启动/检查 MongoDB 和 Redis"
            echo "  -h, --help     显示帮助信息"
            echo ""
            echo "示例:"
            echo "  ./restart.sh                 # 一键启动/重启本地服务"
            echo "  ./restart.sh --init          # 首次启动，先初始化数据库"
            echo "  ./restart.sh --stop          # 停止所有本地服务"
            echo "  ./restart.sh --logs          # 启动并跟踪日志"
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            log_info "使用 ./restart.sh --help 查看帮助"
            exit 1
            ;;
    esac
done

# --------------------------- 通用工具 ---------------------------
check_command() {
    command -v "$1" &>/dev/null
}

is_port_in_use() {
    local port="$1"
    if check_command lsof; then
        lsof -i ":${port}" -sTCP:LISTEN &>/dev/null
    elif check_command netstat; then
        netstat -an 2>/dev/null | grep -q ":${port} .*LISTEN"
    elif check_command ss; then
        ss -ltn 2>/dev/null | grep -q ":${port}"
    else
        return 1
    fi
}

kill_process() {
    local pid="$1"
    local name="$2"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        log_info "停止 ${name} (PID: ${pid})"
        kill "${pid}" 2>/dev/null || true
        for _ in {1..10}; do
            if ! kill -0 "${pid}" 2>/dev/null; then
                return 0
            fi
            sleep 0.5
        done
        if kill -0 "${pid}" 2>/dev/null; then
            log_warn "强制终止 ${name} (PID: ${pid})"
            kill -9 "${pid}" 2>/dev/null || true
        fi
    fi
}

# --------------------------- 虚拟环境 ---------------------------
activate_venv() {
    local venv_paths=("env" "venv" ".venv")
    for venv in "${venv_paths[@]}"; do
        if [[ -f "${venv}/bin/activate" ]]; then
            # shellcheck source=/dev/null
            source "${venv}/bin/activate"
            log_info "已激活虚拟环境: ${venv}"
            return 0
        fi
    done

    log_error "未找到虚拟环境"
    log_info "请先在项目根目录创建虚拟环境:"
    echo -e "  ${YELLOW}python3 -m venv env${NC}"
    echo -e "  ${YELLOW}source env/bin/activate${NC}"
    echo -e "  ${YELLOW}pip install -r requirements.txt${NC}"
    exit 1
}

# --------------------------- 服务管理（MongoDB / Redis） ---------------------------
check_brew_service() {
    local service_name="$1"
    if brew services list 2>/dev/null | grep -q "^${service_name}"; then
        return 0
    fi
    return 1
}

is_brew_service_running() {
    local service_name="$1"
    local state
    state=$(brew services list 2>/dev/null | awk -v name="${service_name}" '$1 == name {print $2}')
    [[ "${state}" == "started" ]]
}

start_brew_service() {
    local service_name="$1"
    log_step "启动 ${service_name}..."
    if brew services start "${service_name}" >/dev/null 2>&1; then
        log_info "${service_name} 已启动"
        return 0
    else
        log_error "${service_name} 启动失败"
        return 1
    fi
}

manage_services() {
    if [[ "${MANAGE_SERVICES}" != true ]]; then
        log_info "跳过 MongoDB / Redis 服务检查"
        return 0
    fi

    if ! check_command brew; then
        log_warn "未找到 brew 命令，无法自动管理 MongoDB / Redis"
        log_info "请确保 MongoDB 和 Redis 已手动启动"
        return 0
    fi

    log_step "检查本地依赖服务..."

    # MongoDB
    if check_brew_service "mongodb-community"; then
        if is_brew_service_running "mongodb-community"; then
            log_info "MongoDB (mongodb-community) 正在运行"
        else
            start_brew_service "mongodb-community"
        fi
    elif check_brew_service "mongodb-community@4.4" || check_brew_service "mongodb-community@5.0" || check_brew_service "mongodb-community@6.0" || check_brew_service "mongodb-community@7.0"; then
        log_info "检测到已安装的 MongoDB 版本服务"
        # 启动第一个找到的版本
        for svc in mongodb-community@7.0 mongodb-community@6.0 mongodb-community@5.0 mongodb-community@4.4; do
            if check_brew_service "${svc}"; then
                if is_brew_service_running "${svc}"; then
                    log_info "MongoDB (${svc}) 正在运行"
                else
                    start_brew_service "${svc}"
                fi
                break
            fi
        done
    else
        log_warn "未通过 brew 找到 MongoDB 服务"
        echo -e "  安装参考: ${YELLOW}brew tap mongodb/brew && brew install mongodb-community${NC}"
    fi

    # Redis
    if check_brew_service "redis"; then
        if is_brew_service_running "redis"; then
            log_info "Redis 正在运行"
        else
            start_brew_service "redis"
        fi
    else
        log_warn "未通过 brew 找到 Redis 服务"
        echo -e "  安装参考: ${YELLOW}brew install redis${NC}"
    fi

    # 等待服务就绪
    log_step "等待 MongoDB / Redis 就绪..."
    sleep 2
}

# --------------------------- 环境配置检查 ---------------------------
check_env() {
    if [[ ! -f ".env" ]]; then
        log_warn "未找到 .env 文件"
        if [[ -f ".env.example" ]]; then
            log_info "正在从 .env.example 复制生成 .env..."
            cp .env.example .env
            log_warn "请编辑 .env 文件填入真实 API 密钥后重新运行本脚本"
            exit 1
        else
            log_error "未找到 .env.example，无法自动生成 .env"
            exit 1
        fi
    fi

    # 简单检查关键占位符
    local placeholder_keys=("JWT_SECRET" "CSRF_SECRET")
    local has_placeholder=false
    for key in "${placeholder_keys[@]}"; do
        if grep -qE "^${key}=your-.*-change-in-production" .env 2>/dev/null || grep -qE "^${key}=change-me" .env 2>/dev/null; then
            log_warn ".env 中的 ${key} 仍为默认占位符"
            has_placeholder=true
        fi
    done
    if [[ "${has_placeholder}" == true ]]; then
        log_warn "建议修改默认密钥后再用于生产环境"
    fi
}

# --------------------------- 数据库初始化 ---------------------------
run_db_init() {
    log_step "执行数据库初始化..."
    if [[ ! -f "scripts/import_config_and_create_user.py" ]]; then
        log_warn "未找到 scripts/import_config_and_create_user.py，跳过初始化"
        return 0
    fi
    python scripts/import_config_and_create_user.py --host
}

# --------------------------- 启动核心服务 ---------------------------
stop_services() {
    log_step "停止已有本地服务..."

    # 从 PID 文件停止
    if [[ -f "${PID_FILE}" ]]; then
        while IFS=: read -r name pid; do
            [[ -z "${pid}" ]] && continue
            kill_process "${pid}" "${name}"
        done < "${PID_FILE}"
        rm -f "${PID_FILE}"
    fi

    # 兜底：按端口清理进程
    if check_command lsof; then
        for port in "${BACKEND_PORT}" "${FRONTEND_PORT}" 3000; do
            local pids
            pids=$(lsof -t -i ":${port}" 2>/dev/null || true)
            if [[ -n "${pids}" ]]; then
                log_warn "发现端口 ${port} 仍被占用，正在清理..."
                echo "${pids}" | xargs kill -9 2>/dev/null || true
            fi
        done
    fi

    log_info "本地服务已停止"
}

start_backend() {
    log_step "启动后端服务..."

    if is_port_in_use "${BACKEND_PORT}"; then
        log_error "端口 ${BACKEND_PORT} 已被占用"
        exit 1
    fi

    mkdir -p logs
    nohup python -m app > "${BACKEND_LOG}" 2>&1 &
    local pid=$!
    echo "backend:${pid}" > "${PID_FILE}"
    log_info "后端已启动 (PID: ${pid})，日志: ${BACKEND_LOG}"
}

start_frontend() {
    log_step "启动前端服务..."

    if [[ ! -d "frontend" ]]; then
        log_error "frontend 目录不存在"
        return 1
    fi

    if [[ ! -d "frontend/node_modules" ]]; then
        log_warn "前端依赖未安装，正在安装..."
        (cd frontend && npm install)
    fi

    if is_port_in_use "${FRONTEND_PORT}"; then
        log_warn "端口 ${FRONTEND_PORT} 已被占用，尝试使用 3000"
        FRONTEND_PORT=3000
        if is_port_in_use "${FRONTEND_PORT}"; then
            log_error "端口 3000 也被占用"
            return 1
        fi
    fi

    mkdir -p logs
    (cd frontend && nohup npm run dev > "../${FRONTEND_LOG}" 2>&1 &)
    local pid=$!
    echo "frontend:${pid}" >> "${PID_FILE}"
    log_info "前端已启动 (PID: ${pid})，日志: ${FRONTEND_LOG}"
}

show_status() {
    if [[ ! -f "${PID_FILE}" ]]; then
        log_info "没有记录到本地服务进程"
        return 0
    fi

    log_info "本地服务状态:"
    while IFS=: read -r name pid; do
        [[ -z "${pid}" ]] && continue
        if kill -0 "${pid}" 2>/dev/null; then
            echo -e "  ${GREEN}●${NC} ${name} (PID: ${pid}) 运行中"
        else
            echo -e "  ${RED}●${NC} ${name} (PID: ${pid}) 已停止"
        fi
    done < "${PID_FILE}"
}

wait_for_backend() {
    log_step "等待后端服务就绪..."
    local url="http://127.0.0.1:${BACKEND_PORT}/api/health"
    for i in {1..30}; do
        if curl -sf "${url}" >/dev/null 2>&1; then
            log_info "后端健康检查通过"
            return 0
        fi
        sleep 1
    done
    log_warn "后端健康检查超时，请查看日志: ${BACKEND_LOG}"
    return 1
}

# --------------------------- 主入口 ---------------------------
main() {
    echo ""
    log_info "TradingAgents-CN 本地一键重启脚本"
    echo ""

    # 仅停止
    if [[ "${ONLY_STOP}" == true ]]; then
        stop_services
        exit 0
    fi

    # 仅查看状态
    if [[ "${SHOW_STATUS}" == true ]]; then
        show_status
        exit 0
    fi

    # 激活虚拟环境
    activate_venv

    # 检查 .env
    check_env

    # 启动/检查 MongoDB / Redis
    manage_services

    # 数据库初始化
    if [[ "${RUN_INIT}" == true ]]; then
        run_db_init
    fi

    # 停止已有服务
    stop_services

    # 启动后端和前端
    start_backend
    start_frontend

    # 等待后端就绪
    if wait_for_backend; then
        echo ""
        log_info "🎉 本地服务启动完成"
        echo -e "  ${BLUE}前端页面${NC}: http://localhost:${FRONTEND_PORT}"
        echo -e "  ${BLUE}后端 API${NC}: http://localhost:${BACKEND_PORT}"
        echo -e "  ${BLUE}API 文档${NC}: http://localhost:${BACKEND_PORT}/docs"
        echo -e "  ${BLUE}健康检查${NC}: http://localhost:${BACKEND_PORT}/api/health"
        echo -e "  ${BLUE}默认账号${NC}: admin / admin123"
        echo ""
        log_info "常用命令:"
        echo -e "  停止服务: ${YELLOW}./restart.sh --stop${NC}"
        echo -e "  查看状态: ${YELLOW}./restart.sh --status${NC}"
        echo -e "  查看日志: ${YELLOW}tail -f logs/backend.log logs/frontend.log${NC}"
    fi

    # 跟踪日志
    if [[ "${FOLLOW_LOGS}" == true ]]; then
        echo ""
        log_info "正在跟踪日志，按 Ctrl+C 退出日志查看（不会停止服务）..."
        tail -f "${BACKEND_LOG}" "${FRONTEND_LOG}"
    fi
}

main "$@"
