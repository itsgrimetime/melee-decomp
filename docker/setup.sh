#!/bin/bash

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default values
INSTANCE_ID="${INSTANCE_ID:-default}"
NGINX_PORT="${NGINX_PORT:-80}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
DEBUGGER_PORT="${DEBUGGER_PORT:-5678}"

# Compose file selection
COMPOSE_FILES="-f docker-compose.yml"
if [ "$INSTANCE_ID" != "default" ]; then
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.parallel.yml"
fi

# Functions
print_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

check_prerequisites() {
    print_header "Checking Prerequisites"

    # Check for Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    print_success "Docker is installed: $(docker --version)"

    # Check for Docker Compose
    if ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not available. Please install Docker Compose."
        exit 1
    fi
    print_success "Docker Compose is available: $(docker compose version)"

    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
    print_success "Docker daemon is running"

    # Check for decomp.me submodule
    if [ ! -d "$PROJECT_ROOT/decomp.me" ]; then
        print_error "decomp.me submodule not found at $PROJECT_ROOT/decomp.me"
        print_info "Run: git submodule update --init --recursive"
        exit 1
    fi
    print_success "decomp.me submodule found"

    # Check for required files
    if [ ! -f "$PROJECT_ROOT/decomp.me/backend/Dockerfile" ]; then
        print_error "decomp.me backend Dockerfile not found"
        exit 1
    fi
    print_success "Required files present"
}

check_ports() {
    print_header "Checking Port Availability"

    local ports=("$NGINX_PORT" "$BACKEND_PORT" "$FRONTEND_PORT" "$POSTGRES_PORT" "$DEBUGGER_PORT")
    local port_names=("NGINX" "BACKEND" "FRONTEND" "POSTGRES" "DEBUGGER")
    local all_available=true

    for i in "${!ports[@]}"; do
        port="${ports[$i]}"
        name="${port_names[$i]}"

        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            print_warning "$name port $port is already in use"
            all_available=false
        else
            print_success "$name port $port is available"
        fi
    done

    if [ "$all_available" = false ]; then
        print_warning "Some ports are in use. You may want to use different ports or stop conflicting services."
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

build_images() {
    print_header "Building Docker Images"

    cd "$SCRIPT_DIR"

    export INSTANCE_ID NGINX_PORT BACKEND_PORT FRONTEND_PORT POSTGRES_PORT DEBUGGER_PORT

    print_info "This may take several minutes on first run..."
    if docker compose $COMPOSE_FILES build --progress=plain; then
        print_success "Images built successfully"
    else
        print_error "Failed to build images"
        exit 1
    fi
}

start_services() {
    print_header "Starting Services"

    cd "$SCRIPT_DIR"

    export INSTANCE_ID NGINX_PORT BACKEND_PORT FRONTEND_PORT POSTGRES_PORT DEBUGGER_PORT

    if docker compose $COMPOSE_FILES up -d; then
        print_success "Services started"
    else
        print_error "Failed to start services"
        exit 1
    fi
}

wait_for_health() {
    print_header "Waiting for Services to be Healthy"

    cd "$SCRIPT_DIR"

    export INSTANCE_ID NGINX_PORT BACKEND_PORT FRONTEND_PORT POSTGRES_PORT DEBUGGER_PORT

    local max_wait=300  # 5 minutes
    local elapsed=0
    local services=("postgres" "backend" "frontend" "nginx")

    while [ $elapsed -lt $max_wait ]; do
        local all_healthy=true

        for service in "${services[@]}"; do
            local container_name="decompme_${service}_${INSTANCE_ID}"
            local health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "starting")

            if [ "$health" != "healthy" ]; then
                all_healthy=false
                break
            fi
        done

        if [ "$all_healthy" = true ]; then
            print_success "All services are healthy"
            return 0
        fi

        echo -ne "\rWaiting for services to be healthy... ${elapsed}s/${max_wait}s"
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo ""
    print_error "Services did not become healthy within ${max_wait}s"
    print_info "Check logs with: docker compose $COMPOSE_FILES logs"
    return 1
}

print_status() {
    print_header "Deployment Status"

    cd "$SCRIPT_DIR"

    export INSTANCE_ID NGINX_PORT BACKEND_PORT FRONTEND_PORT POSTGRES_PORT DEBUGGER_PORT

    docker compose $COMPOSE_FILES ps

    print_header "Access Information"
    print_success "Instance ID: $INSTANCE_ID"
    print_success "Frontend UI: http://localhost:$NGINX_PORT"
    print_success "Backend API: http://localhost:$NGINX_PORT/api"
    print_success "Direct Backend API: http://localhost:$BACKEND_PORT/api"
    print_success "Direct Frontend: http://localhost:$FRONTEND_PORT"
    print_success "PostgreSQL: localhost:$POSTGRES_PORT"
    print_success "Debugger: localhost:$DEBUGGER_PORT"

    print_header "Useful Commands"
    echo "View logs:       cd $SCRIPT_DIR && docker compose $COMPOSE_FILES logs -f"
    echo "Stop services:   cd $SCRIPT_DIR && docker compose $COMPOSE_FILES down"
    echo "Restart:         cd $SCRIPT_DIR && docker compose $COMPOSE_FILES restart"
    echo "Clean volumes:   cd $SCRIPT_DIR && docker compose $COMPOSE_FILES down -v"
}

# Main execution
main() {
    print_header "decomp.me Local Setup - Instance: $INSTANCE_ID"

    # Parse command line arguments
    case "${1:-}" in
        --skip-checks)
            print_warning "Skipping prerequisite checks"
            ;;
        --build-only)
            check_prerequisites
            build_images
            print_success "Build complete"
            exit 0
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-checks   Skip prerequisite and port checks"
            echo "  --build-only    Only build images, don't start services"
            echo "  --help          Show this help message"
            echo ""
            echo "Environment Variables:"
            echo "  INSTANCE_ID     Unique identifier for this instance (default: default)"
            echo "  NGINX_PORT      Nginx port (default: 80)"
            echo "  BACKEND_PORT    Backend API port (default: 8000)"
            echo "  FRONTEND_PORT   Frontend dev server port (default: 8080)"
            echo "  POSTGRES_PORT   PostgreSQL port (default: 5432)"
            echo "  DEBUGGER_PORT   Python debugger port (default: 5678)"
            echo ""
            echo "Examples:"
            echo "  # Start default instance"
            echo "  ./setup.sh"
            echo ""
            echo "  # Start parallel instance on different ports"
            echo "  INSTANCE_ID=worker1 NGINX_PORT=8001 BACKEND_PORT=9001 FRONTEND_PORT=9101 POSTGRES_PORT=5433 ./setup.sh"
            exit 0
            ;;
        *)
            check_prerequisites
            check_ports
            ;;
    esac

    build_images
    start_services

    if wait_for_health; then
        print_status
        print_success "\nSetup complete! decomp.me is ready to use."
    else
        print_error "\nSetup completed with warnings. Check service logs for issues."
        print_status
        exit 1
    fi
}

# Run main function
main "$@"
