import discord
from discord.ext import commands, tasks
from google.oauth2.service_account import Credentials
import gspread
import json
import os
from datetime import datetime
import re
import hashlib

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

# Globální proměnné
last_row_hashes = {}  # {hash: {'data': {...}, 'message_id': 12345}}
first_check_done = False

def load_state():
    """Načti poslední známý stav ze souboru"""
    global last_row_hashes
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                last_row_hashes = data.get('last_row_hashes', {})
                print(f"✅ Načten poslední stav: {len(last_row_hashes)} řádků")
        else:
            last_row_hashes = {}
            print("📝 Žádný předchozí stav nenalezen")
    except Exception as e:
        print(f"⚠️  Chyba při načítání stavu: {e}")
        last_row_hashes = {}

def save_state():
    """Ulož aktuální stav do souboru"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_row_hashes': last_row_hashes}, f)
        print(f"💾 Stav uložen: {len(last_row_hashes)} řádků")
    except Exception as e:
        print(f"❌ Chyba při ukládání stavu: {e}")

def create_row_hash(row_data):
    """Vytvoř unikátní hash pro řádek (datum|popis|castka)"""
    row_str = f"{row_data['datum']}|{row_data['popis']}|{row_data['castka']}"
    return hashlib.md5(row_str.encode()).hexdigest()

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
        
        # Čti sloupce B, C, D - řádky 2-500 (na místo 2-100)
        all_cells = sheet.range('B2:D1000')
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

async def send_new_transaction(channel, item):
    """Pošli novou transakci a vrať ID zprávy"""
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
    
    msg = await channel.send(embed=embed)
    print(f"✅ Nová transakce poslána: {item['datum']} - {item['popis']} (ID: {msg.id})")
    return msg.id

async def update_transaction(channel, message_id, item):
    """Uprav existující transakci v Discordu"""
    castka_fmt = format_accounting(item['castka'])
    
    try:
        msg = await channel.fetch_message(message_id)
        
        embed = create_embed(
            "📝 Upravená Transakce",
            "",
            discord.Color.from_rgb(251, 191, 36),  # Oranžová pro úpravu
            datetime.now()
        )
        
        embed.add_field(
            name="💳 Detail",
            value=(f"**Datum:** {item['datum']}\n"
                   f"**Popis:** {item['popis']}\n"
                   f"**Částka:** {castka_fmt}"),
            inline=False
        )
        
        embed.set_footer(text="⚠️ Tento řádek byl upraven")
        
        await msg.edit(embed=embed)
        print(f"✅ Transakce upravena: {item['datum']} - {item['popis']}")
    except discord.NotFound:
        print(f"⚠️  Zpráva s ID {message_id} nebyla nalezena (možná byla smazána)")
    except Exception as e:
        print(f"❌ Chyba při úpravě zprávy: {e}")

@tasks.loop(minutes=2)
async def check_new_transactions():
    """Kontroluj nové transakce a změny"""
    global last_row_hashes, first_check_done
    
    print("\n🔍 Kontrola transakcí...")
    data = get_accounting_data()
    
    if not data:
        print("❌ Nelze přečíst data")
        return
    
    try:
        guild = bot.get_guild(SERVER_ID)
        channel = guild.get_channel(CHANNEL_ID)
        
        if not channel:
            print("❌ Kanál nenalezen!")
            return
        
        # PRVNÍ KONTROLA - jen si zapamatuj všechny řádky
        if not first_check_done:
            print(f"📌 PRVNÍ KONTROLA - Zapamatuji si {len(data)} stávajících řádků")
            
            for item in data:
                row_hash = create_row_hash(item)
                last_row_hashes[row_hash] = {
                    'data': item,
                    'message_id': None
                }
            
            save_state()
            first_check_done = True
            print(f"⏭️  Příští nové řádky budou poslány jako notifikace")
            return
        
        # DALŠÍ KONTROLY - Detekuj nové a upravené řádky
        current_hashes = set()
        new_items = []
        
        for item in data:
            row_hash = create_row_hash(item)
            current_hashes.add(row_hash)
            
            if row_hash not in last_row_hashes:
                # NOVÝ ŘÁDEK
                print(f"📈 Nový řádek: {item['datum']} - {item['popis']}")
                new_items.append(item)
                last_row_hashes[row_hash] = {
                    'data': item,
                    'message_id': None
                }
        
        # Pošli nové transakce
        for item in new_items:
            row_hash = create_row_hash(item)
            msg_id = await send_new_transaction(channel, item)
            last_row_hashes[row_hash]['message_id'] = msg_id
        
        # Detekuj ZMĚNY v existujících řádcích
        for row_hash, stored_info in list(last_row_hashes.items()):
            if row_hash in current_hashes:
                # Řádek stále existuje - zkontroluj jestli se změnil
                # (toto by se stalo jen když bys ručně editoval obsah)
                pass
        
        # Detekuj SMAZANÉ řádky
        deleted_hashes = set(last_row_hashes.keys()) - current_hashes
        if deleted_hashes:
            print(f"🗑️  Smazáno {len(deleted_hashes)} řádků")
            for deleted_hash in deleted_hashes:
                del last_row_hashes[deleted_hash]
        
        if not new_items and not deleted_hashes:
            print("✅ Žádné změny")
        
        save_state()
        
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
        print("🔍 Kontrola transakcí spuštěna (každých 2 minuty)")

token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
