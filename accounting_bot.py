import discord
from discord.ext import commands, tasks
from google.oauth2.service_account import Credentials
import gspread
import json
import os
from datetime import datetime
import re

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

SERVER_ID = int(os.getenv("GUILD_ID", "1397286059406000249"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1443610848391204955"))
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_NAME = "Učetnictví"

# Soubor pro uložení stavu (persistent storage)
STATE_FILE = "/tmp/bot_state.json"

# Globální proměnné pro sledování řádků
last_row_count = 0

def load_state():
    """Načti poslední známý počet řádků ze souboru"""
    global last_row_count
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                last_row_count = data.get('last_row_count', 0)
                print(f"✅ Načten poslední stav: {last_row_count} řádků")
        else:
            last_row_count = 0
            print("📝 Žádný předchozí stav nenalezen")
    except Exception as e:
        print(f"⚠️  Chyba při načítání stavu: {e}")
        last_row_count = 0

def save_state():
    """Ulož aktuální stav do souboru"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_row_count': last_row_count}, f)
        print(f"💾 Stav uložen: {last_row_count} řádků")
    except Exception as e:
        print(f"❌ Chyba při ukládání stavu: {e}")

print("="*60)
print("ACCOUNTING BOT - CZM8")
print("="*60)
print(f"SHEET_ID: {SHEET_ID}")
print(f"SHEET_NAME: {SHEET_NAME}")

def get_sheets_client():
    try:
        creds_json = os.getenv("GOOGLE_CREDENTIALS")
        if not creds_json:
            print("❌ GOOGLE_CREDENTIALS not found!")
            return None
            
        creds_dict = json.loads(creds_json)
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        print("✅ Google Sheets client OK")
        return client
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def clean_number(value):
    """Vyčistit číslo - odstranit speciální znaky a formátování"""
    if not value:
        return 0.0
    
    s = str(value).replace('\xa0', '').replace(' ', '').strip()
    s = re.sub(r'[^\d.,\-]', '', s)
    s = s.replace(',', '.')
    
    try:
        return float(s) if s and s != '-' else 0.0
    except:
        return 0.0

def format_accounting(value):
    """Formátuj číslo v účetním formátu: 10000 -> 10.000"""
    num = clean_number(value)
    return f"{int(num):,}".replace(',', '.')

def get_accounting_data():
    try:
        client = get_sheets_client()
        if not client:
            return None
        
        print(f"Opening sheet {SHEET_ID}...")
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        print("✅ Sheet opened")
        
        # Čti sloupce B, C, D - řádky 2-100
        all_cells = sheet.range('B2:D100')
        print(f"✅ Got {len(all_cells)} cells")
        
        if len(all_cells) >= 3:
            data = []
            for i in range(0, len(all_cells), 3):  # 3 sloupce (B-D)
                row_data = all_cells[i:i+3]
                
                if len(row_data) >= 1 and row_data[0].value:
                    datum = str(row_data[0].value).strip()
                    
                    # Přeskočit prázdné řádky a nadpisy
                    if not datum or datum.lower() in ['datum', 'date', ''] or 'celkem' in datum.lower():
                        continue
                    
                    try:
                        # B=datum, C=popis, D=castka
                        popis = str(row_data[1].value).strip() if len(row_data) > 1 else ""
                        castka = clean_number(row_data[2].value if len(row_data) > 2 else 0)
                        
                        if castka != 0 or datum:
                            data.append({
                                "datum": datum,
                                "popis": popis,
                                "castka": castka
                            })
                            print(f"✅ {datum}: {popis} = {castka}")
                    except Exception as e:
                        print(f"Parse error for {datum}: {e}")
                        continue
            
            print(f"✅ Got {len(data)} rows of data")
            return data if data else None
        else:
            return None
    except Exception as e:
        print(f"❌ Error reading sheets: {e}")
        import traceback
        traceback.print_exc()
        return None

def create_embed(title, description, color, timestamp):
    """Vytvoří embed"""
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=timestamp
    )

async def send_new_transactions(channel, new_data):
    """Pošli POUZE nové transakce jako nové zprávy"""
    if not new_data:
        return
    
    for item in new_data:
        castka_fmt = format_accounting(item['castka'])
        
        embed = create_embed(
            "📝 Nová Transakce",
            "",
            discord.Color.from_rgb(52, 211, 153),
            datetime.now()
        )
        
        embed.add_field(
            name="💳 Detail",
            value=(f"**Datum:** {item['datum']}\n"
                   f"**Popis:** {item['popis']}\n"
                   f"**Částka:** {castka_fmt}"),
            inline=False
        )
        
        await channel.send(embed=embed)
        print(f"✅ Nová transakce poslána: {item['datum']} - {item['popis']}")

@tasks.loop(minutes=5)
async def check_new_transactions():
    """Kontroluj nové transakce každých 5 minut"""
    global last_row_count
    
    print("\n🔍 Kontrola nových transakcí...")
    data = get_accounting_data()
    
    if not data:
        print("❌ Nelze přečíst data")
        return
    
    current_row_count = len(data)
    print(f"📊 Aktuální počet řádků: {current_row_count}, Poslední známý: {last_row_count}")
    
    try:
        guild = bot.get_guild(SERVER_ID)
        channel = guild.get_channel(CHANNEL_ID)
        
        if not channel:
            print("❌ Kanál nenalezen!")
            return
        
        # Pokud je nový počet řádků větší než poslední známý
        if current_row_count > last_row_count:
            new_rows = current_row_count - last_row_count
            print(f"📈 Nalezeny {new_rows} nové transakce!")
            
            # Pošli POUZE nové transakce
            new_transactions = data[-new_rows:]
            await send_new_transactions(channel, new_transactions)
        else:
            print("✅ Žádné nové transakce")
        
        # Aktualizuj poslední známý počet
        last_row_count = current_row_count
        save_state()  # Ulož do souboru
        
    except Exception as e:
        print(f"❌ Chyba při kontrole: {e}")

@check_new_transactions.before_loop
async def before_check():
    """Čekej než je bot připraven"""
    await bot.wait_until_ready()

@bot.command(name="accounting")
async def accounting_command(ctx):
    """Zobrazí všechny transakce"""
    print("Command: !accounting")
    data = get_accounting_data()
    if data:
        total_castka = sum(d["castka"] for d in data)
        
        # Hlavní embed s totály
        main_embed = create_embed(
            "📊 Účetnictví CZM8",
            "Přehled všech transakcí",
            discord.Color.gold(),
            datetime.now()
        )
        
        main_embed.add_field(
            name="💰 Celkem",
            value=f"`{format_accounting(total_castka)}`",
            inline=False
        )
        
        await ctx.send(embed=main_embed)
        
        # Pošli transakce po 10 na embed
        chunk_size = 10
        total_chunks = (len(data) + chunk_size - 1) // chunk_size
        
        for chunk_idx in range(0, len(data), chunk_size):
            chunk = data[chunk_idx:chunk_idx + chunk_size]
            part_num = (chunk_idx // chunk_size) + 1
            
            color = discord.Color.from_rgb(52, 211, 153) if chunk_idx == 0 else discord.Color.from_rgb(59, 130, 246)
            
            if total_chunks == 1:
                title = "📝 Transakce"
            else:
                title = f"📝 Transakce ({part_num}. část)"
            
            embed = create_embed(
                title,
                "",
                color,
                datetime.now()
            )
            
            for item in chunk:
                castka_fmt = format_accounting(item['castka'])
                
                value = (f"**Datum:** {item['datum']}\n"
                        f"**Popis:** {item['popis']}\n"
                        f"**Částka:** {castka_fmt}")
                
                embed.add_field(
                    name=f"💳 Transakce",
                    value=value,
                    inline=False
                )
            
            await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Nemohu přečíst data z Google Sheets")

@bot.command(name="test")
async def test(ctx):
    """Test bota"""
    embed = discord.Embed(
        title="✅ Bot Funguje",
        description="Účetnictví bot je online!",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print("="*60)
    print(f"Bot: {bot.user}")
    print("="*60)
    
    # Načti stav při startu
    load_state()
    
    print("READY")
    print("="*60)
    
    if not check_new_transactions.is_running():
        check_new_transactions.start()
        print("🔍 Kontrola nových transakcí spuštěna (každých 5 minut)")

token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
