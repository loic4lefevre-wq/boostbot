import discord
from discord.ext import commands, tasks
import re
import json
import os
from datetime import time, datetime
from zoneinfo import ZoneInfo

# ==========================
# CONFIG
# ==========================

# ⚠️ MET TON TOKEN ICI ⚠️
import os
TOKEN = os.getenv("DISCORD_TOKEN")

# IDs de salons
BOOST_VINTED_CHANNEL_ID = 1443544605122625608   # #boost-vinted
BOOST_CLEMZ_CHANNEL_ID = 1443544650056073236    # #boost-clemz
VENTES_CHANNEL_ID = 1443585993797009581         # #ventes (photos => bonus)
GENERAL_CHANNEL_ID = 1443540859961212960        # #général (privé admin)

DATA_FILE = "bot_data.json"

VINTED_REGEX = re.compile(r"https?://(www\.)?vinted\.(fr|com)[^\s]*", re.IGNORECASE)
PARIS_TZ = ZoneInfo("Europe/Paris")
HEART_EMOJI = "❤️"

# ==========================
# DONNÉES PERSISTANTES
# ==========================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "participations": {},   # user_id -> int
            "bonus": {},           # user_id -> int (stock de bonus)
            "photo_counter": {},   # user_id -> int (reste modulo 2)
            "photo_hashes": {},    # user_id -> list[str]
            "warnings": {}         # user_id -> int (avertissements semaine)
        }
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()


def add_participation(user_id: int, points: int = 1):
    uid = str(user_id)
    data["participations"][uid] = data["participations"].get(uid, 0) + points
    save_data()


def add_bonus(user_id: int, points: int = 1):
    uid = str(user_id)
    data["bonus"][uid] = data["bonus"].get(uid, 0) + points
    save_data()


def get_bonus(user_id: int) -> int:
    return data["bonus"].get(str(user_id), 0)


def consume_bonus(user_id: int, points: int = 1) -> int:
    """Consomme jusqu'à `points` bonus, retourne réellement consommé."""
    uid = str(user_id)
    current = data["bonus"].get(uid, 0)
    used = min(current, points)
    if used > 0:
        data["bonus"][uid] = current - used
        save_data()
    return used


def add_warning(user_id: int, reason: str, general_channel: discord.TextChannel | None = None):
    uid = str(user_id)
    data["warnings"][uid] = data["warnings"].get(uid, 0) + 1
    save_data()
    if general_channel is not None:
        mention = f"<@{user_id}>"
        count = data["warnings"][uid]
        msg = (
            f"⚠️ **Avertissement pour {mention}**\n"
            f"Raison : {reason}\n"
            f"Avertissements cette semaine : **{count}**"
        )
        if count >= 3:
            msg += "\n🚫 Ce membre a atteint **3 avertissements** cette semaine. " \
                   "Tu peux décider de le bannir manuellement si besoin."
        return general_channel.send(msg)


def get_warnings(user_id: int) -> int:
    return data["warnings"].get(str(user_id), 0)


def remove_warning(user_id: int, count: int = 1):
    uid = str(user_id)
    current = data["warnings"].get(uid, 0)
    data["warnings"][uid] = max(0, current - count)
    save_data()


def get_user_photo_counter(user_id: int) -> int:
    return data["photo_counter"].get(str(user_id), 0)


def set_user_photo_counter(user_id: int, value: int):
    data["photo_counter"][str(user_id)] = value
    save_data()


def add_user_photo_hash(user_id: int, photo_hash: str) -> bool:
    """Retourne True si nouvelle photo (pas doublon), False sinon."""
    uid = str(user_id)
    if uid not in data["photo_hashes"]:
        data["photo_hashes"][uid] = []
    if photo_hash not in data["photo_hashes"][uid]:
        data["photo_hashes"][uid].append(photo_hash)
        save_data()
        return True
    return False


def reset_week():
    data["participations"] = {}
    data["bonus"] = {}
    data["photo_counter"] = {}
    data["photo_hashes"] = {}
    data["warnings"] = {}
    save_data()


def get_top_10():
    items = [(int(uid), score) for uid, score in data["participations"].items()]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:10]


# ==========================
# SUIVI DES SESSIONS
# ==========================

# type_session possible :
# "1lien2bonus", "10favs", "5favs", "repas", "clemz"

current_vinted_session = {
    "active": False,
    "type": None,
    "participants": set(),        # users ayant posté un lien de participation
    "links_count": {},            # user_id -> nb liens de participation
    "bonus_links_count": {},      # user_id -> nb liens contenant "bonus"
    "validated": set()            # users ayant mis un ❤️ dans #boost-vinted
}

current_clemz_session = {
    "active": False,
    "participants": set(),
    "validated": set()
}


def start_vinted_session(session_type: str):
    current_vinted_session["active"] = True
    current_vinted_session["type"] = session_type
    current_vinted_session["participants"].clear()
    current_vinted_session["links_count"].clear()
    current_vinted_session["bonus_links_count"].clear()
    current_vinted_session["validated"].clear()


async def end_vinted_session_recap(channel: discord.TextChannel, general_channel: discord.TextChannel | None):
    if not current_vinted_session["active"]:
        return
    participants = current_vinted_session["participants"].copy()
    validated = current_vinted_session["validated"].copy()
    links_count = current_vinted_session["links_count"]

    total_participations = sum(links_count.values()) if links_count else 0

    if not participants:
        await channel.send("📊 Fin de session : aucun lien posté sur cette session.")
    else:
        # règle : si une seule personne a participé, pas besoin de ❤️
        if len(participants) == 1:
            ok = participants
            not_ok = set()
        else:
            ok = participants & validated
            not_ok = participants - validated

        lines = [f"📈 **Participations (liens) sur cette session : {total_participations}**"]

        if ok:
            ok_list = ", ".join(f"<@{uid}>" for uid in ok)
            lines.append(f"✅ **Session validée pour :** {ok_list}")
        else:
            lines.append("✅ **Session validée :** personne cette fois.")

        if not_ok:
            nok_list = ", ".join(f"<@{uid}>" for uid in not_ok)
            lines.append(f"⚠️ **Session NON validée pour :** {nok_list}")
            # avertissement pour chacun
            for uid in not_ok:
                if general_channel is not None:
                    await add_warning(
                        uid,
                        "Session non validée (pas de ❤️ pour confirmer les tâches)",
                        general_channel
                    )
        else:
            lines.append("✨ Tout le monde a joué le jeu, bravo !")

        await channel.send("\n".join(lines))

    # reset session
    current_vinted_session["active"] = False
    current_vinted_session["type"] = None
    current_vinted_session["participants"].clear()
    current_vinted_session["links_count"].clear()
    current_vinted_session["bonus_links_count"].clear()
    current_vinted_session["validated"].clear()


def start_clemz_session():
    current_clemz_session["active"] = True
    current_clemz_session["participants"].clear()
    current_clemz_session["validated"].clear()


async def end_clemz_session_recap(channel: discord.TextChannel, general_channel: discord.TextChannel | None):
    if not current_clemz_session["active"]:
        return
    participants = current_clemz_session["participants"].copy()
    validated = current_clemz_session["validated"].copy()

    if not participants:
        await channel.send("📊 Fin de session : aucun lien posté sur cette session.")
    else:
        if len(participants) == 1:
            ok = participants
            not_ok = set()
        else:
            ok = participants & validated
            not_ok = participants - validated

        lines = []

        if ok:
            ok_list = ", ".join(f"<@{uid}>" for uid in ok)
            lines.append(f"✅ **Session validée pour :** {ok_list}")
        else:
            lines.append("✅ **Session validée :** personne.")

        if not_ok:
            nok_list = ", ".join(f"<@{uid}>" for uid in not_ok)
            lines.append(f"⚠️ **Session NON validée pour :** {nok_list}")
            for uid in not_ok:
                if general_channel is not None:
                    await add_warning(
                        uid,
                        "Session Boost Clemz non validée (pas de ❤️)",
                        general_channel
                    )
        else:
            lines.append("✨ Tout le monde a joué le jeu, bravo !")

        await channel.send("\n".join(lines))

    current_clemz_session["active"] = False
    current_clemz_session["participants"].clear()
    current_clemz_session["validated"].clear()


# ==========================
# BOT SETUP
# ==========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    weekly_reset.start()
    session_scheduler.start()
    print("Tâches périodiques démarrées.")


# ==========================
# EVENTS MESSAGES
# ==========================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    channel_id = message.channel.id
    content = (message.content or "").lower()

    # -------- BOOST VINTED --------
    if channel_id == BOOST_VINTED_CHANNEL_ID:
        if VINTED_REGEX.search(message.content or ""):
            general_channel = bot.get_channel(GENERAL_CHANNEL_ID)

            is_bonus_link = "bonus" in content

            # session en cours ?
            session_active = current_vinted_session["active"]
            session_type = current_vinted_session["type"]

            # liens contenant "bonus" : ne comptent pas en participation
            if is_bonus_link:
                # tentative d'utilisation de bonus
                current_bonus = get_bonus(message.author.id)
                if current_bonus <= 0:
                    # avertissement : bonus utilisé alors que 0
                    if general_channel is not None:
                        await add_warning(
                            message.author.id,
                            "Utilisation d'un lien BONUS alors que le stock de bonus est à 0",
                            general_channel
                        )
                else:
                    used = consume_bonus(message.author.id, 1)
                    if session_active and session_type == "1lien2bonus":
                        d = current_vinted_session["bonus_links_count"]
                        d[message.author.id] = d.get(message.author.id, 0) + 1
                        # plus de 2 bonus sur une session 1 lien + 2 bonus => avertissement
                        if d[message.author.id] > 2 and general_channel is not None:
                            await add_warning(
                                message.author.id,
                                "Plus de 2 liens BONUS sur une session 1 lien + 2 bonus",
                                general_channel
                            )
                # même si c'est un bonus, on considère aussi que la personne a participé
                if session_active:
                    current_vinted_session["participants"].add(message.author.id)

            else:
                # lien de participation normal
                add_participation(message.author.id, 1)

                if current_vinted_session["active"]:
                    current_vinted_session["participants"].add(message.author.id)
                    if session_type == "1lien2bonus":
                        d = current_vinted_session["links_count"]
                        d[message.author.id] = d.get(message.author.id, 0) + 1
                        # plus d'un lien normal => avertissement
                        if d[message.author.id] > 1 and general_channel is not None:
                            await add_warning(
                                message.author.id,
                                "Plus d'un lien de participation sur une session 1 lien + 2 bonus",
                                general_channel
                            )

            # si session (peu importe le type), on enregistre le participant
            if current_vinted_session["active"]:
                current_vinted_session["participants"].add(message.author.id)

    # -------- BOOST CLEMZ --------
    elif channel_id == BOOST_CLEMZ_CHANNEL_ID:
        if VINTED_REGEX.search(message.content or ""):
            add_participation(message.author.id, 1)
            if current_clemz_session["active"]:
                current_clemz_session["participants"].add(message.author.id)

    # -------- VENTES (photos => bonus) --------
    elif channel_id == VENTES_CHANNEL_ID:
        # on compte TOUT type de fichier en pièce jointe
        if message.attachments:
            new_unique_photos = 0
            for att in message.attachments:
                photo_hash = f"{att.filename}-{att.size}"
                if add_user_photo_hash(message.author.id, photo_hash):
                    new_unique_photos += 1

            if new_unique_photos > 0:
                count = get_user_photo_counter(message.author.id)
                count += new_unique_photos
                bonus_to_add = count // 2
                if bonus_to_add > 0:
                    add_bonus(message.author.id, bonus_to_add)
                    count = count % 2
                set_user_photo_counter(message.author.id, count)

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji.name) != HEART_EMOJI:
        return

    channel_id = payload.channel_id
    user_id = payload.user_id

    if channel_id == BOOST_VINTED_CHANNEL_ID and current_vinted_session["active"]:
        # la personne n'a PAS besoin de mettre ❤️ sur son propre lien,
        # on considère juste qu'elle a validé la session.
        current_vinted_session["validated"].add(user_id)

    if channel_id == BOOST_CLEMZ_CHANNEL_ID and current_clemz_session["active"]:
        current_clemz_session["validated"].add(user_id)


# ==========================
# COMMANDES
# ==========================

@bot.command(name="top")
async def top(ctx: commands.Context):
    """Top 10 participations de la semaine."""
    top10 = get_top_10()
    if not top10:
        await ctx.send("Aucun point pour le moment cette semaine 😊")
        return

    lines = []
    for rank, (user_id, score) in enumerate(top10, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Utilisateur {user_id}"
        bonus_stock = get_bonus(user_id)
        warns = get_warnings(user_id)
        lines.append(
            f"{rank}. **{name}** — {score} participations | {bonus_stock} bonus | {warns} avertissements"
        )

    await ctx.send("🏆 **TOP 10 DE LA SEMAINE**\n" + "\n".join(lines))


@bot.command(name="stats")
async def stats(ctx: commands.Context, member: discord.Member | None = None):
    """Voir participations / bonus / avertissements d'une personne."""
    if member is None:
        member = ctx.author
    uid = str(member.id)
    parts = data["participations"].get(uid, 0)
    bonus_stock = data["bonus"].get(uid, 0)
    warns = data["warnings"].get(uid, 0)
    await ctx.send(
        f"📊 Statistiques pour {member.mention} :\n"
        f"- Participations : **{parts}**\n"
        f"- Bonus disponibles : **{bonus_stock}**\n"
        f"- Avertissements cette semaine : **{warns}**"
    )


def is_admin():
    async def predicate(ctx: commands.Context):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)


@bot.command(name="add")
@is_admin()
async def add_cmd(ctx: commands.Context, member: discord.Member, points: int = 1):
    """Admin : ajoute des participations."""
    if points < 0:
        await ctx.send("Utilise un nombre positif 😉")
        return
    add_participation(member.id, points)
    await ctx.send(f"✅ {points} participation(s) ajoutée(s) à {member.mention}.")


@bot.command(name="remove")
@is_admin()
async def remove_cmd(ctx: commands.Context, member: discord.Member, points: int = 1):
    """Admin : retire des participations."""
    if points < 0:
        await ctx.send("Utilise un nombre positif 😉")
        return
    uid = str(member.id)
    current = data["participations"].get(uid, 0)
    data["participations"][uid] = max(0, current - points)
    save_data()
    await ctx.send(f"✅ {points} participation(s) retirée(s) à {member.mention}.")


@bot.command(name="unwarn")
@is_admin()
async def unwarn_cmd(ctx: commands.Context, member: discord.Member, count: int = 1):
    """Admin : retire des avertissements à quelqu'un."""
    if count < 0:
        await ctx.send("Utilise un nombre positif 😉")
        return
    remove_warning(member.id, count)
    await ctx.send(f"✅ {count} avertissement(s) retiré(s) à {member.mention}.")


# ==========================
# RESET HEBDO + RAPPORT
# ==========================

@tasks.loop(time=time(hour=23, minute=59, second=0, tzinfo=PARIS_TZ))
async def weekly_reset():
    now = datetime.now(PARIS_TZ)
    # dimanche = 6
    if now.weekday() == 6:
        general = bot.get_channel(GENERAL_CHANNEL_ID)
        if general:
            # rapport avant reset
            if data["participations"]:
                lines = ["📊 **Récap hebdo avant reset**"]
                for uid, parts in data["participations"].items():
                    user_id = int(uid)
                    member = general.guild.get_member(user_id)
                    name = member.display_name if member else f"Utilisateur {user_id}"
                    bonus_stock = data["bonus"].get(uid, 0)
                    warns = data["warnings"].get(uid, 0)
                    lines.append(
                        f"- **{name}** : {parts} participations | {bonus_stock} bonus | {warns} avertissements"
                    )

                # ceux avec moins de 5 participations
                low = [
                    int(uid) for uid, parts in data["participations"].items()
                    if parts < 5
                ]
                if low:
                    low_list = ", ".join(f"<@{u}>" for u in low)
                    lines.append(
                        f"\n⚠️ **Moins de 5 sessions cette semaine :** {low_list}\n"
                        "Tu peux décider de les bannir ou non."
                    )

                await general.send("\n".join(lines))
            else:
                await general.send("📊 Récap hebdo : aucun participant cette semaine.")

            await general.send("🔄 **Reset hebdomadaire effectué** (participations, bonus, avertissements).")

        reset_week()


# ==========================
# PLANIFICATION DES SESSIONS
# ==========================

last_processed_minute = None

@tasks.loop(minutes=1)
async def session_scheduler():
    global last_processed_minute

    now = datetime.now(PARIS_TZ)
    key = (now.date().isoformat(), now.hour, now.minute)
    if key == last_processed_minute:
        return
    last_processed_minute = key

    h = now.hour
    m = now.minute

    vinted_channel = bot.get_channel(BOOST_VINTED_CHANNEL_ID)
    clemz_channel = bot.get_channel(BOOST_CLEMZ_CHANNEL_ID)
    general_channel = bot.get_channel(GENERAL_CHANNEL_ID)

    # ---------- SESSIONS CLEMz : toutes les heures ----------
    if clemz_channel:
        if m == 0:
            start_clemz_session()
            await clemz_channel.send(
                f"🚀 **Début de session Boost Clemz {h:02d}h00 - {h:02d}h59**\n"
                "Postez vos liens maintenant 💜 et n'oubliez pas de mettre un ❤️ quand vous avez tout rendu."
            )
        elif m == 59:
            await clemz_channel.send(
                "⛔ **Fin de session Boost Clemz**\n"
                "Merci de vous mettre à jour, voici le récap 👇"
            )
            await end_clemz_session_recap(clemz_channel, general_channel)

    # ---------- SESSIONS VINTED ----------
    if not vinted_channel:
        return

    # SESSIONS REPAS
    if h == 12 and m == 30:
        start_vinted_session("repas")
        await vinted_channel.send(
            "🍽️ **Session spéciale repas 12h30 - 13h30**\n"
            "1 lien + 2 bonus autorisés pendant la pause 😋\n"
            f"Pensez à mettre un {HEART_EMOJI} quand vous avez rendu tous les boosts."
        )
        return
    if h == 13 and m == 30:
        await vinted_channel.send(
            "⛔ **Fin de la session spéciale repas de midi**\n"
            "Merci de vous mettre à jour, voici le récap 👇"
        )
        await end_vinted_session_recap(vinted_channel, general_channel)
        return

    if h == 19 and m == 0:
        start_vinted_session("repas")
        await vinted_channel.send(
            "🍽️ **Session spéciale repas 19h00 - 20h00**\n"
            "1 lien + 2 bonus autorisés pendant le dîner ✨\n"
            f"N'oubliez pas le {HEART_EMOJI} quand tout est rendu."
        )
        return
    if h == 20 and m == 0:
        await vinted_channel.send(
            "⛔ **Fin de la session spéciale repas du soir**\n"
            "Merci de vous mettre à jour, voici le récap 👇"
        )
        await end_vinted_session_recap(vinted_channel, general_channel)
        return

    # SESSIONS 10 FAVS
    sessions_10favs_start = {(10, 0), (14, 0), (20, 0)}
    sessions_10favs_end = {(10, 15), (14, 15), (20, 15)}

    if (h, m) in sessions_10favs_start:
        start_vinted_session("10favs")
        await vinted_channel.send(
            "💥 **Session 10 favs (15 minutes)**\n"
            "1 lien + 2 bonus possibles, objectif **10 favs** chacune !\n"
            f"Mettez un {HEART_EMOJI} quand vous avez tout rendu."
        )
        return

    if (h, m) in sessions_10favs_end:
        await vinted_channel.send(
            "⛔ **Fin de la session 10 favs**\n"
            "Merci de vous mettre à jour, voici le récap 👇"
        )
        await end_vinted_session_recap(vinted_channel, general_channel)
        return

    # SESSIONS 5 FAVS
    sessions_5favs_start = {(7, 0), (11, 0), (15, 0), (18, 0), (21, 0)}
    sessions_5favs_end = {(7, 15), (11, 15), (15, 15), (18, 15), (21, 15)}

    if (h, m) in sessions_5favs_start:
        start_vinted_session("5favs")
        await vinted_channel.send(
            "✨ **Session 5 favs (15 minutes)**\n"
            "1 lien + 2 bonus possibles, objectif **5 favs** chacune 😉\n"
            f"Pensez au {HEART_EMOJI} quand tout est rendu."
        )
        return

    if (h, m) in sessions_5favs_end:
        await vinted_channel.send(
            "⛔ **Fin de la session 5 favs**\n"
            "Merci de vous mettre à jour, voici le récap 👇"
        )
        await end_vinted_session_recap(vinted_channel, general_channel)
        return

    # SESSIONS 1 LIEN + 2 BONUS (par défaut toutes les 15 min)
    # Début : 00, 15, 30, 45
    if m in (0, 15, 30, 45):
        start_vinted_session("1lien2bonus")
        await vinted_channel.send(
            "🕒 **Nouvelle session : 1 lien + 2 bonus (15 minutes)**\n"
            "Postez **1 lien Vinted** et jusqu'à **2 liens BONUS** maximum.\n"
            f"Quand vous avez rendu toutes les tâches, mettez un {HEART_EMOJI}."
        )
        return

    # Fin : 14, 29, 44, 59
    if m in (14, 29, 44, 59):
        await vinted_channel.send(
            "⛔ **Fin de la session 1 lien + 2 bonus**\n"
            "Merci de vous mettre à jour, voici le récap 👇"
        )
        await end_vinted_session_recap(vinted_channel, general_channel)
        return


# ==========================
# LANCEMENT
# ==========================

bot.run(TOKEN)
