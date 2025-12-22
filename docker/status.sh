#!/bin/bash

# Helper script to show status of all decomp.me instances
# Usage: ./status.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color codes
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}=== decomp.me Instance Status ===${NC}\n"

# Find all decompme containers
containers=$(docker ps -a --filter "name=decompme_" --format "{{.Names}}" | sort)

if [ -z "$containers" ]; then
    echo -e "${YELLOW}No decomp.me instances found${NC}"
    exit 0
fi

# Group by instance
current_instance=""
for container in $containers; do
    # Extract instance ID from container name (format: decompme_SERVICE_INSTANCE)
    instance=$(echo "$container" | cut -d'_' -f3)

    if [ "$instance" != "$current_instance" ]; then
        if [ ! -z "$current_instance" ]; then
            echo ""
        fi
        current_instance="$instance"
        echo -e "${BLUE}Instance: $instance${NC}"
    fi

    # Get container status
    status=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo "unknown")
    health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "none")

    # Color based on status
    if [ "$status" = "running" ]; then
        if [ "$health" = "healthy" ]; then
            status_color="${GREEN}"
            status_text="running (healthy)"
        elif [ "$health" = "unhealthy" ]; then
            status_color="${RED}"
            status_text="running (unhealthy)"
        elif [ "$health" = "starting" ]; then
            status_color="${YELLOW}"
            status_text="running (starting)"
        else
            status_color="${GREEN}"
            status_text="running"
        fi
    else
        status_color="${RED}"
        status_text="$status"
    fi

    # Get service name
    service=$(echo "$container" | cut -d'_' -f2)

    # Get ports
    ports=$(docker port "$container" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')

    echo -e "  ${status_color}● $service${NC} - $status_text"
    if [ ! -z "$ports" ]; then
        echo -e "    Ports: $ports"
    fi
done

echo ""
echo -e "${BLUE}=== Quick Access URLs ===${NC}\n"

# Get unique instances and their nginx ports
instances=$(docker ps --filter "name=decompme_nginx_" --format "{{.Names}}" | cut -d'_' -f3 | sort)

for instance in $instances; do
    nginx_port=$(docker port "decompme_nginx_$instance" 80 2>/dev/null | cut -d':' -f2)
    if [ ! -z "$nginx_port" ]; then
        echo -e "  Instance '$instance': ${GREEN}http://localhost:$nginx_port${NC} (API: http://localhost:$nginx_port/api)"
    fi
done

echo ""
echo -e "${BLUE}=== Resource Usage ===${NC}\n"

# Show resource usage for decompme containers
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" $(docker ps --filter "name=decompme_" -q) 2>/dev/null || echo "No running containers"

echo ""
echo -e "${BLUE}=== Disk Usage ===${NC}\n"

# Show volume usage
volumes=$(docker volume ls --filter "name=decompme_" --format "{{.Name}}")
if [ ! -z "$volumes" ]; then
    for vol in $volumes; do
        size=$(docker system df -v 2>/dev/null | grep "$vol" | awk '{print $3}' || echo "unknown")
        echo -e "  $vol: $size"
    done
else
    echo "  No volumes found"
fi
