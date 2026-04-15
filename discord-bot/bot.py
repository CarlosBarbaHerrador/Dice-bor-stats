import discord
import re
import json
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from keep_alive import keep_alive

# --- CONFIGURACIÓN Y CONSTANTES ---
# Usamos una ruta absoluta para asegurar que el archivo se guarde siempre en el mismo sitio
BASE_DIR = Path(__file__).parent
STATS_FILE = BASE_DIR / "stats.json"
MENTION_IN_EMBED_PATTERN = re.compile(r"<@!?(\d+)>")
MARKDOWN_BOLD = re.compile(r"\*+")

def strip_markdown(text: str) -> str:
    """Elimina asteriscos de negrita/cursiva."""
    if not text: return ""
    return MARKDOWN_BOLD.sub("", text).strip()

# --- GESTIÓN DE DATOS (JSON) ---
def load_stats() -> dict:
    """Carga los datos del JSON de forma segura."""
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                if not content: return {}
                return json.loads(content)
        except Exception as e:
            print(f"Error cargando archivo: {e}")
            return {}
    return {}

def save_stats(stats: dict) -> None:
    """Guarda los datos asegurando persistencia."""
    try:
        # Guardar en un archivo temporal primero para evitar corrupción si el bot se apaga a mitad
        temp_file = STATS_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        # Reemplazar el archivo original con el temporal
        temp_file.replace(STATS_FILE)
    except Exception as e:
        print(f"Error crítico guardando archivo: {e}")

# --- LÓGICA DE ESTADÍSTICAS ---
def get_dado_sub(dados: dict, dado_key: str) -> dict:
    val = dados.get(dado_key)
    if not isinstance(val, dict):
        dados[dado_key] = {"tiradas": val if isinstance(val, int) else 0, "criticos": 0, "pifias": 0}
    return dados[dado_key]

def merge_entry(target: dict, source: dict) -> None:
    target["criticos"] = target.get("criticos", 0) + source.get("criticos", 0)
    target["pifias"] = target.get("pifias", 0) + source.get("pifias", 0)
    target["tiradas"] = target.get("tiradas", 0) + source.get("tiradas", 0)
    target_dados = target.setdefault("dados", {})
    for dado_key, dado_val in source.get("dados", {}).items():
        t = get_dado_sub(target_dados, dado_key)
        if isinstance(dado_val, int):
            t["tiradas"] += dado_val
        else:
            t["tiradas"] += dado_val.get("tiradas", 0)
            t["criticos"] += dado_val.get("criticos", 0)
            t["pifias"] += dado_val.get("pifias", 0)

def migrate_stats(stats: dict) -> dict:
    changed = False
    new_stats: dict = {}
    for key, data in stats.items():
        clean_key = strip_markdown(key)
        if clean_key.startswith("user_"):
            clean_key = clean_key[len("user_"):]
        clean_name = strip_markdown(data.get("name", clean_key))
        data["name"] = clean_name
        if clean_key in new_stats:
            merge_entry(new_stats[clean_key], data)
            changed = True
        else:
            new_stats[clean_key] = data
            if clean_key != key: changed = True

    for data in new_stats.values():
        if "dados" not in data:
            data["dados"] = {}; changed = True
        for dk, dv in list(data["dados"].items()):
            if isinstance(dv, int):
                data["dados"][dk] = {"tiradas": dv, "criticos": 0, "pifias": 0}
                changed = True
    if changed:
        stats.clear(); stats.update(new_stats); save_stats(stats)
    return stats

def get_or_create_entry(stats: dict, uid: str, display_name: str) -> dict:
    clean_name = strip_markdown(display_name)
    if uid not in stats:
        stats[uid] = {"name": clean_name, "criticos": 0, "pifias": 0, "tiradas": 0, "dados": {}}
    else:
        entry = stats[uid]
        entry["name"] = clean_name
        entry.setdefault("dados", {})
    return stats[uid]

def register_roll(stats: dict, uid: str, display_name: str, resultado: int, caras: int) -> str | None:
    entry = get_or_create_entry(stats, uid, display_name)
    entry["tiradas"] += 1
    dado_key = f"d{caras}"
    dado_sub = get_dado_sub(entry["dados"], dado_key)
    dado_sub["tiradas"] += 1

    msg = None
    if resultado == caras:
        entry["criticos"] += 1; dado_sub["criticos"] += 1
        msg = f"🎯 **¡CRÍTICO!** {entry['name']} sacó {resultado} en un d{caras}!"
    elif resultado == 1:
        entry["pifias"] += 1; dado_sub["pifias"] += 1
        msg = f"💀 **¡PIFIA!** {entry['name']} sacó 1 en un d{caras}!"
    
    save_stats(stats)
    return msg

# --- UTILIDADES DE DISCORD ---
async def find_command_invoker(channel, bot_message):
    try:
        async for msg in channel.history(limit=5, before=bot_message):
            if not msg.author.bot: return msg.author
    except: pass
    return None

def resolve_member_by_name(guild, name):
    clean = strip_markdown(name).lower()
    for m in guild.members:
        if m.display_name.lower() == clean or m.name.lower() == clean: return m
    return None

# --- CLASE PRINCIPAL DEL BOT ---
class DiceBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.stats = {}

    async def on_ready(self):
        print(f"Conectado como {self.user}")
        self.stats = load_stats()
        migrate_stats(self.stats)

    async def safe_send(self, channel, content=None, embed=None):
        try:
            await channel.send(content=content, embed=embed)
        except Exception as e:
            print(f"Error enviando mensaje: {e}")

    async def on_message(self, message: discord.Message):
        if message.author == self.user: return
        
        # Comandos
        if message.content.startswith("!marcador"):
            await self.cmd_marcador(message)
            return
        if message.content.startswith("!estadisticas"):
            await self.cmd_estadisticas(message)
            return
        if message.content.startswith("!set"):
            await self.cmd_set(message)
            return
        if message.content.startswith("!remove"):
            await self.cmd_remove(message)
            return

        # Detección de bots de dados
        bot_name = message.author.name.lower()
        if "dice maiden" in bot_name or "dicemaiden" in bot_name or "dado" in bot_name:
            await self.handle_dice_maiden(message)
        elif "avrae" in bot_name:
            await self.handle_avrae(message)

    async def handle_dice_maiden(self, message: discord.Message):
        # Reconoce "d2" y "1d2"
        roll_match = re.search(r"(\d*)d(\d+).*?Roll:.*?\[(\d+)\]", message.content, re.IGNORECASE | re.DOTALL)
        if not roll_match: return
        
        caras, resultado = int(roll_match.group(2)), int(roll_match.group(3))
        uid, name = None, "Desconocido"
        
        m = MENTION_IN_EMBED_PATTERN.search(message.content)
        if m: 
            uid = m.group(1)
            member = message.guild.get_member(int(uid))
            name = member.display_name if member else f"Usuario {uid}"
        else:
            nm = re.search(r"🎲\s*(.*?)\s+(?:Request|ha utilizado|roll)\b", message.content, re.IGNORECASE)
            if nm:
                ext = strip_markdown(nm.group(1).strip())
                mb = resolve_member_by_name(message.guild, ext)
                uid, name = (str(mb.id), mb.display_name) if mb else (ext, ext)
        
        if not uid:
            invoker = await find_command_invoker(message.channel, message)
            if invoker: uid, name = str(invoker.id), invoker.display_name
        
        if uid:
            msg = register_roll(self.stats, uid, name, resultado, caras)
            if msg: await self.safe_send(message.channel, msg)

    async def handle_avrae(self, message: discord.Message):
        text = message.content
        if message.embeds:
            for e in message.embeds:
                text += f" {e.title or ''} {e.description or ''} "
                for f in e.fields: text += f" {f.name} {f.value}"
        
        clean_text = strip_markdown(text)
        roll_match = re.search(r"(\d*)d(\d+).*?\((\d+)\)", clean_text, re.IGNORECASE)
        
        if roll_match:
            caras, res = int(roll_match.group(2) or 1), int(roll_match.group(3))
            inv = await find_command_invoker(message.channel, message)
            if inv:
                msg = register_roll(self.stats, str(inv.id), inv.display_name, res, caras)
                if msg: await self.safe_send(message.channel, msg)

    async def cmd_marcador(self, message):
        if not self.stats:
            return await message.channel.send("📊 No hay estadísticas guardadas aún.")
        
        sorted_players = sorted(self.stats.items(), key=lambda kv: kv[1].get("criticos", 0), reverse=True)
        lines = ["📊 **Marcador de dados**\n"]
        for uid, data in sorted_players:
            mention = f"<@{uid}>" if uid.isdigit() else f"**{data.get('name', uid)}**"
            lines.append(f"{mention} — 🎯: `{data.get('criticos',0)}` | 💀: `{data.get('pifias',0)}` | **Total: {data.get('tiradas',0)}**")
        
        await message.channel.send("\n".join(lines))

    async def cmd_estadisticas(self, message):
        m = MENTION_IN_EMBED_PATTERN.search(message.content)
        uid = m.group(1) if m else str(message.author.id)
        if uid not in self.stats:
            return await message.channel.send("No tengo datos de ese usuario.")
        
        data = self.stats[uid]
        embed = discord.Embed(title=f"Estadísticas de {data.get('name', uid)}", color=0x7289da)
        embed.add_field(name="Resumen", value=f"🎯 Críticos: {data.get('criticos')}\n💀 Pifias: {data.get('pifias')}\nTotal: {data.get('tiradas')}")
        await message.channel.send(embed=embed)

    async def cmd_set(self, message):
        if not message.author.guild_permissions.administrator: return
        try:
            p = message.content.split()
            m = MENTION_IN_EMBED_PATTERN.search(p[1])
            uid, campo, val = m.group(1), p[2].lower(), int(p[3])
            entry = get_or_create_entry(self.stats, uid, "Usuario")
            
            if "_" in campo:
                dk, sub = campo.split("_")
                get_dado_sub(entry["dados"], dk)[sub] = val
                # Recalcular totales
                entry["criticos"] = sum(v.get("criticos", 0) for v in entry["dados"].values() if isinstance(v, dict))
                entry["pifias"] = sum(v.get("pifias", 0) for v in entry["dados"].values() if isinstance(v, dict))
                entry["tiradas"] = sum(v.get("tiradas", 0) for v in entry["dados"].values() if isinstance(v, dict))
            else:
                entry[campo] = val
            
            save_stats(self.stats)
            await message.channel.send(f"✅ {campo} actualizado.")
        except:
            await message.channel.send("❌ Uso: `!set @user campo valor` (Ej: `d20_criticos 10`)")

    async def cmd_remove(self, message):
        if not message.author.guild_permissions.administrator: return
        parts = message.content.split(maxsplit=1)
        if len(parts) < 2: return
        target = parts[1].strip()
        
        m = MENTION_IN_EMBED_PATTERN.search(target)
        uid_to_del = m.group(1) if m else None
        
        if uid_to_del in self.stats:
            del self.stats[uid_to_del]
            save_stats(self.stats)
            await message.channel.send(f"🗑️ Usuario eliminado.")

# --- ARRANQUE ---
async def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token: return print("Falta el TOKEN.")
    keep_alive()
    while True:
        client = DiceBot()
        try:
            await client.start(token)
        except Exception as e:
            print(f"Reconectando... {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
