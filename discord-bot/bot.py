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


def migrate_stats(stats: dict) -> dict:
    """
    Migra entradas antiguas con clave "user_XXXXXXX" a solo "XXXXXXX".
    Fusiona entradas duplicadas si se detectan.
    """
    changed = False
    keys_to_rename = {}

    for key in list(stats.keys()):
        if key.startswith("user_"):
            new_key = key[len("user_"):]
            keys_to_rename[key] = new_key

    for old_key, new_key in keys_to_rename.items():
        entry = stats.pop(old_key)
        if new_key in stats:
            existing = stats[new_key]
            existing["criticos"] += entry.get("criticos", 0)
            existing["pifias"] += entry.get("pifias", 0)
            existing["tiradas"] += entry.get("tiradas", 0)
        else:
            stats[new_key] = entry
        changed = True

    if changed:
        save_stats(stats)
        print(f"[Migración] {len(keys_to_rename)} entrada(s) migradas.")

    return stats


def get_or_create_entry(stats: dict, uid: str, display_name: str) -> dict:
    if uid not in stats:
        stats[uid] = {
            "name": display_name,
            "criticos": 0,
            "pifias": 0,
            "tiradas": 0,
        }
    else:
        if stats[uid].get("name") != display_name:
            print(f"[Nombre actualizado] {stats[uid]['name']} → {display_name}")
            stats[uid]["name"] = display_name
    return stats[uid]


def register_roll(
    stats: dict,
    uid: str,
    display_name: str,
    resultado: int,
    caras: int,
) -> str | None:
    entry = get_or_create_entry(stats, uid, display_name)
    entry["tiradas"] += 1

    if resultado == caras:
        entry["criticos"] += 1
        save_stats(stats)
        return f"🎯 **¡CRÍTICO!** {display_name} sacó {resultado} en un d{caras}!"

    if resultado == 1:
        entry["pifias"] += 1
        save_stats(stats)
        return f"💀 **¡PIFIA!** {display_name} sacó 1 en un d{caras}!"

    save_stats(stats)
    return None


def resolve_member_by_name(guild: discord.Guild, name: str) -> discord.Member | None:
    """Busca un miembro del servidor cuyo display_name o name coincida (sin distinción de mayúsculas)."""
    name_lower = name.lower()
    for member in guild.members:
        if member.display_name.lower() == name_lower or member.name.lower() == name_lower:
            return member
    return None


class DiceBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Bot conectado como {self.user} (ID: {self.user.id})")
        stats = load_stats()
        migrate_stats(stats)

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

        uid = None
        display_name = None

        mention_match = MENTION_IN_EMBED_PATTERN.search(content)
        if mention_match:
            uid = mention_match.group(1)
            member = message.guild.get_member(int(uid)) if message.guild else None
            display_name = member.display_name if member else f"Usuario {uid}"
        else:
            name_match = re.search(r"🎲\s*(.*?)\s+Request\b", content, re.IGNORECASE)
            if name_match:
                extracted_name = name_match.group(1).strip()
                if message.guild:
                    member = resolve_member_by_name(message.guild, extracted_name)
                    if member:
                        uid = str(member.id)
                        display_name = member.display_name
                    else:
                        uid = extracted_name.lower().replace(" ", "_")
                        display_name = extracted_name
                else:
                    uid = extracted_name.lower().replace(" ", "_")
                    display_name = extracted_name
            else:
                return

        roll_match = re.search(
            r"(\d+)d(\d+).*?Roll:.*?\[(\d+)\]",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if not roll_match:
            return

        caras = int(roll_match.group(2))
        resultado = int(roll_match.group(3))

        msg = register_roll(stats, uid, display_name, resultado, caras)
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
        uid = None
        display_name = None

        if mention_match:
            uid = mention_match.group(1)
            member = message.guild.get_member(int(uid)) if message.guild else None
            display_name = member.display_name if member else f"Usuario {uid}"
        else:
            uid = "unknown"
            display_name = "Jugador desconocido"

        roll_match = re.search(
            r"(\d+)d(\d+)[^(]*\((\d+)\)",
            full_text,
            re.IGNORECASE,
        )
        if not roll_match:
            return

        caras = int(roll_match.group(2))
        resultado = int(roll_match.group(3))

        msg = register_roll(stats, uid, display_name, resultado, caras)
        if msg:
            await message.channel.send(msg)

    async def cmd_marcador(self, message: discord.Message, stats: dict):
        if not stats:
            await message.channel.send("📊 No hay estadísticas registradas aún.")
            return

        sorted_players = sorted(
            stats.items(),
            key=lambda kv: kv[1].get("criticos", 0),
            reverse=True,
        )

        lines = ["📊 **Marcador de dados**\n"]
        for uid, data in sorted_players:
            if uid.isdigit() and message.guild:
                member = message.guild.get_member(int(uid))
                if member:
                    if data.get("name") != member.display_name:
                        data["name"] = member.display_name
                        save_stats(stats)
                    name_display = f"<@{uid}>"
                else:
                    name_display = f"**{data.get('name', uid)}**"
            else:
                name_display = f"**{data.get('name', uid)}**"

            criticos = data.get("criticos", 0)
            pifias = data.get("pifias", 0)
            tiradas = data.get("tiradas", 0)
            lines.append(
                f"{name_display} — 🎯 Críticos: `{criticos}` | 💀 Pifias: `{pifias}` | 🎲 Tiradas: `{tiradas}`"
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
