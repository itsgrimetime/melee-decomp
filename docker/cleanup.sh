#!/bin/bash

# Cleanup script for decomp.me Docker setup
# Removes all containers, networks, and optionally volumes

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${YELLOW}=== decomp.me Docker Cleanup ===${NC}\n"

# Parse arguments
REMOVE_VOLUMES=false
REMOVE_IMAGES=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --volumes|-v)
            REMOVE_VOLUMES=true
            shift
            ;;
        --images|-i)
            REMOVE_IMAGES=true
            shift
            ;;
        --all|-a)
            REMOVE_VOLUMES=true
            REMOVE_IMAGES=true
            shift
            ;;
        --force|-f)
            FORCE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --volumes, -v    Remove volumes (database data will be lost)"
            echo "  --images, -i     Remove built images"
            echo "  --all, -a        Remove everything (volumes + images)"
            echo "  --force, -f      Skip confirmation prompts"
            echo "  --help, -h       Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                  # Stop and remove containers only"
            echo "  $0 --volumes        # Also remove volumes (data loss!)"
            echo "  $0 --all            # Remove everything"
            echo "  $0 --all --force    # Remove everything without confirmation"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Show what will be done
echo -e "${BLUE}This script will:${NC}"
echo "  - Stop and remove all decomp.me containers"
echo "  - Remove decomp.me networks"
if [ "$REMOVE_VOLUMES" = true ]; then
    echo -e "  ${RED}- Remove decomp.me volumes (DATABASE DATA WILL BE LOST!)${NC}"
fi
if [ "$REMOVE_IMAGES" = true ]; then
    echo -e "  ${YELLOW}- Remove decomp.me Docker images${NC}"
fi
echo ""

# Confirmation
if [ "$FORCE" = false ]; then
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted"
        exit 1
    fi
fi

# Find all decomp.me containers
containers=$(docker ps -aq --filter "name=decompme_" 2>/dev/null || true)

if [ ! -z "$containers" ]; then
    echo -e "\n${BLUE}Stopping and removing containers...${NC}"
    docker stop $containers 2>/dev/null || true
    docker rm $containers 2>/dev/null || true
    echo -e "${GREEN}✓ Containers removed${NC}"
else
    echo -e "\n${YELLOW}No containers to remove${NC}"
fi

# Remove networks
networks=$(docker network ls --filter "name=decompme_network_" -q 2>/dev/null || true)

if [ ! -z "$networks" ]; then
    echo -e "\n${BLUE}Removing networks...${NC}"
    docker network rm $networks 2>/dev/null || true
    echo -e "${GREEN}✓ Networks removed${NC}"
else
    echo -e "\n${YELLOW}No networks to remove${NC}"
fi

# Remove volumes if requested
if [ "$REMOVE_VOLUMES" = true ]; then
    volumes=$(docker volume ls --filter "name=decompme_postgres_data_" -q 2>/dev/null || true)

    if [ ! -z "$volumes" ]; then
        echo -e "\n${BLUE}Removing volumes...${NC}"
        docker volume rm $volumes 2>/dev/null || true
        echo -e "${GREEN}✓ Volumes removed${NC}"
    else
        echo -e "\n${YELLOW}No volumes to remove${NC}"
    fi
fi

# Remove images if requested
if [ "$REMOVE_IMAGES" = true ]; then
    echo -e "\n${BLUE}Removing images...${NC}"

    # Remove images built for decomp.me
    backend_images=$(docker images --filter "reference=*backend*" -q 2>/dev/null || true)
    frontend_images=$(docker images --filter "reference=*frontend*" -q 2>/dev/null || true)

    if [ ! -z "$backend_images" ]; then
        docker rmi $backend_images 2>/dev/null || true
    fi
    if [ ! -z "$frontend_images" ]; then
        docker rmi $frontend_images 2>/dev/null || true
    fi

    echo -e "${GREEN}✓ Images removed${NC}"
fi

# Clean up log files
if [ -f worker*.log ]; then
    echo -e "\n${BLUE}Removing log files...${NC}"
    rm -f worker*.log
    echo -e "${GREEN}✓ Log files removed${NC}"
fi

echo -e "\n${GREEN}Cleanup complete!${NC}"

# Show disk space recovered
echo -e "\n${BLUE}Docker disk usage:${NC}"
docker system df
