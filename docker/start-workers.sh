#!/bin/bash

# Helper script to start multiple decomp.me instances in parallel
# Usage: ./start-workers.sh [NUM_WORKERS]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color codes
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Default number of workers
NUM_WORKERS="${1:-3}"

# Port base ranges
NGINX_BASE=8000
BACKEND_BASE=9000
FRONTEND_BASE=9100
POSTGRES_BASE=5433
DEBUGGER_BASE=5679

echo -e "${BLUE}Starting $NUM_WORKERS decomp.me worker instances...${NC}\n"

# Start workers in background
for i in $(seq 1 $NUM_WORKERS); do
    echo -e "${YELLOW}Starting worker$i...${NC}"

    INSTANCE_ID="worker$i" \
    NGINX_PORT=$((NGINX_BASE + i)) \
    BACKEND_PORT=$((BACKEND_BASE + i)) \
    FRONTEND_PORT=$((FRONTEND_BASE + i)) \
    POSTGRES_PORT=$((POSTGRES_BASE + i - 1)) \
    DEBUGGER_PORT=$((DEBUGGER_BASE + i - 1)) \
    ./setup.sh --skip-checks > "worker$i.log" 2>&1 &

    echo -e "  Worker$i will be available at: http://localhost:$((NGINX_BASE + i))"
done

echo -e "\n${YELLOW}Waiting for all workers to start...${NC}"
wait

echo -e "\n${GREEN}All workers started!${NC}\n"

echo -e "${BLUE}Worker URLs:${NC}"
for i in $(seq 1 $NUM_WORKERS); do
    echo -e "  Worker $i: http://localhost:$((NGINX_BASE + i))/api"
done

echo -e "\n${BLUE}View logs:${NC}"
for i in $(seq 1 $NUM_WORKERS); do
    echo -e "  Worker $i: tail -f worker$i.log"
done

echo -e "\n${BLUE}Stop all workers:${NC}"
echo -e "  ./stop-workers.sh $NUM_WORKERS"
