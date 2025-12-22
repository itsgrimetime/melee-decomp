# decomp.me Docker Quick Start

## First Time Setup

```bash
cd /Users/mike/code/melee-decomp/docker

# Start default instance
./setup.sh
```

Access at: **http://localhost**

## Common Commands

### Single Instance

```bash
# Start
./setup.sh

# View logs
docker compose logs -f

# Stop
docker compose down

# Restart
docker compose restart

# Clean everything (including data)
./cleanup.sh --all
```

### Multiple Parallel Instances

```bash
# Start 3 workers
./start-workers.sh 3

# Check status
./status.sh

# Stop all workers
./stop-workers.sh 3

# Stop and remove data
./stop-workers.sh 3 --volumes
```

### Worker URLs

When running parallel instances:

- Worker 1: http://localhost:8001/api
- Worker 2: http://localhost:8002/api
- Worker 3: http://localhost:8003/api

## Manual Parallel Instance

```bash
INSTANCE_ID=custom \
NGINX_PORT=8005 \
BACKEND_PORT=9005 \
FRONTEND_PORT=9105 \
POSTGRES_PORT=5437 \
./setup.sh
```

## Troubleshooting

### Ports in use?
```bash
# Check what's using port 80
lsof -i :80

# Start on different port
NGINX_PORT=8080 ./setup.sh
```

### Services unhealthy?
```bash
# Check status
./status.sh

# View logs
docker compose logs backend
docker compose logs postgres

# Restart specific service
docker compose restart backend
```

### Clean slate
```bash
# Remove everything and start fresh
./cleanup.sh --all --force
./setup.sh
```

## Database Access

```bash
# Connect to postgres
docker exec -it decompme_postgres_default psql -U decompme -d decompme

# Backup database
docker exec decompme_postgres_default pg_dump -U decompme decompme > backup.sql

# Restore database
cat backup.sql | docker exec -i decompme_postgres_default psql -U decompme -d decompme
```

## API Testing

```bash
# List compilers
curl http://localhost/api/compilers

# Check health
curl http://localhost/api/
```

## Development

### Hot Reload
- Backend: Edit files in `../decomp.me/backend/` - auto-reloads
- Frontend: Edit files in `../decomp.me/frontend/` - HMR enabled

### Debugging
- Python debugger on port 5678
- Configure your IDE to connect remotely

### Access Containers
```bash
# Backend shell
docker exec -it decompme_backend_default bash

# View Django logs
docker compose logs -f backend

# Run Django management commands
docker exec -it decompme_backend_default python manage.py <command>
```

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `setup.sh` | Start a single instance |
| `start-workers.sh` | Start multiple parallel instances |
| `stop-workers.sh` | Stop parallel instances |
| `status.sh` | Show status of all instances |
| `cleanup.sh` | Remove containers/volumes/images |

## Configuration

Edit `decomp.me.env` to configure:
- Database credentials
- Platform support (GameCube/Wii enabled by default)
- Debug mode
- Secret keys

## Port Mapping

Default ports:
- **80**: Nginx (main access point)
- **8000**: Backend API (direct)
- **8080**: Frontend dev server (direct)
- **5432**: PostgreSQL
- **5678**: Python debugger

## Resources

- Full documentation: `README.md`
- decomp.me docs: https://decomp.me
