# Dice-bor-stats

Discord bot that tracks dice roll statistics with PostgreSQL database, REST API, and monorepo architecture using Python and TypeScript.

## Overview

This bot monitors dice rolls from **Dice Maiden** and **Avrae** in Discord, automatically recording critical hits, fumbles, and totals per player. It provides leaderboards and detailed statistics.

## Features

- **Automatic Detection** — Listens to dice roll messages from Dice Maiden and Avrae bots
- **Player Rankings** — `!marcador` displays leaderboards by critical hits
- **Detailed Stats** — `!estadisticas [@user]` shows per-player or global statistics
- **Admin Controls** — `!set` and `!remove` commands for manual adjustments

## Commands

| Command | Description |
|---------|-------------|
| `!marcador` | Player ranking by critical hits |
| `!estadisticas [@user]` | Detailed statistics per player or all |
| `!set @user <field> <value>` | Admin: manually adjust stats |
| `!remove [name]` | Admin: clear player data |

## Architecture

```
Discord (events) → Python Bot → PostgreSQL (Drizzle ORM) ← Express API (TypeScript)
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Bot | Python 3.11, discord.py 2.7.1 |
| API Server | TypeScript, Express 5 |
| Database | PostgreSQL, Drizzle ORM |
| Frontend (sandbox) | React, Vite, Tailwind CSS |
| API Spec | OpenAPI 3.1 → Orval → Zod + React Query |
| Monorepo | pnpm workspaces |

## Project Structure

```
Dice-bor-stats/
├── discord-bot/        # Python Discord bot
├── artifacts/
│   └── api-server/     # Express REST API
├── lib/
│   ├── db/             # Drizzle ORM schema + connection
│   ├── api-spec/       # OpenAPI specification
│   ├── api-zod/        # Generated Zod schemas
│   └── api-client-react/ # Generated React Query hooks
└── scripts/            # Utility scripts
```

## Development

```bash
# Install dependencies
pnpm install

# Run the Discord bot
python main.py

# Run the API server
pnpm --filter @workspace/api-server run dev
```

## Secrets

| Secret | Description |
|--------|-------------|
| `DISCORD_TOKEN` | Discord bot token |
| `DATABASE_URL` | PostgreSQL connection string |
