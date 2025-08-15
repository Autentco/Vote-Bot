import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
import random
import os

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # necessário para sorteio

bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------- DADOS --------------------
authorized_roles = {}  # guild_id : [role_ids]
polls = {}  # guild_id : { titulo, canal, options, message_id, view, end_time }
sorteios = {}  # guild_id : { canal, participantes, numero, message_id, end_time }

# -------------------- FUNÇÕES AUXILIARES --------------------
def is_authorized(interaction: discord.Interaction):
    guild_roles = authorized_roles.get(interaction.guild.id, [])
    for role in interaction.user.roles:
        if role.id in guild_roles:
            return True
    return False

async def update_poll_message(guild_id):
    guild_poll = polls[guild_id]
    canal = guild_poll["canal"]
    msg = await canal.fetch_message(guild_poll["message_id"])
    description = "\n".join([f"{opt} — {v} votos" for opt,v in guild_poll["options"].items()])
    embed = discord.Embed(title=guild_poll["titulo"], description=description, color=discord.Color.blue())
    await msg.edit(embed=embed, view=guild_poll["view"])

async def finalize_poll(guild_id):
    guild_poll = polls[guild_id]
    canal = guild_poll["canal"]
    msg = await canal.fetch_message(guild_poll["message_id"])
    description = "\n".join([f"{opt} — {v} votos" for opt,v in guild_poll["options"].items()])
    embed = discord.Embed(title=f"{guild_poll['titulo']} (Finalizada)", description=description, color=discord.Color.green())
    await msg.edit(embed=embed, view=None)
    guild_poll["end_time"] = None

async def finalize_sorteio(guild_id):
    sorteio = sorteios.get(guild_id)
    if not sorteio:
        return
    canal = sorteio["canal"]
    msg = await canal.fetch_message(sorteio["message_id"])
    participantes = sorteio["participantes"]
    numero = sorteio["numero"]
    if len(participantes) == 0:
        winners = "Nenhum participante"
    else:
        winners = random.sample(participantes, min(numero, len(participantes)))
        winners = ", ".join([w.name for w in winners])
    embed = discord.Embed(title="Sorteio Finalizado", description=f"Ganhadores: {winners}", color=discord.Color.green())
    await msg.edit(embed=embed, view=None)
    sorteio["end_time"] = None

# -------------------- TASKS --------------------
@tasks.loop(seconds=60)
async def check_scheduled_events():
    now = datetime.utcnow()
    for guild_id, poll in polls.items():
        if poll.get("end_time") and poll["end_time"] <= now:
            await finalize_poll(guild_id)
    for guild_id, sorteio in sorteios.items():
        if sorteio.get("end_time") and sorteio["end_time"] <= now:
            await finalize_sorteio(guild_id)

check_scheduled_events.start()

# -------------------- COMANDOS --------------------

# /ping
@bot.tree.command(name="ping", description="Verifica se o bot está online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🟢", ephemeral=True)

# /chat
@bot.tree.command(name="chat", description="Mostra o canal onde a votação está ativa")
async def chat(interaction: discord.Interaction):
    guild_poll = polls.get(interaction.guild.id)
    if not guild_poll:
        await interaction.response.send_message("Não há votação ativa.", ephemeral=True)
    else:
        await interaction.response.send_message(f"A votação está ativa em {guild_poll['canal'].mention}", ephemeral=True)

# /role
@bot.tree.command(name="role", description="Define cargos que podem gerenciar votações")
@app_commands.describe(role="Cargo autorizado")
async def role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
        return
    guild_roles = authorized_roles.get(interaction.guild.id, [])
    if role.id not in guild_roles:
        guild_roles.append(role.id)
        authorized_roles[interaction.guild.id] = guild_roles
        await interaction.response.send_message(f"Cargo {role.name} agora pode criar/editar votações.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Cargo {role.name} já está autorizado.", ephemeral=True)

# /new
@bot.tree.command(name="new", description="Cria uma nova votação")
@app_commands.describe(titulo="Título da votação", canal="Canal onde será publicada", tempo="Duração em horas (opcional)")
async def new(interaction: discord.Interaction, titulo: str, canal: discord.TextChannel, tempo: int = None):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para criar votações.", ephemeral=True)
        return
    embed = discord.Embed(title=titulo, description="Sem opções ainda", color=discord.Color.blue())
    view = discord.ui.View()
    msg = await canal.send(embed=embed, view=view)
    end_time = None
    if tempo:
        end_time = datetime.utcnow() + timedelta(hours=tempo)
    polls[interaction.guild.id] = {
        "titulo": titulo,
        "canal": canal,
        "options": {},
        "message_id": msg.id,
        "view": view,
        "end_time": end_time
    }
    await interaction.response.send_message(f"Votação '{titulo}' criada em {canal.mention}.", ephemeral=True)

# /add
@bot.tree.command(name="add", description="Adiciona opção à votação")
@app_commands.describe(opcao="Texto da opção")
async def add(interaction: discord.Interaction, opcao: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para editar votações.", ephemeral=True)
        return
    guild_poll = polls.get(interaction.guild.id)
    if not guild_poll:
        await interaction.response.send_message("Não há votação ativa.", ephemeral=True)
        return
    if opcao in guild_poll["options"]:
        await interaction.response.send_message("Essa opção já existe.", ephemeral=True)
        return
    guild_poll["options"][opcao] = 0

    class VoteButton(discord.ui.Button):
        def __init__(self, label):
            super().__init__(label=label, style=discord.ButtonStyle.primary)
            self.label_name = label
        async def callback(self, button_interaction: discord.Interaction):
            guild_poll["options"][self.label_name] += 1
            await update_poll_message(interaction.guild.id)
            await button_interaction.response.send_message(f"Você votou em {self.label_name}!", ephemeral=True)

    button = VoteButton(opcao)
    guild_poll["view"].add_item(button)
    await update_poll_message(interaction.guild.id)
    await interaction.response.send_message(f"Opção '{opcao}' adicionada.", ephemeral=True)

# /edit
@bot.tree.command(name="edit", description="Edita o título ou uma opção")
@app_commands.describe(opcao_num="Número da opção (1,2,...) ou 0 para título", novo_texto="Novo texto")
async def edit(interaction: discord.Interaction, opcao_num: int, novo_texto: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para editar votações.", ephemeral=True)
        return
    guild_poll = polls.get(interaction.guild.id)
    if not guild_poll:
        await interaction.response.send_message("Não há votação ativa.", ephemeral=True)
        return
    if opcao_num == 0:
        guild_poll["titulo"] = novo_texto
    else:
        try:
            key = list(guild_poll["options"].keys())[opcao_num - 1]
            guild_poll["options"][novo_texto] = guild_poll["options"].pop(key)
        except IndexError:
            await interaction.response.send_message("Opção não encontrada.", ephemeral=True)
            return
    await update_poll_message(interaction.guild.id)
    await interaction.response.send_message("Votação atualizada.", ephemeral=True)

# /close
@bot.tree.command(name="close", description="Finaliza a votação")
@app_commands.describe(agendar="Data e hora no formato YYYY-MM-DD HH:MM (UTC) para finalizar automaticamente")
async def close(interaction: discord.Interaction, agendar: str = None):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para fechar votações.", ephemeral=True)
        return
    guild_poll = polls.get(interaction.guild.id)
    if not guild_poll:
        await interaction.response.send_message("Não há votação ativa.", ephemeral=True)
        return
    if agendar:
        try:
            dt = datetime.strptime(agendar, "%Y-%m-%d %H:%M")
            guild_poll["end_time"] = dt
            await interaction.response.send_message(f"Votação agendada para fechar em {dt} UTC.", ephemeral=True)
        except:
            await interaction.response.send_message("Formato inválido. Use YYYY-MM-DD HH:MM", ephemeral=True)
        return
    await finalize_poll(interaction.guild.id)
    await interaction.response.send_message("Votação finalizada!", ephemeral=True)

# /sorteio
@bot.tree.command(name="sorteio", description="Inicia um sorteio")
@app_commands.describe(numero="Número de pessoas sorteadas", dia="Data final do sorteio no formato YYYY-MM-DD HH:MM (UTC)")
async def sorteio(interaction: discord.Interaction, numero: int, dia: str = None):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para criar sorteios.", ephemeral=True)
        return
    canal = interaction.channel
    participantes = [m for m in canal.members if not m.bot]
    end_time = None
    if dia:
        try:
            end_time = datetime.strptime(dia, "%Y-%m-%d %H:%M")
        except:
            await interaction.response.send_message("Formato de data inválido. Use YYYY-MM-DD HH:MM", ephemeral=True)
            return
    embed = discord.Embed(title="Sorteio Ativo", description=f"Número de ganhadores: {numero}\nParticipantes: {len(participantes)}", color=discord.Color.purple())
    msg = await canal.send(embed=embed)
    sorteios[interaction.guild.id] = {
        "canal": canal,
        "participantes": participantes,
        "numero": numero,
        "message_id": msg.id,
        "end_time": end_time
    }
    await interaction.response.send_message("Sorteio iniciado!", ephemeral=True)

# /closesorteio
@bot.tree.command(name="closesorteio", description="Finaliza o sorteio")
async def closesorteio(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para fechar sorteios.", ephemeral=True)
        return
    await finalize_sorteio(interaction.guild.id)
    await interaction.response.send_message("Sorteio finalizado!", ephemeral=True)

# -------------------- BOT START --------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logado como {bot.user}")

# Rodar no Railway
bot.run(os.environ['DISCORD_TOKEN'])
