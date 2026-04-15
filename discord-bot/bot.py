import discord
import re
import json
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from keep_alive import keep_alive

# --- CONFIGURACIÓN Y CONSTANTES ---
STATS_FILE = Path(__file__).parent / "stats.json"
MENTION_IN_EMBED_PATTERN = re.compile(r"<@!?(\d+)>")
# AJUSTE 1: Ahora solo limpia asteriscos para no romper los nombres con barras bajas
MARKDOWN_BOLD = re.compile(r"\*+")

def strip_markdown(text: str) -> str:
    """Elimina asteriscos de negrita/cursiva."""
    return MARKDOWN_BOLD.sub("", text).strip()

# --- GESTIÓN DE DATOS (JSON) ---
def load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error cargando archivo: {e}")
            return {}
    return {}

def save_stats(stats: dict) -> None:
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando archivo: {e}")

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

    if resultado == caras:
        entry["criticos"] += 1; dado_sub["criticos"] += 1; save_stats(stats)
        return f"🎯 **¡CRÍTICO!** {entry['name']} sacó {resultado} en un d{caras}!"
    if resultado == 1:
        entry["pifias"] += 1; dado_sub["pifias"] += 1; save_stats(stats)
        return f"💀 **¡PIFIA!** {entry['name']} sacó 1 en un d{caras}!"
    
    save_stats(stats)
    return None

# --- UTILIDADES DE DISCORD ---
async def find_command_invoker(channel, bot_message):
    try:
        # Aumentamos a 10 para asegurar que encontramos al humano tras el spam del bot
        async for msg in channel.history(limit=10, before=bot_message):
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
            await asyncio.sleep(1.5)
        except discord.errors.HTTPException as e:
            if e.status == 429: print("Rate limit detectado.")

    async def on_message(self, message: discord.Message):
        if message.author == self.user: return
        if (datetime.now(timezone.utc) - message.created_at).total_seconds() > 30: return

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

        bot_name = message.author.name.lower()
        if "dice maiden" in bot_name or "dicemaiden" in bot_name:
            await self.handle_dice_maiden(message)
        elif "avrae" in bot_name:
            await self.handle_avrae(message)

    async def handle_dice_maiden(self, message: discord.Message):
        # AJUSTE 2: Se cambia (\d+) por (\d*) para que reconozca "d2" (cero o más números antes de la d)
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
            # AJUSTE 3: Regex más flexible para capturar el nombre antes de "Request" u otras palabras
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
        clean_text = re.sub(r"\*+", "", text)
        # También aquí ajustamos para d20 sin número delante
        roll_match = re.search(r"Result:.*?(\d*)d(\d+)\s*\((\d+)\)", clean_text, re.IGNORECASE)
        if not roll_match:
            roll_match = re.search(r"(\d*)d(\d+)[^()\n]*\((\d+)\)", clean_text, re.IGNORECASE)
        if roll_match:
            caras = int(roll_match.group(2))
            res = int(roll_match.group(3))
            uid = None
            m = MENTION_IN_EMBED_PATTERN.search(text)
            if m: uid = m.group(1)
            else:
                inv = await find_command_invoker(message.channel, message)
                if inv: uid = str(inv.id)
            if uid:
                mb = message.guild.get_member(int(uid))
                name = mb.display_name if mb else f"Usuario {uid}"
                msg = register_roll(self.stats, uid, name, res, caras)
                try:
                    if res == caras: 
                        await message.add_reaction("🎯")
                        if msg: await self.safe_send(message.channel, msg)
                    elif res == 1: 
                        await message.add_reaction("💀")
                        if msg: await self.safe_send(message.channel, msg)
                    else: await message.add_reaction("🎲")
                except: pass

    async def cmd_marcador(self, message):
        if not self.stats:
            await message.channel.send("📊 No hay estadísticas.")
            return
        sorted_players = sorted(self.stats.items(), key=lambda kv: kv[1].get("criticos", 0), reverse=True)
        lines = ["📊 **Marcador de dados**\n"]
        for uid, data in sorted_players:
            name = f"<@{uid}>" if uid.isdigit() else f"**{data.get('name', uid)}**"
            ds = data.get("dados", {})
            desglose = " | ".join([f"{k}: {v['tiradas'] if isinstance(v,dict) else v}" for k, v in sorted(ds.items(), key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 0)])
            lines.append(f"{name} — 🎯: `{data.get('criticos',0)}` | 💀: `{data.get('pifias',0)}` | 🎲 {desglose or 'n/a'} | **Total: {data.get('tiradas',0)}**")
        full_msg = "\n".join(lines)
        if len(full_msg) > 1900:
            for i in range(0, len(lines), 5): await self.safe_send(message.channel, "\n".join(lines[i:i+5]))
        else: await self.safe_send(message.channel, full_msg)

    async def cmd_estadisticas(self, message):
        m = MENTION_IN_EMBED_PATTERN.search(message.content)
        players = [(m.group(1), self.stats[m.group(1)])] if m and m.group(1) in self.stats else sorted(self.stats.items(), key=lambda x: x[1].get("criticos",0), reverse=True)
        for uid, data in players:
            embed = discord.Embed(title=f"📊 Estadísticas de {data.get('name', uid)}", color=0x7289da)
            embed.add_field(name="Global", value=f"🎯 Críticos: **{data.get('criticos',0)}**\n💀 Pifias: **{data.get('pifias',0)}**\n🎲 Total: **{data.get('tiradas',0)}**")
            ds = data.get("dados", {})
            if ds:
                txt = "\n".join([f"**{k}**: {v['tiradas']} tiradas | 🎯 {v['criticos']} | 💀 {v['pifias']}" for k, v in sorted(ds.items(), key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 0) if (v['tiradas'] if isinstance(v,dict) else v) > 0])
                if txt: embed.add_field(name="Desglose", value=txt, inline=False)
            await self.safe_send(message.channel, content=f"<@{uid}>" if uid.isdigit() else None, embed=embed)

    async def cmd_set(self, message):
        if not message.guild or not message.author.guild_permissions.administrator: return
        parts = message.content.split()
        if len(parts) != 4: return
        _, m_raw, campo, val_raw = parts
        m_match = MENTION_IN_EMBED_PATTERN.search(m_raw)
        if not m_match: return
        uid, val = m_match.group(1), int(val_raw)
        entry = get_or_create_entry(self.stats, uid, "Usuario")
        campo = campo.lower()
        if campo in {"criticos", "pifias", "tiradas"}: entry[campo] = val
        else:
            d_match = re.match(r"^(d\d+)_(criticos|pifias|tiradas)$", campo)
            if d_match:
                dk, sub = d_match.groups()
                get_dado_sub(entry["dados"], dk)[sub] = val
                entry["tiradas"] = sum(v.get("tiradas",0) for v in entry["dados"].values())
                entry["criticos"] = sum(v.get("criticos",0) for v in entry["dados"].values())
                entry["pifias"] = sum(v.get("pifias",0) for v in entry["dados"].values())
        save_stats(self.stats)
        await message.channel.send(f"✅ Actualizado {campo} de <@{uid}> a {val}.")

    async def cmd_remove(self, message):
        if not message.guild or not message.author.guild_permissions.administrator: return
        parts = message.content.split(maxsplit=1)
        target = parts[1].strip().lower() if len(parts) > 1 else None
        removed = []
        for k in list(self.stats.keys()):
            if not k.isdigit() or (target and self.stats[k].get("name", "").lower() == target):
                removed.append(self.stats[k].get("name", k))
                del self.stats[k]
        save_stats(self.stats)
        await message.channel.send(f"🗑️ Eliminados: {', '.join(removed) if removed else 'Nada'}")

# --- ARRANQUE COMPATIBLE CON RENDER ---
async def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Error: TOKEN no encontrado.")
        return
    keep_alive()
    while True:
        client = DiceBot()
        try:
            print("Iniciando conexión con Discord...")
            await client.start(token, reconnect=True)
        except Exception as e:
            print(f"Error: {e}. Reintentando en 30s...")
            await asyncio.sleep(30)
        finally:
            if not client.is_closed(): await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt: pass
