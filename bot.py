import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Dicionários para armazenar dados
authorized_roles = {}  # guild_id : [role_ids]
polls = {}  # guild_id : { "titulo": str, "canal": discord.TextChannel, "options": {option_text: votos}, "message_id": int, "end_time": datetime }

# -------------------- FUNÇÃO AUXILIAR --------------------
def is_authorized(interaction: discord.Interaction):
    guild_roles = authorized_roles.get(interaction.guild.id, [])
    for role in interaction.user.roles:
        if role.id in guild_roles:
            return True
    return False

# -------------------- COMANDOS --------------------

# /role - Define cargos que podem criar/editar votação
@bot.tree.command(name="role", description="Define quais cargos podem gerenciar votações")
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

# /new - Criar votação
@bot.tree.command(name="new", description="Cria uma nova votação")
@app_commands.describe(titulo="Título da votação", canal="Canal onde será publicada", tempo="Duração em horas (opcional)")
async def new(interaction: discord.Interaction, titulo: str, canal: discord.TextChannel, tempo: int = None):
    if not is_authorized(interaction):
        await interaction.response.send_message("Você não tem permissão para criar votações.", ephemeral=True)
        return

    # Criar embed e botão inicial vazio
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

# /add - Adicionar opção
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

    # Adicionar opção
    if opcao in guild_poll["options"]:
        await interaction.response.send_message("Essa opção já existe.", ephemeral=True)
        return

    guild_poll["options"][opcao] = 0

    # Criar botão
    class VoteButton(discord.ui.Button):
        def __init__(self, label):
            super().__init__(label=label, style=discord.ButtonStyle.primary)
        
        async def callback(self, button_interaction: discord.Interaction):
            guild_poll["options"][label] += 1
            await update_poll_message(interaction.guild.id)
            await button_interaction.response.send_message(f"Você votou em {label}!", ephemeral=True)

    button = VoteButton(opcao)
    guild_poll["view"].add_item(button)
    await update_poll_message(interaction.guild.id)
    await interaction.response.send_message(f"Opção '{opcao}' adicionada.", ephemeral=True)

# /edit - Editar opção ou título
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

# /close - Finalizar votação
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

    # Fechar imediatamente
    await finalize_poll(interaction.guild.id)
    await interaction.response.send_message("Votação finalizada!", ephemeral=True)

# -------------------- FUNÇÕES AUXILIARES --------------------
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

# -------------------- TASK DE VERIFICAÇÃO --------------------
@tasks.loop(seconds=60)
async def check_scheduled_polls():
    now = datetime.utcnow()
    for guild_id, poll in polls.items():
        if poll.get("end_time") and poll["end_time"] <= now:
            await finalize_poll(guild_id)

check_scheduled_polls.start()

# -------------------- BOT START --------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logado como {bot.user}")

import os
bot.run(os.environ['DISCORD_TOKEN'])
