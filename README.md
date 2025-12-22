# Melee Decomp Agent

Agent tooling for contributing to the Super Smash Bros. Melee decompilation project.

## Overview

This project provides tooling to enable AI agents to:
- Extract unmatched functions from the melee decomp project
- Use locally hosted decomp.me instances to iteratively decompile functions
- Commit matched source code back to the original decomp project
- Create PRs with matched functions

## Installation

```bash
pip install -e .
```

## Usage

```bash
# List unmatched functions
melee-agent extract list

# Get info about a specific function
melee-agent extract get <function_name>

# Create a scratch on decomp.me
melee-agent scratch create <function_name>

# Run the matching agent
melee-agent match run [function_name]
```

## Modules

- `src/extractor` - Extract function info from melee decomp project
- `src/client` - Decomp.me API client
- `src/agent` - Main agent loop for matching functions
- `src/commit` - Commit and PR management

## Docker Setup

Start a local decomp.me instance:

```bash
cd docker
./setup.sh
```
