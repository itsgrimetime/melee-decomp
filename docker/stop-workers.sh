#!/bin/bash

# Helper script to stop multiple decomp.me instances
# Usage: ./stop-workers.sh [NUM_WORKERS]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color codes
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Default number of workers
NUM_WORKERS="${1:-3}"

# Parse arguments
REMOVE_VOLUMES=false
if [ "$2" = "--volumes" ] || [ "$2" = "-v" ]; then
    REMOVE_VOLUMES=true
fi

echo -e "${BLUE}Stopping $NUM_WORKERS decomp.me worker instances...${NC}\n"

# Stop workers
for i in $(seq 1 $NUM_WORKERS); do
    echo -e "${YELLOW}Stopping worker$i...${NC}"

    if [ "$REMOVE_VOLUMES" = true ]; then
        INSTANCE_ID="worker$i" docker compose -f docker-compose.yml -f docker-compose.parallel.yml down -v || true
        echo -e "${RED}  Removed volumes for worker$i${NC}"
    else
        INSTANCE_ID="worker$i" docker compose -f docker-compose.yml -f docker-compose.parallel.yml down || true
    fi
done

echo -e "\n${GREEN}All workers stopped!${NC}"

if [ "$REMOVE_VOLUMES" = false ]; then
    echo -e "\n${YELLOW}Note: Volumes were preserved. To remove volumes, run:${NC}"
    echo -e "  ./stop-workers.sh $NUM_WORKERS --volumes"
fi
