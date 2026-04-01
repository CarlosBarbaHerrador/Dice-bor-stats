import discord
import re
import json
import os
from pathlib import Path
from keep_alive import keep_alive

STATS_FILE = Path(__file__).parent / "stats.json"

MENTION_IN_EMBED_PATTERN = re.compile(r"<@!?(\d+)>")
MARKDOWN_BOLD = re.compile(r"\*{1,2}")


def strip_markdown(text: str) -> str:
    """Elimina asteriscos de negrita/cursiva de un texto."""
    return MARKDOWN_BOLD.sub("", text).strip()


def load_stats() -> dict:
    if STATS_FILE.exists():
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def get_dado_sub(dados: dict, dado_key: str) -> dict:
    """Devuelve (y crea si falta) el subdict {tiradas, criticos, pifias} de un dado."""
    val = dados.get(dado_key)
    if not isinstance(val, dict):
        dados[dado_key] = {"tiradas": val if isinstance(val, int) else 0, "criticos": 0, "pifias": 0}
    return dados[dado_key]


def merge_entry(target: dict, source: dict) -> None:
    """Suma las estadísticas de source en target, incluyendo el desglose de dados."""
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
    """
    - Elimina asteriscos de claves y nombres.
    - Migra claves "user_XXXXXXX" → "XXXXXXX".
    - Fusiona entradas duplicadas detectadas por nombre normalizado.
    """
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
            print(f"[Migración] Fusionadas entradas duplicadas: '{key}' → '{clean_key}'")
        else:
            new_stats[clean_key] = data
            if clean_key != key:
                changed = True
                print(f"[Migración] Clave renombrada: '{key}' → '{clean_key}'")

    # Asegurarse de que todas las entradas tengan el campo "dados"
    # y que cada dado sea un dict {tiradas, criticos, pifias}
    for data in new_stats.values():
        if "dados" not in data:
            data["dados"] = {}
            changed = True
        for dado_key, dado_val in list(data["dados"].items()):
            if isinstance(dado_val, int):
                data["dados"][dado_key] = {"tiradas": dado_val, "criticos": 0, "pifias": 0}
                changed = True

    if changed:
        stats.clear()
        stats.update(new_stats)
        save_stats(stats)
        print(f"[Migración] Stats guardadas. Total de jugadores: {len(stats)}")

    return stats


def get_or_create_entry(stats: dict, uid: str, display_name: str) -> dict:
    clean_name = strip_markdown(display_name)
    if uid not in stats:
        stats[uid] = {
            "name": clean_name,
            "criticos": 0,
            "pifias": 0,
            "tiradas": 0,
            "dados": {},
        }
    else:
        entry = stats[uid]
        if entry.get("name") != clean_name:
            print(f"[Nombre actualizado] {entry['name']} → {clean_name}")
            entry["name"] = clean_name
        if "dados" not in entry:
            entry["dados"] = {}
    return stats[uid]


def register_roll(
    stats: dict,
    uid: str,
    display_name: str,
    resultado: int,
    caras: int,
) -> str | None:
    clean_name = strip_markdown(display_name)
    entry = get_or_create_entry(stats, uid, clean_name)
    entry["tiradas"] += 1

    dado_key = f"d{caras}"
    dado_sub = get_dado_sub(entry["dados"], dado_key)
    dado_sub["tiradas"] += 1

    if resultado == caras:
        entry["criticos"] += 1
        dado_sub["criticos"] += 1
        save_stats(stats)
        return f"🎯 **¡CRÍTICO!** {clean_name} sacó {resultado} en un d{caras}!"

    if resultado == 1:
        entry["pifias"] += 1
        dado_sub["pifias"] += 1
        save_stats(stats)
        return f"💀 **¡PIFIA!** {clean_name} sacó 1 en un d{caras}!"

    save_stats(stats)
    return None


def resolve_member_by_name(guild: discord.Guild, name: str) -> discord.Member | None:
    """Busca miembro por display_name o name (sin distinción de mayúsculas)."""
    clean = strip_markdown(name).lower()
    for member in guild.members:
        if member.display_name.lower() == clean or member.name.lower() == clean:
            return member
    return None


async def find_command_invoker(
    channel: discord.abc.Messageable,
    bot_message: discord.Message,
) -> discord.Member | None:
    """
    Busca en el historial reciente el último mensaje de un humano
    antes del mensaje del bot de dados, que sea el comando de tirada.
    """
    try:
        async for msg in channel.history(limit=10, before=bot_message):
            if not msg.author.bot:
                return msg.author
    except (discord.Forbidden, discord.HTTPException):
        pass
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

        if message.content.startswith("!estadisticas"):
            await self.cmd_estadisticas(message, stats)
            return

        if message.content.startswith("!set"):
            await self.cmd_set(message, stats)
            return

        if message.content.startswith("!remove"):
            await self.cmd_remove(message, stats)
            return

        bot_name = message.author.name.lower()

        if "dice maiden" in bot_name or "dicemaiden" in bot_name:
            await self.handle_dice_maiden(message, stats)
        elif "avrae" in bot_name:
            await self.handle_avrae(message, stats)

    async def resolve_player(
        self,
        content: str,
        message: discord.Message,
    ) -> tuple[str, str] | None:
        """
        Intenta obtener (uid, display_name) de un mensaje:
        1. Mención directa en el contenido.
        2. Nombre entre 🎲 y Request → busca miembro en el servidor.
        3. Historial del canal → primer humano antes del mensaje.
        Devuelve None si no puede identificar al jugador.
        """
        # 1. Mención directa
        mention_match = MENTION_IN_EMBED_PATTERN.search(content)
        if mention_match:
            uid = mention_match.group(1)
            member = message.guild.get_member(int(uid)) if message.guild else None
            display_name = member.display_name if member else f"Usuario {uid}"
            return uid, display_name

        # 2. Nombre entre 🎲 y Request
        name_match = re.search(r"🎲\s*(.*?)\s+Request\b", content, re.IGNORECASE)
        if name_match:
            extracted = strip_markdown(name_match.group(1).strip())
            if message.guild:
                member = resolve_member_by_name(message.guild, extracted)
                if member:
                    return str(member.id), member.display_name
            uid = extracted.lower().replace(" ", "_")
            return uid, extracted

        # 3. Historial: quien ejecutó el comando
        invoker = await find_command_invoker(message.channel, message)
        if invoker:
            return str(invoker.id), invoker.display_name

        return None

    async def handle_dice_maiden(self, message: discord.Message, stats: dict):
        content = message.content

        roll_match = re.search(
            r"(\d+)d(\d+).*?Roll:.*?\[(\d+)\]",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if not roll_match:
            return

        caras = int(roll_match.group(2))
        resultado = int(roll_match.group(3))

        player = await self.resolve_player(content, message)
        if not player:
            return

        uid, display_name = player
        msg = register_roll(stats, uid, display_name, resultado, caras)
        if msg:
            await message.channel.send(msg)

    async def handle_avrae(self, message: discord.Message, stats: dict):
        content = message.content

        # Recopilar texto del mensaje y sus embeds (por si usa ambos)
        embed_parts: list[str] = []
        if message.embeds:
            for embed in message.embeds:
                if embed.author and embed.author.name:
                    embed_parts.append(embed.author.name)
                if embed.title:
                    embed_parts.append(embed.title)
                if embed.description:
                    embed_parts.append(embed.description)
                for field in embed.fields:
                    if field.name:
                        embed_parts.append(field.name)
                    if field.value:
                        embed_parts.append(field.value)

        full_text = content + ("\n" + "\n".join(embed_parts) if embed_parts else "")

        # Avrae usa negritas (**) alrededor de los números. Las eliminamos antes
        # de aplicar el regex para que "(**1**)" se convierta en "(1)".
        clean_text = re.sub(r"\*+", "", full_text)

        # Patrón principal (indicado por el usuario):
        # "Result: 1d20 (20)" → grupo 1 = caras, grupo 2 = resultado
        roll_match = re.search(
            r"Result:.*?\d+d(\d+)\s*\((\d+)\)",
            clean_text,
            re.IGNORECASE,
        )
        if roll_match:
            caras = int(roll_match.group(1))
            resultado = int(roll_match.group(2))
        else:
            # Fallback general: "1d20 (20)"
            roll_match = re.search(
                r"(\d+)d(\d+)[^()\n]*\((\d+)\)",
                clean_text,
                re.IGNORECASE,
            )
            if roll_match:
                caras = int(roll_match.group(2))
                resultado = int(roll_match.group(3))
            else:
                return

        # Identificar al jugador por la mención en message.content
        # Avrae formato: "<@ID>  :game_die:\n**Result**: ..."
        uid: str | None = None
        mention_match = MENTION_IN_EMBED_PATTERN.search(content)
        if mention_match:
            uid = mention_match.group(1)

        if not uid and message.embeds:
            for embed in message.embeds:
                if embed.title:
                    m = MENTION_IN_EMBED_PATTERN.search(embed.title)
                    if m:
                        uid = m.group(1)
                        break

        if uid:
            member = message.guild.get_member(int(uid)) if message.guild else None
            display_name = member.display_name if member else f"Usuario {uid}"
        else:
            invoker = await find_command_invoker(message.channel, message)
            if invoker:
                uid = str(invoker.id)
                display_name = invoker.display_name
            else:
                uid = "unknown"
                display_name = "Jugador desconocido"

        # Registrar y reaccionar directamente al mensaje de Avrae
        register_roll(stats, uid, display_name, resultado, caras)

        if resultado == caras:
            await message.add_reaction("🎯")
            await message.channel.send(
                f"🎯 **¡CRÍTICO!** {display_name} sacó {resultado} en un d{caras}!"
            )
        elif resultado == 1:
            await message.add_reaction("💀")
            await message.channel.send(
                f"💀 **¡PIFIA!** {display_name} sacó 1 en un d{caras}!"
            )
        else:
            await message.add_reaction("🎲")

    async def cmd_marcador(self, message: discord.Message, stats: dict):
        if not stats:
            await message.channel.send("📊 No hay estadísticas registradas aún.")
            return

        sorted_players = sorted(
            stats.items(),
            key=lambda kv: kv[1].get("criticos", 0),
            reverse=True,
        )

        # Actualizar nombres si el miembro sigue en el servidor
        stats_changed = False
        for uid, data in sorted_players:
            if uid.isdigit() and message.guild:
                member = message.guild.get_member(int(uid))
                if member and data.get("name") != member.display_name:
                    data["name"] = member.display_name
                    stats_changed = True
        if stats_changed:
            save_stats(stats)

        lines = ["📊 **Marcador de dados**\n"]
        total_servidor = 0

        for uid, data in sorted_players:
            name_display = f"<@{uid}>" if uid.isdigit() else f"**{data.get('name', uid)}**"

            criticos = data.get("criticos", 0)
            pifias = data.get("pifias", 0)
            tiradas = data.get("tiradas", 0)
            total_servidor += tiradas

            # Desglose de dados ordenado numéricamente
            dados: dict = data.get("dados", {})

            def dado_sort_key(k: str) -> int:
                try:
                    return int(k[1:])
                except ValueError:
                    return 0

            if dados:
                desglose_parts = []
                for dado, val in sorted(dados.items(), key=lambda x: dado_sort_key(x[0])):
                    t = val["tiradas"] if isinstance(val, dict) else val
                    if t > 0:
                        desglose_parts.append(f"{dado}: {t}")
                desglose = " | ".join(desglose_parts) if desglose_parts else "sin datos"
            else:
                desglose = "sin datos"

            lines.append(
                f"{name_display} — 🎯: `{criticos}` | 💀: `{pifias}` | 🎲 {desglose} | **Total: {tiradas}**"
            )

        lines.append(f"{'─' * 35}\n🎲 Total de dados lanzados en el servidor: **{total_servidor}**")
        await message.channel.send("\n".join(lines))


    async def cmd_estadisticas(self, message: discord.Message, stats: dict):
        if not stats:
            await message.channel.send("📊 No hay estadísticas registradas aún.")
            return

        # Determinar si se filtra por un usuario concreto
        mention_match = MENTION_IN_EMBED_PATTERN.search(message.content)
        if mention_match:
            uid_filter = mention_match.group(1)
            players = [(uid_filter, stats[uid_filter])] if uid_filter in stats else []
            if not players:
                await message.channel.send("⚠️ Ese usuario no tiene estadísticas registradas.")
                return
        else:
            players = sorted(
                stats.items(),
                key=lambda kv: kv[1].get("criticos", 0),
                reverse=True,
            )

        def dado_sort_key(k: str) -> int:
            try:
                return int(k[1:])
            except ValueError:
                return 0

        for uid, data in players:
            name_display = f"<@{uid}>" if uid.isdigit() else data.get("name", uid)

            embed = discord.Embed(
                title=f"📊 Estadísticas de {data.get('name', uid)}",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Global",
                value=(
                    f"🎯 Críticos: **{data.get('criticos', 0)}**\n"
                    f"💀 Pifias: **{data.get('pifias', 0)}**\n"
                    f"🎲 Tiradas totales: **{data.get('tiradas', 0)}**"
                ),
                inline=False,
            )

            dados: dict = data.get("dados", {})
            if dados:
                lines_dado = []
                for dado, val in sorted(dados.items(), key=lambda x: dado_sort_key(x[0])):
                    if isinstance(val, dict):
                        t = val.get("tiradas", 0)
                        c = val.get("criticos", 0)
                        p = val.get("pifias", 0)
                    else:
                        t, c, p = val, 0, 0
                    if t > 0:
                        lines_dado.append(f"**{dado}**: {t} tiradas | 🎯 {c} | 💀 {p}")
                if lines_dado:
                    embed.add_field(
                        name="Desglose por dado",
                        value="\n".join(lines_dado),
                        inline=False,
                    )

            if uid.isdigit():
                embed.set_footer(text=f"ID: {uid}")

            await message.channel.send(content=name_display, embed=embed)


    async def cmd_set(self, message: discord.Message, stats: dict):
        # Campos globales
        CAMPOS_GLOBALES = {"criticos", "pifias", "tiradas"}
        CAMPO_LABEL = {"criticos": "Críticos", "pifias": "Pifias", "tiradas": "Tiradas"}
        # Campos por dado: patrón d<N>_(criticos|pifias|tiradas)
        DADO_SUB_PATTERN = re.compile(r"^(d\d+)_(criticos|pifias|tiradas)$")

        if not message.guild:
            await message.channel.send("❌ Este comando solo puede usarse en un servidor.")
            return

        member_author = message.guild.get_member(message.author.id)
        if not member_author or not member_author.guild_permissions.administrator:
            await message.channel.send("🚫 No tienes permiso para esto.")
            return

        parts = message.content.strip().split()
        if len(parts) != 4:
            await message.channel.send(
                "❌ Formato incorrecto.\n"
                "  Global: `!set @usuario criticos/pifias/tiradas número`\n"
                "  Por dado: `!set @usuario d20_criticos/d20_pifias/d20_tiradas número`"
            )
            return

        _, mention_raw, campo, valor_raw = parts
        campo = campo.lower()

        try:
            valor = int(valor_raw)
            if valor < 0:
                raise ValueError
        except ValueError:
            await message.channel.send("❌ El número debe ser un entero positivo.")
            return

        mention_match = MENTION_IN_EMBED_PATTERN.search(mention_raw)
        if not mention_match:
            await message.channel.send("❌ Debes mencionar a un usuario con @.")
            return

        uid = mention_match.group(1)
        target_member = message.guild.get_member(int(uid))
        display_name = target_member.display_name if target_member else f"Usuario {uid}"

        if uid not in stats:
            stats[uid] = {"name": display_name, "criticos": 0, "pifias": 0, "tiradas": 0, "dados": {}}
        else:
            stats[uid].setdefault("dados", {})
            stats[uid]["name"] = display_name

        # Campo global
        if campo in CAMPOS_GLOBALES:
            stats[uid][campo] = valor
            save_stats(stats)
            await message.channel.send(
                f"✅ **{CAMPO_LABEL[campo]}** de <@{uid}> actualizados a `{valor}`."
            )
            return

        # Campo por dado (d20_criticos, d10_pifias, etc.)
        dado_match = DADO_SUB_PATTERN.match(campo)
        if dado_match:
            dado_key = dado_match.group(1)
            sub_campo = dado_match.group(2)
            dado_sub = get_dado_sub(stats[uid]["dados"], dado_key)
            dado_sub[sub_campo] = valor
            # Recalcular totales globales desde los dados
            stats[uid]["tiradas"] = sum(
                (v.get("tiradas", 0) if isinstance(v, dict) else v)
                for v in stats[uid]["dados"].values()
            )
            stats[uid]["criticos"] = sum(
                v.get("criticos", 0) for v in stats[uid]["dados"].values() if isinstance(v, dict)
            )
            stats[uid]["pifias"] = sum(
                v.get("pifias", 0) for v in stats[uid]["dados"].values() if isinstance(v, dict)
            )
            save_stats(stats)
            await message.channel.send(
                f"✅ **{sub_campo}** de {dado_key} para <@{uid}> actualizado a `{valor}`."
            )
            return

        await message.channel.send(
            f"❌ Campo inválido: `{campo}`.\n"
            "  Campos globales: `criticos`, `pifias`, `tiradas`.\n"
            "  Campos por dado: `d20_criticos`, `d20_pifias`, `d20_tiradas`, etc."
        )


    async def cmd_remove(self, message: discord.Message, stats: dict):
        if not message.guild:
            await message.channel.send("❌ Este comando solo puede usarse en un servidor.")
            return

        member_author = message.guild.get_member(message.author.id)
        if not member_author or not member_author.guild_permissions.administrator:
            await message.channel.send("🚫 No tienes permiso para esto.")
            return

        parts = message.content.strip().split(maxsplit=1)
        target_name = parts[1].strip() if len(parts) == 2 else ""

        removed: list[str] = []

        # 1. Limpieza automática: eliminar entradas sin ID numérico
        non_numeric = [k for k in list(stats.keys()) if not k.isdigit()]
        for key in non_numeric:
            entry_name = stats[key].get("name", key)
            del stats[key]
            removed.append(entry_name)

        # 2. Si se indicó un nombre, buscar y borrar esa entrada concreta
        if target_name:
            target_lower = target_name.lower()
            keys_to_delete = [
                k for k, v in stats.items()
                if v.get("name", "").lower() == target_lower or k.lower() == target_lower
            ]
            for key in keys_to_delete:
                entry_name = stats[key].get("name", key)
                del stats[key]
                if entry_name not in removed:
                    removed.append(entry_name)

        if removed:
            save_stats(stats)
            lines = [f"🗑️ Entrada **{name}** eliminada del marcador." for name in removed]
            await message.channel.send("\n".join(lines))
        elif target_name:
            await message.channel.send(
                f"⚠️ No se encontró ninguna entrada con el nombre `{target_name}`."
            )
        else:
            await message.channel.send("✅ El marcador ya estaba limpio.")


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("La variable de entorno DISCORD_TOKEN no está configurada.")
    keep_alive()
    bot = DiceBot()
    bot.run(token)


if __name__ == "__main__":
    main()
