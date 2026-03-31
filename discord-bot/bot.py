import discord
import re
import json
import os
from pathlib import Path

STATS_FILE = Path(__file__).parent / "stats.json"

MENTION_IN_EMBED_PATTERN = re.compile(r"<@!?(\d+)>")


def load_stats() -> dict:
    if STATS_FILE.exists():
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def get_player_stats(stats: dict, player_id: str) -> dict:
    if player_id not in stats:
        stats[player_id] = {
            "name": player_id,
            "criticos": 0,
            "pifias": 0,
            "tiradas": 0,
        }
    return stats[player_id]


def register_roll(stats: dict, player_id: str, player_name: str, result: int, max_val: int) -> str | None:
    entry = get_player_stats(stats, player_id)
    entry["name"] = player_name
    entry["tiradas"] += 1

    if result == max_val:
        entry["criticos"] += 1
        save_stats(stats)
        return f"🎯 **¡CRÍTICO!** {player_name} sacó {result} en un d{max_val}!"

    if result == 1:
        entry["pifias"] += 1
        save_stats(stats)
        return f"💀 **¡PIFIA!** {player_name} sacó 1 en un d{max_val}!"

    save_stats(stats)
    return None


class DiceBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Bot conectado como {self.user} (ID: {self.user.id})")

    async def on_message(self, message: discord.Message):
        stats = load_stats()

        if message.content.startswith("!marcador"):
            await self.cmd_marcador(message, stats)
            return

        bot_name = message.author.name.lower()

        if "dice maiden" in bot_name or "dicemaiden" in bot_name:
            await self.handle_dice_maiden(message, stats)
        elif "avrae" in bot_name:
            await self.handle_avrae(message, stats)

    async def handle_dice_maiden(self, message: discord.Message, stats: dict):
        content = message.content

        player_name = None
        player_id = None

        name_match = re.search(
            r"🎲\s*(.*?)\s+Request\b",
            content,
            re.IGNORECASE
        )
        if name_match:
            player_name = name_match.group(1).strip()
            player_id = player_name.lower().replace(" ", "_")
        else:
            mention_match = MENTION_IN_EMBED_PATTERN.search(content)
            if mention_match:
                uid = mention_match.group(1)
                member = message.guild.get_member(int(uid)) if message.guild else None
                player_name = member.display_name if member else f"<@{uid}>"
                player_id = f"user_{uid}"
            else:
                return

        roll_match = re.search(
            r"(\d+)d(\d+).*?Roll:.*?\[(\d+)\]",
            content,
            re.IGNORECASE | re.DOTALL
        )
        if not roll_match:
            return

        caras = int(roll_match.group(2))
        resultado = int(roll_match.group(3))

        msg = register_roll(stats, player_id, player_name, resultado, caras)
        if msg:
            await message.channel.send(msg)

    async def handle_avrae(self, message: discord.Message, stats: dict):
        content = message.content
        full_text = content

        if message.embeds:
            for embed in message.embeds:
                if embed.description:
                    full_text += "\n" + embed.description
                for field in embed.fields:
                    full_text += "\n" + field.value

        mention_match = MENTION_IN_EMBED_PATTERN.search(full_text)
        player_name = None
        player_id = None

        if mention_match:
            uid = mention_match.group(1)
            member = message.guild.get_member(int(uid)) if message.guild else None
            player_name = member.display_name if member else f"<@{uid}>"
            player_id = f"user_{uid}"

        roll_match = re.search(
            r"(\d+)d(\d+)[^(]*\((\d+)\)",
            full_text,
            re.IGNORECASE
        )
        if not roll_match:
            return

        max_val = int(roll_match.group(2))
        result = int(roll_match.group(3))

        if not player_name:
            player_name = "Jugador desconocido"
            player_id = "unknown"

        msg = register_roll(stats, player_id, player_name, result, max_val)
        if msg:
            await message.channel.send(msg)

    async def cmd_marcador(self, message: discord.Message, stats: dict):
        if not stats:
            await message.channel.send("📊 No hay estadísticas registradas aún.")
            return

        lines = ["📊 **Marcador de dados**\n"]
        sorted_players = sorted(
            stats.values(),
            key=lambda p: p.get("criticos", 0),
            reverse=True
        )

        for player in sorted_players:
            name = player.get("name", "Desconocido")
            criticos = player.get("criticos", 0)
            pifias = player.get("pifias", 0)
            tiradas = player.get("tiradas", 0)
            lines.append(
                f"**{name}** — 🎯 Críticos: `{criticos}` | 💀 Pifias: `{pifias}` | 🎲 Tiradas: `{tiradas}`"
            )

        await message.channel.send("\n".join(lines))


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("La variable de entorno DISCORD_TOKEN no está configurada.")
    bot = DiceBot()
    bot.run(token)


if __name__ == "__main__":
    main()
