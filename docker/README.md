# decomp.me Local Docker Setup

This directory contains Docker configuration for running local decomp.me instances for the melee-decomp-agent project.

## Quick Start

```bash
# Make setup script executable
chmod +x setup.sh

# Start the default instance
./setup.sh
```

Access decomp.me at: http://localhost

## Architecture

The setup consists of four main services:

- **nginx** (port 80): Reverse proxy serving the frontend and routing API requests
- **backend** (port 8000): Django REST API for decomp.me
- **frontend** (port 8080): Next.js development server
- **postgres** (port 5432): PostgreSQL database

All services include health checks and proper dependency management.

## Configuration

### Environment Variables

Edit `decomp.me.env` to customize the deployment:

```bash
# Database
DATABASE_URL=psql://decompme:decompme@postgres:5432/decompme
POSTGRES_USER=decompme
POSTGRES_PASSWORD=decompme

# Django
SECRET_KEY="your-secret-key"
DEBUG="on"
ALLOWED_HOSTS="backend,localhost,127.0.0.1"

# Platform support (enable what you need)
ENABLE_GC_WII_SUPPORT=YES    # For GameCube/Wii (Melee)
ENABLE_N64_SUPPORT=NO
ENABLE_PS1_SUPPORT=NO
# ... etc
```

### Port Configuration

The default ports can be overridden with environment variables:

```bash
NGINX_PORT=80          # Main HTTP port
BACKEND_PORT=8000      # Direct backend API access
FRONTEND_PORT=8080     # Direct frontend access
POSTGRES_PORT=5432     # Database port
DEBUGGER_PORT=5678     # Python debugger port
```

## Running Multiple Instances (Parallel Workers)

To run multiple decomp.me instances for parallelization, use unique instance IDs and port ranges:

### Instance 1 (Default)
```bash
./setup.sh
# Uses ports: 80, 8000, 8080, 5432, 5678
```

### Instance 2 (Worker 1)
```bash
INSTANCE_ID=worker1 \
NGINX_PORT=8001 \
BACKEND_PORT=9001 \
FRONTEND_PORT=9101 \
POSTGRES_PORT=5433 \
DEBUGGER_PORT=5679 \
./setup.sh
```

### Instance 3 (Worker 2)
```bash
INSTANCE_ID=worker2 \
NGINX_PORT=8002 \
BACKEND_PORT=9002 \
FRONTEND_PORT=9102 \
POSTGRES_PORT=5434 \
DEBUGGER_PORT=5680 \
./setup.sh
```

Each instance will have:
- Unique container names: `decompme_backend_worker1`, `decompme_postgres_worker1`, etc.
- Isolated networks: `decompme_network_worker1`
- Separate volumes: `decompme_postgres_data_worker1`
- Independent port bindings

### Helper Script for Parallel Instances

Create a file `start-workers.sh`:

```bash
#!/bin/bash

# Start 3 parallel decomp.me instances
for i in {1..3}; do
    INSTANCE_ID="worker$i" \
    NGINX_PORT=$((8000 + i)) \
    BACKEND_PORT=$((9000 + i)) \
    FRONTEND_PORT=$((9100 + i)) \
    POSTGRES_PORT=$((5432 + i)) \
    DEBUGGER_PORT=$((5678 + i)) \
    ./setup.sh &
done

wait
echo "All workers started"
```

## Management Commands

### View Logs
```bash
cd /Users/mike/code/melee-decomp/docker
docker compose logs -f

# View specific service
docker compose logs -f backend

# For parallel instance
INSTANCE_ID=worker1 docker compose -f docker-compose.yml -f docker-compose.parallel.yml logs -f
```

### Stop Services
```bash
# Stop default instance
docker compose down

# Stop and remove volumes (clean slate)
docker compose down -v

# Stop specific parallel instance
INSTANCE_ID=worker1 docker compose -f docker-compose.yml -f docker-compose.parallel.yml down
```

### Restart Services
```bash
docker compose restart

# Restart specific service
docker compose restart backend
```

### Rebuild Images
```bash
# Rebuild all images
docker compose build

# Rebuild specific service
docker compose build backend

# Rebuild without cache
docker compose build --no-cache
```

### Access Container Shell
```bash
# Backend container
docker exec -it decompme_backend_default bash

# Database
docker exec -it decompme_postgres_default psql -U decompme -d decompme

# For parallel instance
docker exec -it decompme_backend_worker1 bash
```

## Health Checks

All services include health checks. Monitor health status:

```bash
docker compose ps

# Detailed health info
docker inspect decompme_backend_default | jq '.[0].State.Health'
```

Health check endpoints:
- **postgres**: `pg_isready -U decompme`
- **backend**: `http://localhost:8000/api/`
- **frontend**: `http://localhost:8080/`
- **nginx**: `http://localhost:80/`

## API Usage

### Direct API Access

The backend API is accessible at:
- Through nginx: http://localhost/api
- Direct: http://localhost:8000/api

### Example API Calls

```bash
# List compilers
curl http://localhost/api/compilers

# Create a scratch (requires authentication)
curl -X POST http://localhost/api/scratch \
  -H "Content-Type: application/json" \
  -d '{"compiler": "gcc", "platform": "gc_wii", "code": "int main() { return 0; }"}'
```

## Development

### Hot Reload

Both frontend and backend support hot reload in development mode:

- **Backend**: Django auto-reloads on Python file changes
- **Frontend**: Next.js HMR (Hot Module Replacement) via webpack

Changes to files in `/Users/mike/code/melee-decomp/decomp.me/backend` and `/Users/mike/code/melee-decomp/decomp.me/frontend` will be reflected immediately.

### Debugging

Python debugger is exposed on port 5678 (or custom `DEBUGGER_PORT`). Configure your IDE to connect to this port for remote debugging.

VSCode launch.json example:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Remote Attach",
      "type": "python",
      "request": "attach",
      "connect": {
        "host": "localhost",
        "port": 5678
      },
      "pathMappings": [
        {
          "localRoot": "${workspaceFolder}/decomp.me/backend",
          "remoteRoot": "/backend"
        }
      ]
    }
  ]
}
```

### Database Access

Access PostgreSQL:
```bash
# Using docker exec
docker exec -it decompme_postgres_default psql -U decompme -d decompme

# Using local psql client
psql -h localhost -p 5432 -U decompme -d decompme
# Password: decompme
```

### Database Backup/Restore

```bash
# Backup
docker exec decompme_postgres_default pg_dump -U decompme decompme > backup.sql

# Restore
cat backup.sql | docker exec -i decompme_postgres_default psql -U decompme -d decompme
```

## Troubleshooting

### Services Won't Start

1. Check if ports are already in use:
   ```bash
   lsof -i :80
   lsof -i :8000
   lsof -i :8080
   lsof -i :5432
   ```

2. Check Docker logs:
   ```bash
   docker compose logs backend
   docker compose logs postgres
   ```

3. Verify submodule is initialized:
   ```bash
   git submodule status
   git submodule update --init --recursive
   ```

### Health Checks Failing

If health checks fail after startup:

```bash
# Check backend health manually
curl http://localhost:8000/api/

# Check frontend
curl http://localhost:8080/

# View detailed health status
docker inspect decompme_backend_default | jq '.[0].State.Health'
```

### Database Connection Issues

```bash
# Verify postgres is running and healthy
docker compose ps postgres

# Check postgres logs
docker compose logs postgres

# Test connection
docker exec decompme_backend_default python manage.py dbshell
```

### Build Failures

If image builds fail:

```bash
# Clean build with no cache
docker compose build --no-cache

# Remove old images
docker image prune -a

# Check disk space
docker system df
```

### Permission Issues

The backend runs as user `ubuntu` (uid 1000). If you encounter permission issues:

```bash
# Fix ownership of backend files
sudo chown -R 1000:1000 ../decomp.me/backend

# Or run as root (not recommended for production)
# Modify docker-compose.yml to remove USER ubuntu
```

### Performance Issues

For better performance:

1. **Allocate more resources to Docker** (Docker Desktop settings)
   - RAM: At least 4GB
   - CPUs: 2 or more cores

2. **Use Docker volumes instead of bind mounts** for better I/O performance
   - Note: This reduces hot-reload capabilities

3. **Disable unnecessary platform support** in `decomp.me.env`
   ```bash
   ENABLE_N64_SUPPORT=NO
   ENABLE_PS1_SUPPORT=NO
   # etc.
   ```

### Network Issues

If containers can't communicate:

```bash
# Check network exists
docker network ls | grep decompme

# Inspect network
docker network inspect decompme_network_default

# Recreate network
docker compose down
docker network prune
docker compose up -d
```

## Integration with melee-decomp-agent

The melee-decomp-agent can use these local instances:

```python
# Point to local instance
decomp_me_url = "http://localhost/api"

# For parallel workers
worker_urls = [
    "http://localhost:8001/api",  # worker1
    "http://localhost:8002/api",  # worker2
    "http://localhost:8003/api",  # worker3
]
```

## Production Deployment

This setup is for **development only**. For production:

1. Use `docker-compose.prod.yaml` from decomp.me
2. Set proper `SECRET_KEY`
3. Disable `DEBUG` mode
4. Use proper SSL certificates
5. Configure proper database settings
6. Set up proper backups
7. Use environment-specific secrets management

## Files

- **docker-compose.yml**: Main compose configuration
- **docker-compose.parallel.yml**: Override for parallel instances
- **decomp.me.env**: Environment variables
- **setup.sh**: Setup and startup script
- **README.md**: This file

## Resources

- decomp.me: https://decomp.me
- decomp.me GitHub: https://github.com/decompme/decomp.me
- Docker Compose: https://docs.docker.com/compose/
- melee decomp: https://github.com/doldecomp/melee

## License

This configuration inherits the license from the decomp.me project.
